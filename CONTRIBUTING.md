# Contributing

## Setup

```bash
pip install -e ".[dev]"
make test && make lint && make evals   # all must pass before any PR
```

## Ground rules

- Read `AGENTS.md` first — its architecture invariants bind humans and AI
  agents alike. In particular: the event log is the only truth, the event
  schema is additive-only, `domain/` stays pure, and safety lives in the
  runtime, not prompts.
- Every behavior change ships with tests; every bug fix ships with a
  regression test that fails without the fix.
- Changes that alter agent *behavior* (loop, policy, budgets, recovery) must
  add or update a scenario in `evals/scenarios.py`.
- Significant design decisions get an ADR in `docs/adr/` (copy the format of
  an existing one: context / decision / alternatives / consequences).
- Common changes have step-by-step procedures in `agent-skills/` — follow
  them; their checklists are the review bar.

## Pull requests

- One logical change per PR; keep diffs reviewable (<400 lines preferred).
- Commit messages: imperative subject ≤72 chars; body explains *why*.
- CI must be green: lint, tests, evals, docker build.
- Update `CHANGELOG.md` under "Unreleased".

## Releases

Semantic versioning. Breaking the event schema is a major version by
definition (existing ledgers must always replay).
