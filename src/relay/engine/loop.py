"""The agent loop.

One iteration of `drive()`:

    1. replay the event log -> current state (never trust cached state)
    2. if terminal or AWAITING_APPROVAL -> stop; nothing to do
    3. if there are pending tool calls -> process the next one
         policy: ALLOW -> execute -> ToolSucceeded/ToolFailed
                 REQUIRE_APPROVAL -> ApprovalRequired -> park, stop
                 DENY -> ToolFailed(surfaced to model)
    4. else check budgets -> maybe BudgetExceeded (terminal)
    5. else call the model -> LLMResponded (+ ToolCallRequested each call,
       or RunCompleted if it produced a final answer)
    6. append events with optimistic concurrency; on ConcurrencyError,
       someone else advanced the run (e.g. a cancel) -> re-read and repeat

Every append persists BEFORE the next side effect, so a crash between any
two steps loses nothing: recovery replays the log and continues from the
exact same decision point.
"""

from __future__ import annotations

import uuid

from relay.config import Settings
from relay.domain.budget import check_budget
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
    ToolCallRequested,
    ToolFailed,
    ToolSucceeded,
    utcnow,
)
from relay.domain.run import RunState, RunStatus, apply, replay
from relay.domain.types import RiskLevel
from relay.engine.executor import ToolExecutor
from relay.llm.base import LLMProvider, ProviderError
from relay.memory.store import MemoryEntry, MemoryStore, render_memory_block
from relay.observability import get_logger, span
from relay.observability.metrics import record_event
from relay.store.base import ConcurrencyError, EventStore
from relay.tools.base import Tool
from relay.tools.policy import PolicyDecision, PolicyEngine
from relay.tools.registry import ToolRegistry, UnknownToolError

log = get_logger(__name__)


