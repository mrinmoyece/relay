"""EventStore protocol - the durability boundary of the whole system.

Guarantees every backend must provide:

1. Per-run, gapless, monotonically increasing `seq`, assigned atomically.
2. Optimistic concurrency: append(expected_version=N) succeeds only if the
   run currently has exactly N events. Two workers racing on the same run
   cannot both win - the loser gets ConcurrencyError and must re-read.
   This is what makes "at most one live executor per run" safe without
   distributed locks (see ADR-0003).
3. Atomicity: all events in one append() land or none do.

The store also maintains a tiny `runs` projection (run_id -> status) so
recovery and list endpoints don't have to replay every log in the system.
The projection is updated in the same transaction as the append, so it can
lag reality by exactly zero events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from relay.domain.events import AnyEvent, EventRecord


class ConcurrencyError(Exception):
    """expected_version did not match - someone else appended first."""

    def __init__(self, run_id: str, expected: int, actual: int):
        super().__init__(
            f"run {run_id}: expected version {expected}, store has {actual}"
        )
        self.run_id = run_id
        self.expected = expected
        self.actual = actual


class EventStore(Protocol):
    async def append(
        self,
        run_id: str,
        events: Sequence[AnyEvent],
        *,
        expected_version: int,
        new_status: str,
    ) -> list[EventRecord]:
        """Atomically append events with seq = expected_version+1, +2, ...

        Raises ConcurrencyError if the run's current version differs from
        expected_version. `new_status` updates the runs projection in the
        same transaction.
        """
        ...

    async def read(self, run_id: str, *, from_seq: int = 0) -> list[EventRecord]:
        """All events for a run with seq > from_seq, ordered by seq."""
        ...

    async def list_runs(self, *, status: str | None = None) -> list[str]:
        """Run ids from the projection, optionally filtered by status."""
        ...
