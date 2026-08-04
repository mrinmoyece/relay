# ADR-0003: Optimistic concurrency + idempotency contracts (no distributed locks, no exactly-once claims)

Status: accepted

## Context
Two failure classes threaten correctness: (a) two workers driving the same run concurrently (double tool execution, interleaved appends); (b) a crash between requesting a side effect and recording its result — the "did the email send?" ambiguity. Naive answers are distributed locks and "exactly-once execution," both of which have well-known failure modes.

## Decision
**(a) Optimistic concurrency.** Every append carries `expected_version` (the last seq the writer saw). The store rejects mismatches atomically. Postgres implementation: `SELECT ... FOR UPDATE` on the run's projection row serializes appenders; composite PK `(run_id, seq)` is a physical second guard. A losing writer catches `ConcurrencyError`, re-reads the log, and defers to it. No lock service, no lease coordination needed for correctness — only for efficiency (avoiding wasted work), which single-node task tracking already provides.

**(b) Idempotency contracts instead of exactly-once.** Exactly-once side effects are impossible in general (the crash window between effect and record cannot be closed). Relay persists intent (`ToolCallRequested`) before execution and result after; on recovery, an in-flight call is resolved by the tool's declared contract:
- `idempotent=True` → re-execute; duplicate execution is harmless by declaration.
- `idempotent=False` → escalate to a human via `ApprovalRequired` with recovery context. A person decides "run it (again)" or "skip".

## Alternatives considered
- **Advisory locks / lease service (Redis, ZooKeeper).** Adds an infra dependency and the classic fencing problem: a paused worker whose lock expired can still write. Our version check IS a fencing token, enforced at the only place that matters — the write.
- **SERIALIZABLE isolation.** Correct but pushes retries onto every transaction; the explicit version check is cheaper and produces a domain-meaningful error.
- **Automatic dedup keys for non-idempotent tools.** Works only when the downstream system supports idempotency keys; when it does, tool authors can use them and mark the tool idempotent — the contract composes.

## Consequences
Correctness never depends on "only one worker is running" being true. Recovery of non-idempotent calls needs a human, which is a latency cost we accept deliberately for destructive operations. Multi-node scale-out needs only an efficiency layer (leases + heartbeats on the projection), not a correctness change.
