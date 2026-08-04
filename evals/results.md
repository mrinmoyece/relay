# Eval results

Behavioral regression suite over the agent runtime (scripted mock provider,
so results are exact and deterministic). Regenerate: `make evals`.

**6/6 scenarios passed** (0.01s)

| Scenario | Result | Notes |
|---|---|---|
| `single_tool_math` | PASS | One tool call then a grounded final answer |
| `multi_step_chaining` | PASS | Result of tool 1 feeds tool 2 |
| `recovers_from_bad_tool_call` | PASS | Tool error is surfaced; agent adapts instead of dying |
| `runaway_loop_is_halted` | PASS | Step budget stops an agent stuck in a tool loop |
| `destructive_tool_requires_human` | PASS | send_email parks the run; approval completes it |
| `tool_allowlist_enforced` | PASS | A tool outside the run's allowlist cannot execute |
