# ADR-0001: Event-source run state instead of mutable rows

Status: accepted

## Context
An agent run is a long-lived, multi-step process with side effects, human pauses, and a hard requirement to survive process death. We need durable state, crash recovery, an audit trail, and replay debugging.

## Decision
Run state is an append-only log of immutable domain events. Current state is derived by a pure fold (`replay`). There is no mutable `runs` row holding truth — only a tiny status projection for cheap lookups, updated in the same transaction as each append.

## Alternatives considered
- **Mutable state row + audit table.** Simpler reads, but recovery must reconstruct "where exactly were we?" from a snapshot that may be mid-step, and the audit table inevitably drifts from the row it describes. Two sources of truth.
- **Workflow engine (Temporal/Cadence).** Genuinely solves durable execution — and in a company with Temporal already deployed we would use it (its event-history model is precisely this pattern, industrialized). Rejected here because the runtime IS the project: we want the mechanism visible, not vendored, and we avoid a heavyweight infra dependency for a single service.
- **Checkpoint/snapshot files.** Cheap, but coarse-grained: you recover to the last checkpoint, not the last event, and there's no audit or replay value.

## Consequences
- Crash recovery, HITL pauses of arbitrary length, audit, and replay debugging all fall out of one mechanism.
- Reads pay a replay cost, O(events in run). Agent runs are short (tens of events), so this is negligible; if runs grew to thousands of events we'd add periodic state snapshots (documented, not built).
- Event schema becomes an API: adding event types is safe; changing fields on existing types requires versioned upcasting at read time. We keep events additive.
- Writing correct fold logic demands discipline (pure, exhaustive, transition-checked) — enforced by making `domain/` I/O-free and the most-tested package in the repo.
