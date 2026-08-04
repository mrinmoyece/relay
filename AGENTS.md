# AGENTS.md — instructions for AI coding agents working on this repo

You are working on **Relay**, a durable, event-sourced runtime for AI agents.
Read `docs/architecture.md` before making structural changes. This file is
binding: if a request conflicts with an invariant below, stop and flag it
instead of complying.

## Architecture invariants (never violate)

1. **The event log is the only truth.** Run state is derived by folding events
   (`domain/run.py`). Never add code that mutates run state directly, and never
   add a second source of truth for anything the ledger already records.
2. **Persist intent before side effects.** Any new side-effecting step must
   append its "requested" event before executing and its result event after.
3. **Additive event schema only.** New event types are fine. Never rename,
   remove, or change the type of a field on an existing event — old ledgers
   must replay forever. If a change seems unavoidable, propose a versioned
   upcaster in an ADR first.
4. **`domain/` stays pure.** No I/O, no clocks, no framework imports, no
   randomness inside the fold. Anything nondeterministic happens in `engine/`
   and gets recorded as an event.
5. **Safety lives in the runtime, not prompts.** Budgets, allowlists, policy
   decisions, and approvals are code paths. Never "fix" a safety issue by
   editing a system prompt.
6. **Vendor types stay in adapters.** Nothing outside `llm/<vendor>.py` may
   import or construct vendor-specific shapes. The engine speaks only the
   neutral types in `domain/types.py` and `llm/base.py`.
7. **No agent frameworks.** Do not introduce LangChain/CrewAI/AutoGen or
   similar. See ADR-0002 for why; changing this requires superseding that ADR.

## Engineering standards

- **Python ≥3.10, ruff-clean** (`make lint`), line length 100, full type hints
  on public functions. Prefer frozen dataclasses/pydantic models for domain
  types.
- **Tests are not optional.** Every behavior change ships with tests; every
  bug fix ships with a regression test that fails without the fix. Fast tests
  only (the whole suite runs in <2s) — use the in-memory store and mock
  provider; never call real networks or models in tests.
- **Behavioral changes need an eval.** If a change alters what the agent
  *does* (loop order, policy, recovery, budgets), add or update a scenario in
  `evals/scenarios.py`. CI gates on `python -m evals.run_evals`.
- **Errors:** classify retryable vs permanent explicitly (`ToolExecutionError`,
  `ProviderError`). A tool or provider bug must never crash the run loop.
- **Logging:** structured JSON via `observability/logging.py`
  (`log.info("event_name", extra={"ctx": {...}})`). No print statements, no
  f-string log soup, never log secrets, API keys, or full prompts.
- **Tracing:** wrap new engine steps in `span("component.action", ...)`.
- **Docs:** significant design decisions get an ADR in `docs/adr/` (context /
  decision / alternatives / consequences). New failure modes go in
  `docs/FAILURE_MODES.md`; known shortcuts go in `docs/LIMITATIONS.md` with an
  upgrade path.

## Security rules

- New tools MUST declare `risk` and `idempotent` honestly. When unsure,
  declare the more dangerous option (higher risk, `idempotent=False`).
- Never widen the default policy (`tools/policy.py`) — destructive stays
  approval-gated. Overrides belong in deployment config, not code defaults.
- Tool inputs are untrusted (they come from a model): validate arguments,
  cap output sizes, no `eval`/`exec`, no shell interpolation, keep SSRF and
  path-traversal guards intact.
- Never commit secrets. Config goes through `config.py` env vars with safe
  local defaults.

## Workflow

- Before finishing any task run: `make lint && make test && make evals` —
  all three must pass.
- Commits: imperative subject ≤72 chars, body explains *why*. One logical
  change per commit.
- Common procedures have skills in `agent-skills/` (add-tool, add-provider,
  add-event) — follow them; they encode the review checklist. To have Claude
  Code auto-load them, copy the directory: `cp -r agent-skills .claude/skills`.

## Repo map (orientation)

| Path | Role |
|---|---|
| `src/relay/domain/` | Events, state machine fold, budgets — pure core |
| `src/relay/store/` | EventStore protocol; Postgres + in-memory backends |
| `src/relay/llm/` | Provider protocol; Anthropic + deterministic mock |
| `src/relay/tools/` | Tool contract, registry, risk levels, policy engine |
| `src/relay/engine/` | Agent loop, executor, crash recovery, run manager |
| `src/relay/memory/` | Long-term memory (distill + retrieve) |
| `src/relay/api/` | FastAPI surface |
| `tests/`, `evals/` | Unit/integration tests; CI-gated behavioral scenarios |
