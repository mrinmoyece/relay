"""RunManager: owns background execution of runs inside one process.

The API handler must return 202 immediately; the agent loop runs as an
asyncio task. The manager tracks those tasks so that:
  * the same run is never driven by two tasks in this process
    (cross-process double-drive is prevented by optimistic concurrency)
  * shutdown can cancel cleanly - safe precisely because every completed
    step is already durable; whatever was in flight is picked up by crash
    recovery on next startup
  * task exceptions are logged, never silently swallowed (the classic
    asyncio fire-and-forget footgun)

Single-node by design. The multi-node story (worker leases + heartbeats on
the runs projection) is sketched in ADR-0003 and LIMITATIONS.md.
"""

from __future__ import annotations

import asyncio

from relay.engine.loop import AgentEngine
from relay.engine.recovery import recover_interrupted_runs
from relay.observability import get_logger
from relay.store.base import EventStore
from relay.tools.registry import ToolRegistry

log = get_logger(__name__)


class RunManager:
    def __init__(
        self, *, engine: AgentEngine, store: EventStore, registry: ToolRegistry
    ) -> None:
        self._engine = engine
        self._store = store
        self._registry = registry
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def engine(self) -> AgentEngine:
        return self._engine

    def schedule(self, run_id: str) -> None:
        """Drive a run in the background (idempotent per process)."""
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._engine.drive(run_id), name=f"run:{run_id}")
        task.add_done_callback(lambda t: self._on_done(run_id, t))
        self._tasks[run_id] = task

    async def recover(self, *, exclusive: bool) -> list[str]:
        """Reconcile claims, then resume runs as tracked background tasks."""
        recovered = await recover_interrupted_runs(
            store=self._store,
            engine=self._engine,
            registry=self._registry,
            exclusive=exclusive,
        )
        for run_id in recovered:
            self.schedule(run_id)
        return recovered

    async def ready(self) -> None:
        """Verify that the configured durability boundary is reachable."""
        await self._store.read("__relay_readiness__", from_seq=0)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def _on_done(self, run_id: str, task: asyncio.Task) -> None:
        if task.cancelled():
            log.info("run_task_cancelled", extra={"ctx": {"run_id": run_id}})
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "run_task_crashed",
                extra={"ctx": {"run_id": run_id, "error": repr(exc)}},
            )
