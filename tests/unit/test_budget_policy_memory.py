from __future__ import annotations

import pytest

from relay.domain.budget import Budget, check_budget
from relay.domain.types import RiskLevel
from relay.memory.store import InMemoryMemoryStore, MemoryEntry, render_memory_block
from relay.tools.builtin import calculator, make_send_email
from relay.tools.policy import PolicyDecision, PolicyEngine

# ---------------------------------------------------------------- budgets


def test_within_budget():
    b = Budget(max_steps=5, max_tokens=1000, max_cost_usd=1.0)
    assert check_budget(b, steps_used=4, tokens_used=999, cost_used_usd=0.99) is None


@pytest.mark.parametrize(
    ("kwargs", "kind"),
    [
        (dict(steps_used=5, tokens_used=0, cost_used_usd=0), "steps"),
        (dict(steps_used=0, tokens_used=1000, cost_used_usd=0), "tokens"),
        (dict(steps_used=0, tokens_used=0, cost_used_usd=1.0), "cost"),
    ],
)
def test_each_dimension_trips(kwargs, kind):
    b = Budget(max_steps=5, max_tokens=1000, max_cost_usd=1.0)
    violation = check_budget(b, **kwargs)
    assert violation is not None and violation.kind == kind


# ----------------------------------------------------------------- policy


def test_default_policy_matrix():
    policy = PolicyEngine()
    assert policy.decide(calculator) == PolicyDecision.ALLOW
    assert policy.decide(make_send_email()) == PolicyDecision.REQUIRE_APPROVAL


def test_tool_override_beats_risk_default():
    policy = PolicyEngine(tool_overrides={"calculator": PolicyDecision.DENY})
    assert policy.decide(calculator) == PolicyDecision.DENY


def test_unknown_risk_defaults_to_approval():
    policy = PolicyEngine(risk_defaults={})
    assert policy.decide(calculator) == PolicyDecision.REQUIRE_APPROVAL


def test_risk_levels_cover_builtins():
    assert calculator.risk == RiskLevel.READ_ONLY
    assert make_send_email().risk == RiskLevel.DESTRUCTIVE
    assert make_send_email().idempotent is False


# ----------------------------------------------------------------- memory


async def test_memory_retrieval_ranks_by_relevance():
    store = InMemoryMemoryStore()
    await store.add(
        MemoryEntry(goal="compute compound interest", summary="done", lessons="use calculator")
    )
    await store.add(
        MemoryEntry(goal="email the quarterly report", summary="done", lessons="needs approval")
    )
    hits = await store.search("interest computation with compound rates", limit=1)
    assert len(hits) == 1
    assert "interest" in hits[0].goal


async def test_memory_no_match_returns_empty():
    store = InMemoryMemoryStore()
    await store.add(MemoryEntry(goal="alpha beta", summary="s", lessons="l"))
    assert await store.search("zzz qqq") == []


def test_render_block_is_prompt_ready():
    block = render_memory_block(
        [MemoryEntry(goal="g", summary="s", lessons="do X first")]
    )
    assert "<relevant_experience>" in block
    assert "do X first" in block
    assert render_memory_block([]) == ""
