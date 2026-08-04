# What & why

<!-- One paragraph: the change and the reason. Link issues. -->

## Checklist

- [ ] `make lint && make test && make evals` pass locally
- [ ] Tests added/updated (regression test for bug fixes)
- [ ] Behavior change → eval scenario added/updated in `evals/scenarios.py`
- [ ] Event schema untouched, or change is additive (AGENTS.md rule 3)
- [ ] New tools declare `risk` + `idempotent` honestly
- [ ] Docs updated (ADR for design decisions, FAILURE_MODES/LIMITATIONS if applicable)
- [ ] `CHANGELOG.md` updated under Unreleased

## Risk & rollback

<!-- Blast radius if this is wrong; how to roll back. -->
