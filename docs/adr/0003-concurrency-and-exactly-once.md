# ADR-0003: Optimistic concurrency + idempotency contracts (no distributed locks, no exactly-once claims)

Status: accepted

## Context
Two failure classes threaten correctness: (a) two workers driving the same run concurrently (double tool execution, interleaved appends); (b) a crash between requesting a side effect and recording its result — the "did the email send?" ambiguity. Naive answers are distributed locks and "exactly-once execution," both of which have well-known failure modes.

## Decision
**(a) Optimistic concurrency plus persisted execution claims.** Every append carries `expected_version` (the last seq the writer saw). The store rejects mismatches atomically. Before invoking a tool handler, a worker must append `ToolExecutionStarted`. Racing workers may both observe `ToolCallRequested`, but only one can append the execution claim at that version; the loser replays and sees the call is already in flight, so it does not perform the side effect. Postgres serializes appenders with `SELECT ... FOR UPDATE`; `(run_id, seq)` is a physical second guard.

**(b) Idempotency contracts instead of exactly-once.** Exactly-once side effects are impossible in general (the crash window between effect and record cannot be closed). Relay persists request, execution claim, then result; on recovery, a claimed call without a result is resolved by the tool's declared contract:
- `idempotent=True` → re-execute; duplicate execution is harmless by declaration.
- `idempotent=False` → escalate to a human via `ApprovalRequired` with recovery context. A person decides "run it (again)" or "skip".

## Alternatives considered
- **Advisory locks / lease service (Redis, ZooKeeper).** Adds an infra dependency and the classic fencing problem. The event version fences tool execution claims and result writes. A projection lease is still the planned fleet-level efficiency layer for preventing duplicate provider calls and spend.
- **SERIALIZABLE isolation.** Correct but pushes retries onto every transaction; the explicit version check is cheaper and produces a domain-meaningful error.
- **Automatic dedup keys for non-idempotent tools.** Works only when the downstream system supports idempotency keys; when it does, tool authors can use them and mark the tool idempotent — the contract composes.

## Consequences
Ledger and tool-side-effect correctness do not depend on only one worker running. Recovery of non-idempotent calls needs a human, which is a latency cost accepted for destructive operations. Multi-node scale-out still needs leases to avoid duplicate provider calls and bounded duplicate spend.
