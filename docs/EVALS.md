# Evaluation methodology

## Why agent runtimes need behavioral evals

Unit tests verify components; behavioral evals verify what the *agent does end-to-end*: which tools it calls, in what order, whether it recovers from errors, whether safety rails hold. A change to loop ordering, policy defaults, or error surfacing can pass every unit test and still change agent behavior. The eval suite (`evals/`) is the regression net for that, and CI runs it as a merge gate — agent behavior is code.

## Design

Each `Scenario` scripts the model with the deterministic mock provider and asserts on observable outcomes only:
- terminal status (`completed` / `budget_exceeded` / ...)
- exact tool-call sequence from the ledger
- final-answer content
- step efficiency (`max_steps`)
- a universal security invariant: a tool outside the run's allowlist must never produce a `tool_succeeded` event, in any scenario.

Scenarios cover the runtime's core promises: single-tool grounding, multi-step chaining, error recovery, runaway-loop halting, human approval gating, and allowlist enforcement.

## Deterministic vs live-model evals

With the scripted mock, results are exact (pass/fail, not probabilistic) — appropriate because these evals test the RUNTIME's behavior around the model, not the model itself. To evaluate a real model + prompt on this runtime, the same harness extends naturally: swap `MockLLMProvider` for a live adapter, run each scenario N≥10 times, gate on pass-rate thresholds (e.g. ≥90%), and track cost/steps distributions per scenario. That distinction — deterministic runtime evals vs statistical model evals — is deliberate and worth preserving.

## Running

```bash
make evals            # or: python -m evals.run_evals
```

Exit code is non-zero on any failure (CI gate). Results are written to [`evals/results.md`](../evals/results.md).

## Latest results

6/6 scenarios passing — see [`evals/results.md`](../evals/results.md) for the per-scenario table (regenerated on every run).
