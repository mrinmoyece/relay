"""Prometheus metrics, derived from the event stream.

Design: metrics are just another *consumer of events*. The engine feeds
every appended event through `record_event()`, so the counters below can
never disagree with the ledger - the same philosophy as the runs
projection. No background threads, no sampling, no second bookkeeping
path to get wrong.

The exposition format (Prometheus text format 0.0.4) is simple enough
that counters and gauges need no client library - one fewer dependency,
and the format is fully covered by tests. If histograms (latency
quantiles) are needed later, swap this module's internals for
prometheus_client behind the same functions; nothing else changes.

Cardinality discipline: label values are bounded sets only (event types,
tool names from the registry, terminal statuses). run_id is deliberately
NEVER a label - unbounded label cardinality is the classic way to kill a
Prometheus server.
"""

from __future__ import annotations

import threading
from collections import defaultdict

from relay.domain.events import (
    AnyEvent,
    ApprovalRequired,
    BudgetExceeded,
    LLMResponded,
    RunCompleted,
    RunCreated,
    RunFailed,
    ToolFailed,
    ToolSucceeded,
)


class MetricsRegistry:
    """Thread-safe counters keyed by (metric_name, sorted label tuple)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._help: dict[str, str] = {}

    def inc(self, name: str, help_text: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._help.setdefault(name, help_text)
            self._counters[key] += value

    def render(
        self, gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] | None = None
    ) -> str:
        """Render Prometheus text format. `gauges` are point-in-time values
        (e.g. runs by status) computed by the caller at scrape time."""
        lines: list[str] = []
        with self._lock:
            items = sorted(self._counters.items())
            help_texts = dict(self._help)
        seen: set[str] = set()
        for (name, labels), value in items:
            if name not in seen:
                seen.add(name)
                lines.append(f"# HELP {name} {help_texts.get(name, '')}")
                lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{_fmt_labels(labels)} {_fmt_value(value)}")
        for name, series in (gauges or {}).items():
            lines.append(f"# HELP {name} {help_texts.get(name, 'point-in-time gauge')}")
            lines.append(f"# TYPE {name} gauge")
            for labels, value in sorted(series.items()):
                lines.append(f"{name}{_fmt_labels(labels)} {_fmt_value(value)}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:  # for tests
        with self._lock:
            self._counters.clear()
            self._help.clear()


def _fmt_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return "{" + body + "}"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_value(v: float) -> str:
    return str(int(v)) if v == int(v) else repr(v)


# Global registry (module-level, like a tracer). Injectable in tests.
registry = MetricsRegistry()


def record_event(event: AnyEvent, reg: MetricsRegistry | None = None) -> None:
    """Map one domain event to metric increments. Called by the engine for
    every event it appends - the single choke point."""
    r = reg or registry
    if isinstance(event, RunCreated):
        r.inc("relay_runs_started_total", "Runs created")
    elif isinstance(event, RunCompleted):
        r.inc("relay_runs_finished_total", "Runs reaching a terminal state", status="completed")
    elif isinstance(event, RunFailed):
        r.inc("relay_runs_finished_total", "Runs reaching a terminal state", status="failed")
    elif isinstance(event, BudgetExceeded):
        r.inc(
            "relay_runs_finished_total",
            "Runs reaching a terminal state",
            status="budget_exceeded",
        )
        r.inc("relay_budget_exceeded_total", "Budget circuit-breaker trips", kind=event.budget_kind)
    elif isinstance(event, LLMResponded):
        r.inc("relay_llm_calls_total", "Completed LLM calls")
        r.inc(
            "relay_llm_tokens_total",
            "Tokens consumed",
            value=event.usage.input_tokens,
            direction="input",
        )
        r.inc(
            "relay_llm_tokens_total",
            "Tokens consumed",
            value=event.usage.output_tokens,
            direction="output",
        )
        r.inc("relay_llm_cost_usd_total", "Model spend in USD", value=event.usage.cost_usd)
    elif isinstance(event, ToolSucceeded):
        r.inc(
            "relay_tool_executions_total",
            "Tool executions by outcome",
            tool=event.tool_name,
            outcome="success",
        )
    elif isinstance(event, ToolFailed):
        r.inc(
            "relay_tool_executions_total",
            "Tool executions by outcome",
            tool=event.tool_name,
            outcome="failure",
        )
    elif isinstance(event, ApprovalRequired):
        r.inc("relay_approvals_requested_total", "Human approval gates hit")
