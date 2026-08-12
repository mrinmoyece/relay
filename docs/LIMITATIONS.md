# Limitations & what I'd do differently

An honest inventory. Each item names the current shortcut, why it was acceptable here, and the production upgrade path.

## Runtime
- **Single-node scheduling.** Runs are asyncio tasks in one process. Multiple processes cannot fork ledger state or duplicate a claimed tool side effect, but they can duplicate provider calls before one response append wins. Upgrade: lease + heartbeat columns on the runs projection; workers claim expired leases. Ledger schema unchanged.
- **Recovery needs exclusive ownership.** Startup recovery can clear persisted execution claims only when the prior worker is guaranteed dead. It is disabled by default and explicitly enabled in the supplied single-worker deployments. Multi-worker recovery requires leases with expiry/fencing; merely scanning every `RUNNING` run is unsafe.
- **Budget overshoot window.** Budgets are checked before each LLM call, so a run can exceed token/cost limits by at most one call. Upgrade: pre-call estimation (prompt tokens + max_output) against remaining budget.
- **Sequential tool execution.** Multiple tool calls from one model turn run one at a time. Simpler recovery semantics; parallel execution of read-only tools is a safe optimization later.
- **No mid-execution cancellation.** Cancel wins at the next append, but an in-flight tool/LLM call runs to completion first. Upgrade: cooperative cancellation tokens threaded into the executor.
- **Context grows unboundedly within a run.** The full transcript is sent every step. Fine for budgeted runs; long-horizon agents need context compaction (summarize old steps into working notes) — a natural fit as a fold-derived view.

## Storage
- **No event schema versioning/upcasting yet.** Events are additive so far. Before changing any existing field: add `schema_version` to the envelope + upcasters at read time.
- **No snapshots.** Replay is O(events/run); trivial at agent scale (tens of events). Snapshot every N events if runs grow long.
- **`init_schema()` at startup instead of migrations.** Fine for one service owning its DB; use Alembic once multiple services share it.

## Models & memory
- **Static price table in the Anthropic adapter.** A vendor price change requires a deploy. Upgrade: price config from settings/remote config.
- **Keyword-overlap memory retrieval.** Deliberate (dependency-free, explainable, testable). Upgrade behind the same `MemoryStore` protocol: embeddings + pgvector, hybrid scoring, recency decay. Also: memory distillation is deterministic (tools used, errors hit); an LLM-written "lesson" would be richer — at the cost of a model call per completed run.
- **No context-window management for tool outputs beyond a char cap.**

## Safety & security
- **No external code sandbox.** In-process guards (AST whitelist, SSRF allowlist, path confinement) protect well-typed tools only. An arbitrary-code tool requires container/microVM isolation (ADR-0006). This is the single biggest gap between this repo and a hardened production deployment.
- **No authn/authz on the API.** Anyone who can reach it can approve runs. Upgrade: OIDC on the API, approver identity from the token (the ledger already records approver identity).
- **Risk levels are self-declared** by tool authors; deny-by-default covers the undeclared case, code review covers the misdeclared one.

## Operations
- **Metrics are counters/gauges only.** `/metrics` exposes run/tool/LLM/cost counters and run-status gauges, but no latency histograms. Upgrade: swap `observability/metrics.py` internals for prometheus_client histograms behind the same functions.
- **No dead-letter/auto-retry for provider-failed runs**; operators resubmit (RUNBOOK.md).
- **Alerting on stale `AWAITING_APPROVAL` runs** is described in the runbook but left to the deployment's alerting stack.

## What I'd do differently starting over
1. Design the lease/claim columns into the projection from day one — recovery and multi-node work land as one feature.
2. Make `Usage` carry cache-read/cache-write token classes now; provider prompt caching changes cost math and retrofitting accounting is annoying.
3. Emit a `PolicyDecided` event even for ALLOW — the ledger currently shows allowed calls implicitly (request → execution) rather than recording the decision itself.
