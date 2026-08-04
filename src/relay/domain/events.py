"""The event vocabulary of a run.

Every meaningful thing that happens to a run is one of these immutable
events, appended to the run's log. State is never written directly - it is
always derived by folding this log (see run.py).

Design rules:
  * Events are facts in past tense. They record what happened, not what
    should happen next.
  * Events are append-only and immutable (frozen pydantic models).
  * Adding a new event type is backward compatible; changing or removing
    fields on an existing type is a breaking schema migration - see
    ADR-0001 for the versioning strategy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from relay.domain.budget import Budget
from relay.domain.types import RiskLevel, ToolCallSpec, Usage


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


class RunCreated(_Event):
    type: Literal["run_created"] = "run_created"
    goal: str
    model: str
    system_prompt: str = ""
    budget: Budget = Field(default_factory=Budget)
    allowed_tools: tuple[str, ...] = ()  # empty = all registered tools
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResumed(_Event):
    """Emitted by crash recovery when a run is picked up after a worker died."""

    type: Literal["run_resumed"] = "run_resumed"
    reason: str = "crash_recovery"
    recovered_after_seq: int


class RunCompleted(_Event):
    type: Literal["run_completed"] = "run_completed"
    final_answer: str


class RunFailed(_Event):
    type: Literal["run_failed"] = "run_failed"
    reason: str
    detail: str = ""


class RunCancelled(_Event):
    type: Literal["run_cancelled"] = "run_cancelled"
    reason: str = "user_requested"


class BudgetExceeded(_Event):
    """Terminal: the runtime circuit breaker tripped."""

    type: Literal["budget_exceeded"] = "budget_exceeded"
    budget_kind: str  # "steps" | "tokens" | "cost"
    limit: float
    used: float


# --------------------------------------------------------------------------
# Model interaction
# --------------------------------------------------------------------------


class LLMResponded(_Event):
    """One completed model call: text and/or requested tool calls."""

    type: Literal["llm_responded"] = "llm_responded"
    step: int
    content: str = ""
    tool_calls: tuple[ToolCallSpec, ...] = ()
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = ""


# --------------------------------------------------------------------------
# Tool execution
# --------------------------------------------------------------------------


class ToolCallRequested(_Event):
    """The model asked for this tool. Recorded BEFORE execution so the
    ledger always shows intent - crucial for crash recovery: a requested
    call with no matching result event may or may not have side-effected."""

    type: Literal["tool_call_requested"] = "tool_call_requested"
    step: int
    call: ToolCallSpec
    risk: RiskLevel


class ToolSucceeded(_Event):
    type: Literal["tool_succeeded"] = "tool_succeeded"
    call_id: str
    tool_name: str
    output: str
    attempts: int = 1
    duration_ms: int = 0


class ToolFailed(_Event):
    """A tool ultimately failed after retries.

    fatal=False -> the error text is surfaced to the model as a tool
    result and the loop continues (the model may pick another approach).
    fatal=True  -> the run transitions to FAILED.
    """

    type: Literal["tool_failed"] = "tool_failed"
    call_id: str
    tool_name: str
    error: str
    attempts: int = 1
    fatal: bool = False


# --------------------------------------------------------------------------
# Human-in-the-loop
# --------------------------------------------------------------------------


class ApprovalRequired(_Event):
    """Run parks in AWAITING_APPROVAL. Durable state means this can wait
    minutes or weeks - no process needs to stay alive holding it."""

    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    call: ToolCallSpec
    risk: RiskLevel
    reason: str


class ApprovalGranted(_Event):
    type: Literal["approval_granted"] = "approval_granted"
    approval_id: str
    call_id: str
    approver: str
    note: str = ""


class ApprovalDenied(_Event):
    """Denial is surfaced to the model as a tool error so it can adapt;
    it does not fail the run."""

    type: Literal["approval_denied"] = "approval_denied"
    approval_id: str
    call_id: str
    approver: str
    note: str = ""


AnyEvent = Annotated[
    RunCreated
    | RunResumed
    | RunCompleted
    | RunFailed
    | RunCancelled
    | BudgetExceeded
    | LLMResponded
    | ToolCallRequested
    | ToolSucceeded
    | ToolFailed
    | ApprovalRequired
    | ApprovalGranted
    | ApprovalDenied,
    Field(discriminator="type"),
]

event_adapter: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


class EventRecord(BaseModel):
    """An event as persisted: the envelope adds identity and ordering.

    seq is a per-run, gapless, monotonically increasing sequence assigned
    by the store under optimistic concurrency (see store/base.py). It is
    the backbone of both replay and conflict detection.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    seq: int
    recorded_at: datetime
    event: AnyEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
