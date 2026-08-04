"""Postgres event store (asyncpg).

Concurrency model
-----------------
Appends for a run are serialized with `SELECT ... FOR UPDATE` on the run's
projection row, then the version check happens inside that critical
section. Losers of a race see a clean ConcurrencyError, never a partial
write. The composite PK (run_id, seq) is a second, independent guard: even
if the locking logic were broken, duplicate seqs are physically impossible.

Why not advisory locks or SERIALIZABLE? See ADR-0003.

Schema lives in schema.sql next to this file; `init_schema()` applies it
idempotently at startup (fine for a single service; use a migration tool
like alembic when multiple services share this database).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from relay.domain.events import AnyEvent, EventRecord, event_adapter
from relay.store.base import ConcurrencyError

try:  # optional dependency: pip install "relay-runtime[postgres]"
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

_SCHEMA = Path(__file__).with_name("schema.sql")


class PostgresEventStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresEventStore:
        if asyncpg is None:  # pragma: no cover
            raise RuntimeError(
                "Postgres support requires asyncpg: pip install 'relay-runtime[postgres]'"
            )
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        store = cls(pool)
        await store.init_schema()
        return store

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA.read_text())

    async def close(self) -> None:
        await self._pool.close()

    async def append(
        self,
        run_id: str,
        events: Sequence[AnyEvent],
        *,
        expected_version: int,
        new_status: str,
    ) -> list[EventRecord]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Ensure the projection row exists, then take a row lock on
                # it. All concurrent appenders for this run queue up here.
                await conn.execute(
                    """
                    INSERT INTO runs (run_id, status) VALUES ($1, $2)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    run_id,
                    new_status,
                )
                await conn.fetchrow(
                    "SELECT run_id FROM runs WHERE run_id = $1 FOR UPDATE", run_id
                )
                row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(seq), 0) AS v FROM events WHERE run_id = $1",
                    run_id,
                )
                actual = row["v"]
                if actual != expected_version:
                    raise ConcurrencyError(run_id, expected_version, actual)

                records: list[EventRecord] = []
                for i, event in enumerate(events):
                    seq = expected_version + i + 1
                    inserted = await conn.fetchrow(
                        """
                        INSERT INTO events (run_id, seq, event)
                        VALUES ($1, $2, $3::jsonb)
                        RETURNING recorded_at
                        """,
                        run_id,
                        seq,
                        event.model_dump_json(),
                    )
                    records.append(
                        EventRecord(
                            run_id=run_id,
                            seq=seq,
                            recorded_at=inserted["recorded_at"],
                            event=event,
                        )
                    )
                await conn.execute(
                    "UPDATE runs SET status = $2, updated_at = now() WHERE run_id = $1",
                    run_id,
                    new_status,
                )
                return records

    async def read(self, run_id: str, *, from_seq: int = 0) -> list[EventRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT seq, recorded_at, event FROM events
                WHERE run_id = $1 AND seq > $2 ORDER BY seq
                """,
                run_id,
                from_seq,
            )
        return [
            EventRecord(
                run_id=run_id,
                seq=row["seq"],
                recorded_at=row["recorded_at"],
                event=event_adapter.validate_python(json.loads(row["event"])),
            )
            for row in rows
        ]

    async def list_runs(self, *, status: str | None = None) -> list[str]:
        async with self._pool.acquire() as conn:
            if status is None:
                rows = await conn.fetch("SELECT run_id FROM runs ORDER BY updated_at")
            else:
                rows = await conn.fetch(
                    "SELECT run_id FROM runs WHERE status = $1 ORDER BY updated_at", status
                )
        return [row["run_id"] for row in rows]
