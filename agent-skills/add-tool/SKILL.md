---
name: add-tool
description: Add a new tool to the Relay runtime with correct risk/idempotency declarations, tests, and eval coverage. Use whenever asked to add, create, or integrate a new agent tool or capability.
---

# Add a tool to Relay

## Procedure

1. **Classify before coding.** Decide honestly:
   - `risk`: `READ_ONLY` (no mutation) / `WRITE` (cheap to undo) /
     `DESTRUCTIVE` (irreversible or externally visible). Unsure → higher risk.
   - `idempotent`: is re-executing the same call harmless? Unsure → `False`.
     This drives crash recovery: non-idempotent in-flight calls escalate to a
     human instead of re-running.
2. **Implement** in `src/relay/tools/builtin.py` (or a new module for large
   tools), following the existing patterns:
   - async handler `(args: dict) -> str`; validate every argument (inputs come
     from a model — they are untrusted).
   - raise `ToolExecutionError(msg, retryable=True)` for transient failures
     (network, contention), `retryable=False` for permanent ones (bad args).
   - cap output size (output enters model context and costs tokens).
   - defensive guards where relevant: allowlists for network, path confinement
     for filesystem, no eval/exec/shell interpolation.
   - side-effecting external calls: prefer idempotency keys if the downstream
     API supports them (then you may declare `idempotent=True`).
3. **Register** it in `build_default_registry()` (`api/app.py`) if it should
   ship by default.
4. **Test** in `tests/`: happy path, argument validation failure, and the
   failure classification (retryable vs permanent). Use the executor fixture —
   never real networks in tests.
5. **Eval**: if the tool changes what agents can do, add a scenario in
   `evals/scenarios.py` scripting the mock model to use it, asserting the tool
   sequence and terminal status. DESTRUCTIVE tools must have a scenario proving
   they park for approval.
6. **Docs**: one line in the module docstring table; if the tool introduces a
   new failure mode, add it to `docs/FAILURE_MODES.md`.

## Checklist (verify before finishing)

- [ ] `risk` and `idempotent` declared and justified in a comment
- [ ] arguments validated; output capped
- [ ] errors classified retryable/permanent
- [ ] tests added; `make lint && make test && make evals` all pass
- [ ] DESTRUCTIVE → approval-gating scenario exists
