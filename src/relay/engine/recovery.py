"""Crash recovery.

Under an exclusive-deployment guarantee, the recovery scanner finds every run
whose projection says RUNNING and resumes it. Without a lease, callers must
prove the prior worker is dead; scanning from an overlapping process is unsafe.

The subtle case is ToolExecutionStarted with no result event. The crash may
have happened before, during, or after the tool's side effect: the ledger
cannot know. Relay resolves the ambiguity by tool contract:

    idempotent tool      -> safe to re-execute; just resume the loop.
    non-idempotent tool  -> re-executing might, say, send an email twice.
                            Escalate to a human via ApprovalRequired with
                            the recovery context in the reason.

This "exactly-once is a lie; choose idempotency or human judgment" framing
is the honest answer to the classic distributed-systems interview question.
"""

from __future__ import annotations

import uuid

from relay.domain.events import ApprovalRequired, RunResumed
from relay.domain.run import RunStatus
from relay.engine.loop import AgentEngine
from relay.observability import get_logger
from relay.store.base import EventStore
from relay.tools.registry import ToolRegistry, UnknownToolError

log = get_logger(__name__)


async def recover_interrupted_runs(
    *,
    store: EventStore,
    engine: AgentEngine,
    registry: ToolRegistry,
    exclusive: bool,
) -> list[str]:
    """Resume RUNNING runs only after exclusive ownership is guaranteed."""
    if not exclusive:
        raise RuntimeError(
            "recovery requires exclusive deployment ownership; a prior worker "
            "may still be executing a claimed side effect"
        )
    interrupted = await store.list_runs(status=RunStatus.RUNNING.value)
    recovered: list[str] = []
    for run_id in interrupted:
        try:
            await _recover_one(run_id, store=store, engine=engine, registry=registry)
            recovered.append(run_id)
        except Exception:  # noqa: BLE001 - one bad run must not block the rest
            log.exception("recovery_failed", extra={"ctx": {"run_id": run_id}})
    return recovered


async def _recover_one(
    run_id: str, *, store: EventStore, engine: AgentEngine, registry: ToolRegistry
) -> None:
    state = await engine.get_state(run_id)
    if state.status != RunStatus.RUNNING:
        return  # raced with a live worker or a cancel; the ledger won

    events: list = [RunResumed(recovered_after_seq=state.last_seq)]

    # A persisted execution claim without a result is ambiguous. A request
    # that was never claimed is safe for the normal loop to process.
    if state.pending_calls:
        if any(pc.execution_started for pc in state.pending_calls[1:]):
            raise RuntimeError(
                f"run {run_id}: multiple/out-of-order execution claims violate "
                "sequential tool processing"
            )
        pc = state.pending_calls[0]
        try:
            tool = registry.scoped(state.allowed_tools).get(pc.call.tool_name)
            idempotent = tool.idempotent
        except UnknownToolError:
            idempotent = False
        if pc.execution_started and not idempotent:
            events.append(
                ApprovalRequired(
                    approval_id=uuid.uuid4().hex,
                    call=pc.call,
                    risk=pc.risk,
                    reason=(
                        "crash recovery: this non-idempotent call was claimed "
                        "before the crash and may or may not have executed. "
                        "Approve to run it (again), deny to skip it."
                    ),
                )
            )

    await engine._append(state, events)  # noqa: SLF001 - engine owns append semantics
    log.info(
        "run_recovered",
        extra={"ctx": {"run_id": run_id, "escalated": len(events) > 1}},
    )
    await engine.drive(run_id)
