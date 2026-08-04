# Failure modes

What breaks, what Relay does about it, and what the residual blast radius is. Ordered roughly by how often each happens in production.

## 1. Provider errors (rate limits, 5xx, timeouts, auth)
**Behavior:** adapter classifies via `ProviderError.retryable`. Retryable → up to `llm_max_attempts` with backoff; non-retryable (auth, invalid request) → fail fast. Exhausted/permanent → `RunFailed(reason="provider_error")` in the ledger with the error detail.
**Residual risk:** a long provider outage fails runs rather than queueing them. Deliberate: silently parking runs on outage hides the incident. (A retry-later queue is a straightforward extension — resubmit failed runs.)
**Tested by:** `test_retryable_provider_error_is_retried`, `test_non_retryable_provider_error_fails_run`.

## 2. Tool failures (bugs, timeouts, bad model arguments)
**Behavior:** executor enforces per-attempt timeouts; retries only declared-transient errors; catches *all* exceptions (a tool bug can never crash the loop). Final failure → `ToolFailed`, surfaced to the model as `ERROR: ...` so it can adapt — the eval `recovers_from_bad_tool_call` proves the agent survives its own bad tool call.
**Residual risk:** a tool that *hangs the event loop* (sync CPU spin) evades asyncio timeouts; tool authors must stay async/non-blocking. Process-level watchdog is the hard backstop (not built).
**Tested by:** `test_timeout_is_enforced_and_retried`, `test_unexpected_exception_never_escapes`, `test_tool_error_is_surfaced_and_model_recovers`.

## 3. Worker crash mid-run
**Behavior:** all progress up to the last append is durable. Recovery scans `RUNNING` runs, appends `RunResumed`, and continues. In-flight tool call ambiguity resolved by idempotency contract: re-execute idempotent, escalate non-idempotent to a human.
**Residual risk:** the money/email window — a crash *after* a non-idempotent side effect but *before* its result event means a human must investigate external state to answer "did it happen?". No system can close this window; Relay makes it visible instead of pretending.
**Tested by:** `test_crash_with_idempotent_call_resumes_automatically`, `test_crash_with_non_idempotent_call_escalates_to_human`.

## 4. Runaway agent (tool loops, degenerate plans)
**Behavior:** step/token/cost budgets checked by the runtime before every model call → `BudgetExceeded` terminal state at exactly the limit. The adversarial eval uses a provider that loops forever.
**Residual risk:** budgets are checked before LLM calls, so a run can overshoot token/cost budgets by at most one call. Documented in LIMITATIONS.md.
**Tested by:** `test_step_budget_halts_a_looping_agent`, eval `runaway_loop_is_halted`.

## 5. Concurrent writers (double-drive, race with cancel/approval)
**Behavior:** optimistic concurrency — the stale writer gets `ConcurrencyError`, re-reads the ledger, and defers. Cancel racing a tool execution: the cancel event wins the append; the driver's subsequent append fails; the tool's side effect may still have occurred (same ambiguity as #3, same visibility).
**Residual risk:** wasted duplicate work (not incorrect state) if two processes drive one run; the lease layer described in ADR-0003 removes the waste at fleet scale.
**Tested by:** `test_optimistic_concurrency_rejects_stale_writer`.

## 6. Prompt injection steering the agent
**Behavior:** contained structurally, not linguistically: per-run tool allowlists (hallucinated/injected tools can't execute), deny-by-default policy, human gates on destructive actions, budget caps on waste. Eval `tool_allowlist_enforced` asserts the security invariant "non-allowlisted tools never succeed" on every scenario.
**Residual risk:** injection can still shape *content* (a misleading final answer) and waste bounded budget. Content-level defenses (output validation, source attribution) are application-layer concerns above the runtime.

## 7. Storage failures
**Behavior:** appends are atomic (all events in a batch or none); a failed append fails the driving iteration, leaving the run resumable at its last durable state. Postgres down → API errors on writes; existing ledgers are untouched.
**Residual risk:** in-memory mode loses everything on restart — it exists for dev/tests and logs a startup warning. The projection cannot drift from the log (same transaction), but a *deleted/corrupted* events table is unrecoverable without DB backups: the ledger IS the system of record, so back it up like one.

## 8. Poison runs (state that crashes the engine itself)
**Behavior:** the fold raises `InvalidTransition` on any illegal event application — a corrupted ledger fails loudly at replay, and recovery isolates failures per run (one poison run cannot block recovery of others).
**Residual risk:** a bug in fold logic itself. Mitigation: `domain/` is pure and carries the densest test coverage in the repo; `test_replay_is_deterministic` pins fold determinism.
