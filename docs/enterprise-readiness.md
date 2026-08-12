# Enterprise readiness

This document separates controls implemented in Relay from controls a production
platform must add. Relay is a strong runtime reference implementation; it is not
safe to expose as a multi-tenant enterprise service without the deployment gates
below.

## Control evidence

| Area | Implemented evidence | Current maturity |
|---|---|---|
| Durability | Atomic append, gapless per-run sequence, replay, Postgres backend | Production-oriented |
| Concurrency | Expected-version writes and persisted tool execution claims | Tool-safe; fleet scheduling pending |
| Recovery | Startup scan, idempotent replay, HITL for ambiguous effects | Production-oriented |
| Safety | Tool allowlists, risk policy, approval, budgets, input/output caps | Strong runtime baseline |
| Availability | Bounded provider/tool calls, liveness/readiness, graceful task shutdown | Single-node |
| Observability | Structured logs, OTel spans, ledger-derived metrics | Baseline; no latency histograms/SLOs |
| Verification | 69 tests, six deterministic behavioral scenarios, CI matrix | Strong runtime regression coverage |
| Supply chain | Non-root image, CodeQL, Dependabot, read-only CI token defaults | Baseline |
| Identity | Approver string recorded from request | Not production-ready |
| Tenant isolation | None | Not implemented |
| Sandbox | Defensive builtins only | External sandbox required for code tools |

## Mandatory production gates

Do not expose Relay to untrusted users until all applicable gates are satisfied:

1. Put the API behind OIDC authentication and authorization. Derive approver
   identity from verified claims; never trust the request body.
2. Enforce tenant ownership on every run, event, approval, cancellation, memory
   lookup, and metric view.
3. Use Postgres with encryption, least-privilege credentials, backups, restore
   drills, retention policy, and access auditing. The ledger contains prompts
   and tool outputs.
4. Move policy overrides, domain allowlists, budgets, and tool grants into
   reviewed deployment configuration.
5. Add egress controls and private-address/DNS-rebinding defenses appropriate
   to the network. Domain checks alone are not a universal SSRF boundary.
6. Run arbitrary-code tools only in isolated containers or microVMs with
   resource quotas, no ambient credentials, and explicit network policy.
7. Add worker leases before horizontal scaling. Tool side effects are fenced,
   but duplicate provider calls can still consume budget across racing workers.
   Do not enable exclusive startup recovery in an overlapping deployment.
8. Define SLOs and alerts for provider latency/errors, run failures, stale
   approvals, recovery failures, budget trips, and database saturation.
9. Pin and scan deployable artifacts, produce an SBOM, sign images, and enforce
   provenance in the deployment environment.
10. Run live-model evaluation against representative, adversarial, and
    tenant-specific tasks before every model or prompt rollout.

## Deployment profiles

| Profile | Suitable use | Required configuration |
|---|---|---|
| Local demo | Development and deterministic evals | In-memory store + mock provider |
| Internal single-node | Trusted users behind a gateway | Postgres, OIDC/RBAC, secrets, backups, alerts |
| Multi-worker | Higher throughput | All internal controls + leases/heartbeats + queue |
| Multi-tenant | External enterprise service | Tenant isolation, quotas, per-tenant policy/memory, audit export |

## Residual risk register

| Risk | Impact | Current treatment | Upgrade path |
|---|---|---|---|
| Provider response exceeds remaining budget | Cost overshoot by one call | Post-call usage accounting | Preflight token/cost reservation |
| Duplicate provider calls across workers | Bounded wasted spend | Winning append preserves one history | Projection lease + fencing token |
| Crash after non-idempotent side effect | Ambiguous external outcome | Durable claim + human review | Downstream idempotency keys |
| Unbounded transcript growth | Context/latency growth | Run budgets | Event-derived compaction/snapshots |
| JSONL memory on multiple nodes | Lost/interleaved memory updates | Single-node limitation | Transactional Postgres/vector store |
| No API identity boundary | Unauthorized control of runs | Documented deployment warning | OIDC, RBAC, ownership checks |
| In-process tool execution | Host compromise by arbitrary code tool | No code-execution builtin | External sandbox |

## Release evidence

Every change must pass:

```bash
make lint
make test
make evals
docker build -t relay:verify .
```

For production releases, add migration rehearsal, backup restore, image scan,
SBOM/signature verification, load tests, and live-model eval thresholds in the
deployment pipeline.
