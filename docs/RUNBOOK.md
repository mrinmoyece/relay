# Operations runbook

## Deploy

```bash
docker compose up --build          # api + postgres (+ --profile tracing for jaeger)
```

Config via environment (see `.env.example`). Key envs: `RELAY_DATABASE_URL` (unset = volatile in-memory mode — never in production), `RELAY_PROVIDER` (`mock`|`anthropic`), `RELAY_ANTHROPIC_API_KEY`, `RELAY_OTEL_ENDPOINT`, and `RELAY_STARTUP_RECOVERY_EXCLUSIVE`.

Startup recovery is disabled by default because a new process cannot infer that
an older worker holding an execution claim is dead. Enable
`RELAY_STARTUP_RECOVERY_EXCLUSIVE=true` only when deployment guarantees no
overlap, such as the single-API Docker Compose stack. Kubernetes pod replacement
does not provide this guarantee even with `replicas: 1` + `Recreate`, so the
supplied manifest leaves recovery disabled. With a valid guarantee, startup applies the
schema, scans interrupted runs, logs `startup_recovery`, and serves. Without it,
startup logs `startup_recovery_disabled`; use worker leases before running
multiple replicas.

## Health & monitoring

- `GET /healthz` — process liveness; does not touch dependencies.
- `GET /readyz` — readiness; returns 503 when the event store cannot be read.
- `GET /metrics` — Prometheus text format: `relay_runs_started_total`,
  `relay_runs_finished_total{status=…}`, `relay_llm_calls_total`,
  `relay_llm_tokens_total{direction=…}`, `relay_llm_cost_usd_total`,
  `relay_tool_executions_total{tool=…,outcome=…}`, `relay_budget_exceeded_total{kind=…}`,
  `relay_approvals_requested_total`, and gauge `relay_runs{status=…}`.
  Scrape config: `observability/prometheus.yml`; k8s pods carry
  `prometheus.io/*` annotations. Alert ideas are in that file's comments.
- Logs are one JSON object per line on stdout. Signal lines: `run_task_crashed` (engine bug — page), `recovery_failed` (poison run — investigate that ledger), `tool_attempt_failed` / `llm_attempt_failed` (normal at low rates; alert on spikes), `using_in_memory_store` (must never appear in prod).
- Traces: set `RELAY_OTEL_ENDPOINT`; spans `llm.call`, `tool.call`, `tool.execute` reconstruct each run's causal chain. `docker compose --profile tracing up`, then Jaeger at :16686.
- Suggested alerts:
  - runs in `AWAITING_APPROVAL` older than N hours (someone's inbox, not the system): `GET /v1/runs?status=awaiting_approval`, inspect each run's `pending_approval.reason`.
  - any `run_task_crashed` log line.
  - failure-rate of runs (`status=failed` count) above baseline.

## Incident procedures

### "A run is stuck"
1. `GET /v1/runs/{id}` — check `status`.
2. `awaiting_approval` → it's waiting for a person; `pending_approval` says who/what/why. Approve/deny via API.
3. `running` with no recent events (`GET /v1/runs/{id}/events`) → investigate `run_task_crashed`; restart an exclusive single-worker deployment to recover it. In a multi-worker deployment, reclaim it only through a lease/ownership mechanism.
4. Anything terminal → read the last event; the reason is recorded (`run_failed.detail`, `budget_exceeded`).

### "Did the agent actually send that email?"
`GET /v1/runs/{id}/events`. `tool_succeeded` for `send_email` → yes, with timestamp and approver in the preceding `approval_granted`. `tool_execution_started` with no result + a later crash-recovery `approval_required` → genuinely ambiguous: check the mail provider's outbox, then approve ("run again") or deny ("skip") accordingly.

### "The agent is burning money"
Per-run: `POST /v1/runs/{id}/cancel` (wins at the next append). Fleet-wide: set `RELAY_PROVIDER=mock` and restart (hard stop, in-flight runs resume later against the mock — visible and reversible), or stop the service; ledgers are durable.

### "Provider outage"
Runs fail with `provider_error` after retries; nothing corrupts. After the outage, resubmit failed goals (idempotent to retry a whole run — new run_id, fresh ledger).

### Rollback
The event log is append-only and additive-schema; deploying an older engine version reads newer ledgers as long as no new event types were emitted. If a release added event types, roll forward instead (or restore the DB snapshot taken at deploy).

## Backup
The `events` table is the system of record — standard Postgres PITR/backup discipline applies. The `runs` projection is derivable (replay all logs) and does not strictly need backup.
