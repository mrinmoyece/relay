"""Relay - a durable, event-sourced runtime for AI agents.

Relay treats an agent run the way a bank treats an account: as an immutable
ledger of events. The current state of any run is *derived* by folding its
event log, never stored as mutable truth. That single decision is what makes
crash recovery, human-in-the-loop approvals, replay debugging, and audit
trails fall out almost for free.

Package layout:
    domain/         Pure domain model: events, run state machine, budgets.
                    No I/O, no framework imports. Fully unit-testable.
    store/          EventStore protocol + Postgres and in-memory backends.
    llm/            LLMProvider protocol + Anthropic and deterministic mock.
    tools/          Tool protocol, registry, risk levels, policy engine.
    engine/         The agent loop, tool executor, crash recovery.
    observability/  OTel tracing (no-op fallback), cost accounting, logging.
    api/            FastAPI surface: create/inspect/approve/cancel runs.
"""

__version__ = "0.1.0"