class AgentEngine:
    def __init__(
        self,
        *,
        store: EventStore,
        provider: LLMProvider,
        registry: ToolRegistry,
        policy: PolicyEngine | None = None,
        settings: Settings | None = None,
        memory: MemoryStore | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._registry = registry
        self._policy = policy or PolicyEngine()
        self._settings = settings or Settings()
        self._memory = memory
        self._executor = executor or ToolExecutor(self._settings)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_run(
        self,
        *,
        goal: str,
        model: str | None = None,
        system_prompt: str = "",
        budget=None,
        allowed_tools: tuple[str, ...] = (),
        metadata: dict | None = None,
        run_id: str | None = None,
    ) -> str:
        """Persist RunCreated (with memory injection) and return run_id.
        Execution is driven separately - creation must be cheap and
        durable before any model spend happens."""
        run_id = run_id or uuid.uuid4().hex
        prompt = system_prompt
        if self._memory is not None:
            # Auditable memory injection: whatever lessons we retrieve are
            # baked into the RunCreated event itself.
            entries = await self._memory.search(goal, limit=3)
            prompt = system_prompt + render_memory_block(entries)

        from relay.domain.budget import Budget

        event = RunCreated(
            goal=goal,
            model=model or self._settings.model,
            system_prompt=prompt,
            budget=budget or Budget(),
            allowed_tools=allowed_tools,
            metadata=metadata or {},
        )
        await self._append(RunState(run_id=run_id), [event])
        return run_id

    async def drive(self, run_id: str) -> RunState:
        """Advance the run until it parks (approval) or terminates."""
        while True:
            state = await self._load(run_id)
            if state.status.is_terminal or state.status == RunStatus.AWAITING_APPROVAL:
                return state
            if state.status == RunStatus.PENDING:
                raise RuntimeError(f"run {run_id} driven before RunCreated")

            try:
                if state.pending_calls:
                    await self._process_next_call(state)
                else:
                    await self._advance_with_model(state)
            except ConcurrencyError:
                # Someone else appended (cancel, approval, another worker).
                # The log is the truth - re-read and let it decide.
                log.info("concurrency_retry", extra={"ctx": {"run_id": run_id}})
                continue

    async def approve(
        self, run_id: str, approval_id: str, *, approver: str, note: str = ""
    ) -> RunState:
        state = await self._load(run_id)
        pa = self._require_pending_approval(state, approval_id)
        await self._append(
            state,
            [
                ApprovalGranted(
                    approval_id=approval_id,
                    call_id=pa.call.call_id,
                    approver=approver,
                    note=note,
                )
            ],
        )
        return await self.drive(run_id)

    async def deny(
        self, run_id: str, approval_id: str, *, approver: str, note: str = ""
    ) -> RunState:
        state = await self._load(run_id)
        pa = self._require_pending_approval(state, approval_id)
        await self._append(
            state,
            [
                ApprovalDenied(
                    approval_id=approval_id,
                    call_id=pa.call.call_id,
                    approver=approver,
                    note=note,
                )
            ],
        )
        return await self.drive(run_id)

    async def cancel(self, run_id: str, *, reason: str = "user_requested") -> RunState:
        state = await self._load(run_id)
        if state.status.is_terminal:
            return state
        await self._append(state, [RunCancelled(reason=reason)])
        return await self._load(run_id)

    async def get_state(self, run_id: str) -> RunState:
        return await self._load(run_id)

    # ------------------------------------------------------------------
    # Loop internals
    # ------------------------------------------------------------------

    async def _process_next_call(self, state: RunState) -> None:
        pc = state.pending_calls[0]
        call = pc.call
        registry = self._registry.scoped(state.allowed_tools)

        try:
            tool = registry.get(call.tool_name)
        except UnknownToolError:
            await self._append(
                state,
                [
                    ToolFailed(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        error=f"unknown or not-permitted tool: {call.tool_name!r}",
                    )
                ],
            )
            return

        decision = (
            PolicyDecision.ALLOW if pc.approved else self._policy.decide(tool)
        )

        if decision == PolicyDecision.DENY:
            await self._append(
                state,
                [
                    ToolFailed(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        error=f"denied by policy (risk={tool.risk.value})",
                    )
                ],
            )
            return

        if decision == PolicyDecision.REQUIRE_APPROVAL:
            await self._append(
                state,
                [
                    ApprovalRequired(
                        approval_id=uuid.uuid4().hex,
                        call=call,
                        risk=tool.risk,
                        reason=f"policy requires approval for {tool.risk.value} tool "
                        f"{tool.name!r}",
                    )
                ],
            )
            return

        with span("tool.call", run_id=state.run_id, tool=tool.name):
            result = await self._executor.execute(tool, call.arguments)
        if result.ok:
            event: AnyEvent = ToolSucceeded(
                call_id=call.call_id,
                tool_name=tool.name,
                output=result.output,
                attempts=result.attempts,
                duration_ms=result.duration_ms,
            )
        else:
            event = ToolFailed(
                call_id=call.call_id,
                tool_name=tool.name,
                error=result.output,
                attempts=result.attempts,
            )
        await self._append(state, [event])

    async def _advance_with_model(self, state: RunState) -> None:
        violation = check_budget(
            state.budget,
            steps_used=state.step,
            tokens_used=state.tokens_used,
            cost_used_usd=state.cost_usd,
        )
        if violation is not None:
            await self._append(
                state,
                [
                    BudgetExceeded(
                        budget_kind=violation.kind,
                        limit=violation.limit,
                        used=violation.used,
                    )
                ],
            )
            return

        registry = self._registry.scoped(state.allowed_tools)
        turn = None
        last_error: ProviderError | None = None
        for attempt in range(1, self._settings.llm_max_attempts + 1):
            try:
                with span("llm.call", run_id=state.run_id, model=state.model, attempt=attempt):
                    turn = await self._provider.complete(
                        model=state.model,
                        messages=state.transcript,
                        tools=registry.tool_defs(),
                    )
                break
            except ProviderError as e:
                last_error = e
                log.warning(
                    "llm_attempt_failed",
                    extra={
                        "ctx": {
                            "run_id": state.run_id,
                            "attempt": attempt,
                            "retryable": e.retryable,
                            "error": str(e)[:500],
                        }
                    },
                )
                if not e.retryable:
                    break

        if turn is None:
            await self._append(
                state,
                [RunFailed(reason="provider_error", detail=str(last_error)[:1000])],
            )
            return

        step = state.step + 1
        events: list[AnyEvent] = [
            LLMResponded(
                step=step,
                content=turn.content,
                tool_calls=turn.tool_calls,
                usage=turn.usage,
                stop_reason=turn.stop_reason,
            )
        ]
        if turn.tool_calls:
            for call in turn.tool_calls:
                risk = (
                    registry.get(call.tool_name).risk
                    if call.tool_name in registry
                    else RiskLevel.DESTRUCTIVE  # unknown -> assume worst
                )
                events.append(ToolCallRequested(step=step, call=call, risk=risk))
        else:
            events.append(RunCompleted(final_answer=turn.content))

        await self._append(state, events)

        if not turn.tool_calls and self._memory is not None:
            await self._distill_memory(await self._load(state.run_id))

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    async def _load(self, run_id: str) -> RunState:
        return replay(run_id, await self._store.read(run_id))

    async def _append(self, state: RunState, events: list[AnyEvent]) -> RunState:
        """Project events onto state to learn the resulting status, then
        append with optimistic concurrency anchored at state.last_seq."""
        projected = state
        for i, event in enumerate(events):
            projected = apply(
                projected,
                EventRecord(
                    run_id=state.run_id,
                    seq=state.last_seq + i + 1,
                    recorded_at=utcnow(),
                    event=event,
                ),
            )
        await self._store.append(
            state.run_id,
            events,
            expected_version=state.last_seq,
            new_status=projected.status.value,
        )
        # Metrics are a consumer of the event stream: recorded only AFTER a
        # successful append, so counters can never disagree with the ledger
        # (a losing concurrent writer increments nothing).
        for event in events:
            record_event(event)
        return projected

    @staticmethod
    def _require_pending_approval(state: RunState, approval_id: str):
        pa = state.pending_approval
        if state.status != RunStatus.AWAITING_APPROVAL or pa is None:
            raise ValueError(f"run {state.run_id} is not awaiting approval")
        if pa.approval_id != approval_id:
            raise ValueError(f"unknown approval_id {approval_id!r}")
        return pa

    async def _distill_memory(self, state: RunState) -> None:
        """Deterministic distillation of a completed run into long-term
        memory. (LLM-written lessons are a documented upgrade path.)"""
        if state.status != RunStatus.COMPLETED or self._memory is None:
            return
        tools_used = sorted(
            {tc.tool_name for m in state.transcript for tc in m.tool_calls}
        )
        failures = [
            m.content[:120]
            for m in state.transcript
            if m.role == "tool" and m.content.startswith("ERROR:")
        ]
        lessons = (
            f"Solved in {state.step} steps using tools: {', '.join(tools_used) or 'none'}."
        )
        if failures:
            lessons += f" Errors hit along the way: {'; '.join(failures[:3])}"
        await self._memory.add(
            MemoryEntry(
                goal=state.goal,
                summary=state.final_answer[:300],
                lessons=lessons,
                tags=tuple(tools_used),
            )
        )


def get_tool(registry: ToolRegistry, name: str) -> Tool:
    return registry.get(name)
