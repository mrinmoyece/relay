"""Metrics: event -> counter mapping and Prometheus text rendering."""

from __future__ import annotations

from relay.domain.events import (
    ApprovalRequired,
    BudgetExceeded,
    LLMResponded,
    RunCompleted,
    RunCreated,
    ToolFailed,
    ToolSucceeded,
)
from relay.domain.types import RiskLevel, ToolCallSpec, Usage
from relay.observability.metrics import MetricsRegistry, record_event


def make_reg() -> MetricsRegistry:
    return MetricsRegistry()


def test_event_stream_drives_counters():
    reg = make_reg()
    record_event(RunCreated(goal="g", model="m"), reg)
    record_event(
        LLMResponded(step=1, usage=Usage(input_tokens=100, output_tokens=40, cost_usd=0.01)),
        reg,
    )
    record_event(ToolSucceeded(call_id="c1", tool_name="calculator", output="4"), reg)
    record_event(ToolFailed(call_id="c2", tool_name="http_get", error="boom"), reg)
    record_event(RunCompleted(final_answer="done"), reg)

    out = reg.render()
    assert "relay_runs_started_total 1" in out
    assert 'relay_runs_finished_total{status="completed"} 1' in out
    assert "relay_llm_calls_total 1" in out
    assert 'relay_llm_tokens_total{direction="input"} 100' in out
    assert 'relay_llm_tokens_total{direction="output"} 40' in out
    assert 'relay_tool_executions_total{outcome="success",tool="calculator"} 1' in out
    assert 'relay_tool_executions_total{outcome="failure",tool="http_get"} 1' in out


def test_budget_and_approval_metrics():
    reg = make_reg()
    record_event(BudgetExceeded(budget_kind="steps", limit=5, used=5), reg)
    call = ToolCallSpec(call_id="e1", tool_name="send_email", arguments={})
    record_event(
        ApprovalRequired(approval_id="a1", call=call, risk=RiskLevel.DESTRUCTIVE, reason="r"),
        reg,
    )
    out = reg.render()
    assert 'relay_budget_exceeded_total{kind="steps"} 1' in out
    assert 'relay_runs_finished_total{status="budget_exceeded"} 1' in out
    assert "relay_approvals_requested_total 1" in out


def test_render_format_is_prometheus_text():
    reg = make_reg()
    reg.inc("relay_test_total", "help text", value=2, label='va"lue')
    out = reg.render()
    # HELP/TYPE headers exactly once per metric; label values escaped.
    assert out.count("# HELP relay_test_total help text") == 1
    assert out.count("# TYPE relay_test_total counter") == 1
    assert 'relay_test_total{label="va\\"lue"} 2' in out
    assert out.endswith("\n")


def test_gauges_rendered_at_scrape_time():
    reg = make_reg()
    out = reg.render(gauges={"relay_runs": {(("status", "running"),): 3.0}})
    assert "# TYPE relay_runs gauge" in out
    assert 'relay_runs{status="running"} 3' in out


def test_cardinality_no_run_id_labels():
    """Guard rail: no metric may ever use run_id as a label."""
    reg = make_reg()
    record_event(RunCreated(goal="g", model="m"), reg)
    assert "run_id" not in reg.render()
