"""Run aggregate: state machine + event fold.

The single most important file in the codebase. A run's current state is
ALWAYS derived by folding its event log through `apply()`. There is no
UPDATE statement anywhere that mutates a run row - the log is the truth.

State machine:

    PENDING --> RUNNING --> COMPLETED
                  |   ^         FAILED
                  |   |         CANCELLED
                  v   |         BUDGET_EXCEEDED
        AWAITING_APPROVAL

Anything that violates these transitions raises InvalidTransition - a bug
in the engine, never a user error.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from relay.domain.budget import Budget
from relay.domain.events import (
    AnyEvent,
    ApprovalDenied,
    ApprovalGranted,
    ApprovalRequired,
    BudgetExceeded,
    EventRecord,
    LLMResponded,
    RunCancelled,
    RunCompleted,
    RunCreated,
    RunFailed,
    RunResumed,
    ToolCallRequested,
    ToolFailed,
    ToolSucceeded,
)
from relay.domain.types import ChatMessage, RiskLevel, ToolCallSpec


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.BUDGET_EXCEEDED,
}


class InvalidTransition(Exception):
    """The engine tried to apply an event that is illegal in the current
    state. This is always a runtime bug (or a corrupted log), so we fail
    loudly instead of guessing."""


@dataclass(frozen=True)
class PendingCall:
    """A tool call that was requested but has no result event yet."""

    call: ToolCallSpec
    risk: RiskLevel
    approved: bool = False  # set once a human explicitly granted it


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    call: ToolCallSpec
    risk: RiskLevel
    reason: str


@dataclass(frozen=True)
class RunState:
    """Immutable snapshot derived from an event log.

    `transcript` is the provider-neutral conversation rebuilt from events;
    the engine hands it straight to the LLM adapter. Rebuilding it in the
    fold (rather than caching it elsewhere) guarantees the model always
    sees exactly what the ledger says happened.
    """

    run_id: str
    status: RunStatus = RunStatus.PENDING
    goal: str = ""
    model: str = ""
    system_prompt: str = ""
    budget: Budget = field(default_factory=Budget)
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    step: int = 0  # completed LLM calls
    tokens_used: int = 0
    cost_usd: float = 0.0

    transcript: tuple[ChatMessage, ...] = ()
    pending_calls: tuple[PendingCall, ...] = ()
    pending_approval: PendingApproval | None = None

    final_answer: str = ""
    error: str = ""
    last_seq: int = 0  # optimistic-concurrency anchor for the next append
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _require(state: RunState, event: AnyEvent, *allowed: RunStatus) -> None:
    if state.status not in allowed:
        raise InvalidTransition(
            f"run {state.run_id}: cannot apply {type(event).__name__} "
            f"in status {state.status.value}"
        )


def apply(state: RunState, record: EventRecord) -> RunState:  # noqa: C901
    """Fold one event into the state. Pure function: no I/O, no clocks."""
    ev = record.event
    base: dict[str, Any] = {"last_seq": record.seq, "updated_at": record.recorded_at}

    if isinstance(ev, RunCreated):
        _require(state, ev, RunStatus.PENDING)
        transcript: list[ChatMessage] = []
        if ev.system_prompt:
            transcript.append(ChatMessage(role="system", content=ev.system_prompt))
        transcript.append(ChatMessage(role="user", content=ev.goal))
        return replace(
            state,
            status=RunStatus.RUNNING,
            goal=ev.goal,
            model=ev.model,
            system_prompt=ev.system_prompt,
            budget=ev.budget,
            allowed_tools=ev.allowed_tools,
            metadata=dict(ev.metadata),
            transcript=tuple(transcript),
            created_at=record.recorded_at,
            **base,
        )

    if isinstance(ev, RunResumed):
        _require(state, ev, RunStatus.RUNNING)
        return replace(state, **base)

    if isinstance(ev, LLMResponded):
        _require(state, ev, RunStatus.RUNNING)
        msg = ChatMessage(role="assistant", content=ev.content, tool_calls=ev.tool_calls)
        return replace(
            state,
            step=ev.step,
            tokens_used=state.tokens_used + ev.usage.total_tokens,
            cost_usd=round(state.cost_usd + ev.usage.cost_usd, 6),
            transcript=state.transcript + (msg,),
            **base,
        )

    if isinstance(ev, ToolCallRequested):
        _require(state, ev, RunStatus.RUNNING)
        return replace(
            state,
            pending_calls=state.pending_calls + (PendingCall(call=ev.call, risk=ev.risk),),
            **base,
        )

    if isinstance(ev, ToolSucceeded):
        _require(state, ev, RunStatus.RUNNING)
        msg = ChatMessage(role="tool", content=ev.output, tool_call_id=ev.call_id)
        return replace(
            state,
            pending_calls=_without_call(state.pending_calls, ev.call_id),
            transcript=state.transcript + (msg,),
            **base,
        )

    if isinstance(ev, ToolFailed):
        _require(state, ev, RunStatus.RUNNING)
        remaining = _without_call(state.pending_calls, ev.call_id)
        if ev.fatal:
            return replace(
                state,
                status=RunStatus.FAILED,
                pending_calls=remaining,
                error=f"tool {ev.tool_name} failed: {ev.error}",
                **base,
            )
        msg = ChatMessage(
            role="tool", content=f"ERROR: {ev.error}", tool_call_id=ev.call_id
        )
        return replace(
            state, pending_calls=remaining, transcript=state.transcript + (msg,), **base
        )

    if isinstance(ev, ApprovalRequired):
        _require(state, ev, RunStatus.RUNNING)
        return replace(
            state,
            status=RunStatus.AWAITING_APPROVAL,
            pending_approval=PendingApproval(
                approval_id=ev.approval_id, call=ev.call, risk=ev.risk, reason=ev.reason
            ),
            **base,
        )

    if isinstance(ev, ApprovalGranted):
        _require(state, ev, RunStatus.AWAITING_APPROVAL)
        _check_approval_id(state, ev.approval_id)
        # Mark the pending call as human-approved so the policy engine
        # does not park it again when execution resumes.
        calls = tuple(
            replace(pc, approved=True) if pc.call.call_id == ev.call_id else pc
            for pc in state.pending_calls
        )
        return replace(
            state,
            status=RunStatus.RUNNING,
            pending_approval=None,
            pending_calls=calls,
            **base,
        )

    if isinstance(ev, ApprovalDenied):
        _require(state, ev, RunStatus.AWAITING_APPROVAL)
        _check_approval_id(state, ev.approval_id)
        msg = ChatMessage(
            role="tool",
            content=f"ERROR: tool call denied by human reviewer ({ev.approver}). "
            f"Note: {ev.note or 'no reason given'}. Choose a different approach.",
            tool_call_id=ev.call_id,
        )
        return replace(
            state,
            status=RunStatus.RUNNING,
            pending_approval=None,
            pending_calls=_without_call(state.pending_calls, ev.call_id),
            transcript=state.transcript + (msg,),
            **base,
        )

    if isinstance(ev, RunCompleted):
        _require(state, ev, RunStatus.RUNNING)
        return replace(
            state, status=RunStatus.COMPLETED, final_answer=ev.final_answer, **base
        )

    if isinstance(ev, RunFailed):
        _require(state, ev, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL)
        return replace(
            state,
            status=RunStatus.FAILED,
            error=f"{ev.reason}: {ev.detail}" if ev.detail else ev.reason,
            **base,
        )

    if isinstance(ev, RunCancelled):
        _require(state, ev, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL, RunStatus.PENDING)
        return replace(state, status=RunStatus.CANCELLED, error=ev.reason, **base)

    if isinstance(ev, BudgetExceeded):
        _require(state, ev, RunStatus.RUNNING)
        return replace(
            state,
            status=RunStatus.BUDGET_EXCEEDED,
            error=f"budget exceeded: {ev.budget_kind} (limit={ev.limit}, used={ev.used})",
            **base,
        )

    raise InvalidTransition(f"unknown event type: {type(ev).__name__}")


def replay(run_id: str, records: Iterable[EventRecord]) -> RunState:
    """Rebuild state from scratch. This IS the read model - used by the
    engine on every resume and by the API on every GET."""
    state = RunState(run_id=run_id)
    for record in records:
        state = apply(state, record)
    return state


def _without_call(calls: tuple[PendingCall, ...], call_id: str) -> tuple[PendingCall, ...]:
    return tuple(pc for pc in calls if pc.call.call_id != call_id)


def _check_approval_id(state: RunState, approval_id: str) -> None:
    if state.pending_approval is None or state.pending_approval.approval_id != approval_id:
        raise InvalidTransition(
            f"run {state.run_id}: approval decision references approval_id "
            f"{approval_id!r} but pending approval is "
            f"{state.pending_approval.approval_id if state.pending_approval else None!r}"
        )
