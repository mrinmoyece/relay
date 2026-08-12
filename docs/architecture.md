# Architecture

## Invariants (the things that must never break)

1. **The log is the truth.** A run's state is only ever derived by folding its event log (`domain/run.py::replay`). No component mutates run state directly.
2. **Persist intent before side effects.** `ToolCallRequested` lands in the store before the tool runs; `LLMResponded` before its tool calls are acted on. A crash between any two events is recoverable by construction.
3. **At most one effective writer per run.** Optimistic concurrency (`expected_version`) means racing writers cannot both append; the loser re-reads and defers.
4. **Nothing enforces safety in a prompt.** Budgets, allowlists, policy decisions, and approvals are runtime checks. Prompts are suggestions; the runtime is the guarantee.
5. **The engine only sees protocols.** `EventStore`, `LLMProvider`, `MemoryStore`, `Tool` — every integration is swappable without touching the loop.

## Component walkthrough

### domain/ — pure core
`events.py` defines the closed vocabulary of facts (14 event types). `run.py` is the fold: `apply(state, record) -> state`, with an explicit state machine that raises `InvalidTransition` on illegal applications — a corrupted log or an engine bug fails loudly, never silently. `budget.py` returns violations as values so the engine can record them as events. Nothing in this package does I/O; it is the most heavily unit-tested code in the repo, and deliberately the easiest to test.

### store/ — the durability boundary
One protocol, two backends with identical contracts (gapless per-run `seq`, atomic multi-event append, optimistic concurrency, status projection updated in-transaction). The in-memory store is not a stub — it implements the full contract, which is why integration tests against it exercise the real concurrency logic. The Postgres store serializes appends per run with `SELECT ... FOR UPDATE` on the projection row; the composite PK `(run_id, seq)` is a physical backstop against duplicate sequence numbers even if the locking were broken.

### llm/ — the vendor seam
`ChatMessage`/`ToolCallSpec`/`Usage` are provider-neutral; each adapter owns the translation to its wire format. Cost accounting is part of the provider contract (`Usage.cost_usd`) because budgets cannot be enforced on data the runtime doesn't have. The mock provider is scripted and deterministic — the foundation of the eval suite. `ProviderError.retryable` classifies failures so the engine retries rate limits/5xx but fails fast on auth errors.

### tools/ — capability + safety metadata
Tools declare `risk`, `idempotent`, and `timeout_s` up front; the runtime enforces all three. The registry provides per-run scoping (`allowed_tools`), and the policy engine maps risk -> allow/approve/deny with per-tool overrides. Builtins demonstrate one of each risk level, each with a real defensive measure: AST-whitelisted arithmetic (no `eval`), SSRF domain allowlist with redirects disabled, path-traversal-proof file writes, and a non-idempotent simulated email sender.

### engine/ — where it all composes
`loop.py::drive()` is intentionally boring to read: replay, then do exactly one of (process pending tool call | check budget | call model), append, repeat. Every iteration re-reads the log, which is what makes cancellation, approvals from another process, and crash recovery compose. A worker appends `ToolExecutionStarted` before invoking a handler; optimistic concurrency turns that event into a fencing claim, preventing racing workers from both performing the same side effect. `executor.py` owns tool reliability (timeout, classified retries, backoff, output caps, "a tool bug must never crash the loop"). `recovery.py` reclaims interrupted idempotent calls and escalates ambiguous non-idempotent calls. `manager.py` runs loops as tracked asyncio tasks with clean shutdown.

### memory/ — cross-run learning, made explicit
Completed runs are distilled into `MemoryEntry` records; new runs retrieve relevant entries and inject them into the system prompt *before* `RunCreated` is written, so the ledger records exactly what influenced the run. Retrieval is keyword overlap by design (dependency-free, explainable); the protocol is the upgrade point for embeddings.

### api/ — thin by intent
DTOs are decoupled from domain objects. POST /v1/runs returns 202 and schedules the loop as a background task; the API never blocks on model calls. Approvals and cancellation work from any process because they are just appends.

## Data flow of one tool-using step

```
replay(log) -> state
  └─ state.pending_calls non-empty?
       policy.decide(tool)
         ALLOW  -> append ToolExecutionStarted -> executor.execute -> append ToolSucceeded/ToolFailed
         APPROVE-> append ApprovalRequired  (run parks; API resumes it later)
         DENY   -> append ToolFailed("denied by policy")   # surfaced to model
  └─ else: check_budget -> maybe append BudgetExceeded (terminal)
  └─ else: provider.complete(transcript, tool_defs)
             append LLMResponded (+ ToolCallRequested per call | RunCompleted)
```

## Scaling story (single node today → fleet)

Current deployment is single-node: one process drives runs as asyncio tasks. Cross-process races cannot fork ledger state, and persisted execution claims fence tool side effects. Duplicate provider calls remain possible before one worker wins the response append, so a worker fleet still needs leases to avoid duplicate spend. The documented path (ADR-0003, LIMITATIONS.md) adds `claimed_by` + `lease_expires_at` to the runs projection; workers claim runs and heartbeat while the ledger remains the source of truth.
