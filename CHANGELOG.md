# Changelog

All notable changes to Relay are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com); versioning is semantic.
Breaking the event schema is a major version by definition.

## [Unreleased]

### Added
- Prometheus metrics: event-stream-driven counters + scrape-time run-status
  gauges at `GET /metrics`; scrape config in `observability/prometheus.yml`;
  `prometheus.io/*` pod annotations in k8s manifests.

## [0.1.0] - 2026-07-03

Initial release.

### Added
- Event-sourced run ledger with pure fold state machine (13 event types)
- EventStore protocol: Postgres (optimistic concurrency via row lock +
  composite PK) and in-memory backends with identical contracts
- Framework-free agent loop with runtime-enforced budgets (steps/tokens/USD)
- Tool system: risk levels, idempotency contracts, registry scoping,
  deny-by-default policy engine, human-in-the-loop approval gates
- Crash recovery with idempotency-aware resolution of in-flight tool calls
- LLM provider protocol: Anthropic adapter + deterministic mock
- Three-tier memory (working / episodic / long-term with retrieval injection)
- Observability: OTel spans with no-op fallback, structured JSON logging,
  per-run cost accounting
- FastAPI surface: create/inspect/approve/deny/cancel/replay
- 47 unit + integration tests; 6-scenario behavioral eval suite gating CI
- Docs: architecture, 6 ADRs, failure modes, runbook, limitations, evals
  methodology, interview study guide; AGENTS.md + agent-skills governance
