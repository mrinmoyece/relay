"""In-memory event store for development and tests.

Implements the exact same contract as the Postgres store (including
optimistic concurrency), so the engine cannot tell the difference. Tests
that pass against this store exercise the real concurrency logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from relay.domain.events import AnyEvent, EventRecord, utcnow
from relay.store.base import ConcurrencyError


class InMemoryEventStore:
    def __init__(self) -> None:
        self._logs: dict[str, list[EventRecord]] = {}
        self._status: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def append(
        self,
        run_id: str,
        events: Sequence[AnyEvent],
        *,
        expected_version: int,
        new_status: str,
    ) -> list[EventRecord]:
        async with self._lock:
            log = self._logs.setdefault(run_id, [])
            actual = len(log)
            if actual != expected_version:
                raise ConcurrencyError(run_id, expected_version, actual)
            records = [
                EventRecord(
                    run_id=run_id,
                    seq=expected_version + i + 1,
                    recorded_at=utcnow(),
                    event=event,
                )
                for i, event in enumerate(events)
            ]
            log.extend(records)
            self._status[run_id] = new_status
            return records

    async def read(self, run_id: str, *, from_seq: int = 0) -> list[EventRecord]:
        async with self._lock:
            return [r for r in self._logs.get(run_id, []) if r.seq > from_seq]

    async def list_runs(self, *, status: str | None = None) -> list[str]:
        async with self._lock:
            if status is None:
                return list(self._status)
            return [rid for rid, s in self._status.items() if s == status]
