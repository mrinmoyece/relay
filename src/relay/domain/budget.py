"""Run budgets - enforced by the runtime, never by the prompt.

A prompt that says "use at most 10 steps" is a suggestion; a runtime check
is a guarantee. Budgets are the circuit breaker that turns an agent from
"unbounded autonomous spend" into something an enterprise can deploy.

Checked BEFORE each LLM call (the expensive, loop-driving action), so a
run can slightly exceed token/cost budgets by at most one model call -
that overshoot is documented in LIMITATIONS.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Hard limits for a single run."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=20, ge=1, description="Max LLM calls per run")
    max_tokens: int = Field(default=200_000, ge=1, description="Max total tokens (in+out)")
    max_cost_usd: float = Field(default=5.0, gt=0, description="Max spend per run in USD")


class BudgetViolation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str  # "steps" | "tokens" | "cost"
    limit: float
    used: float


def check_budget(
    budget: Budget, *, steps_used: int, tokens_used: int, cost_used_usd: float
) -> BudgetViolation | None:
    """Return the first violated budget dimension, or None if within limits.

    Deliberately returns a value instead of raising: the engine turns a
    violation into a BudgetExceeded *event* so the run's ledger records
    exactly why it stopped.
    """
    if steps_used >= budget.max_steps:
        return BudgetViolation(kind="steps", limit=budget.max_steps, used=steps_used)
    if tokens_used >= budget.max_tokens:
        return BudgetViolation(kind="tokens", limit=budget.max_tokens, used=tokens_used)
    if cost_used_usd >= budget.max_cost_usd:
        return BudgetViolation(kind="cost", limit=budget.max_cost_usd, used=cost_used_usd)
    return None
