# ADR-0005: Human approval as a durable state, not a blocking call

Status: accepted

## Context
Destructive tool calls (send email, move money, delete data) need human review. The naive implementation — block the agent process on a callback/webhook while a human decides — couples run liveness to process liveness and to human response time.

## Decision
Approval is modeled as run state. When policy demands review, the engine appends `ApprovalRequired` and stops driving; the run's status is `AWAITING_APPROVAL`, durably, with the full pending call (tool, arguments, risk, reason) in the ledger. Any process, minutes or weeks later, can append `ApprovalGranted`/`ApprovalDenied` via the API and resume the loop. No process waits on a human. Design details:
- **Denial is feedback, not failure.** A denied call is surfaced to the model as a tool error with the reviewer's note; the agent can choose another approach. Killing the run on denial would waste all prior progress.
- **Approval is call-specific.** The grant marks exactly one pending call as human-approved; the policy engine skips re-checking only that call. A model that mutates its request gets a fresh policy decision.
- **Approval identity is recorded** (approver, note, timestamp) in the ledger — the audit story for "who authorized this" is the same event log as everything else.

## Alternatives considered
- **Blocking waits with timeouts** — couples liveness, loses the approval on crash, forces arbitrary timeout policy.
- **Out-of-band approval queues (separate table/service)** — splits run truth across two stores; the ledger no longer tells the whole story.

## Consequences
Approvals survive restarts and deploys for free. UI/notification layers (Slack ping, dashboard) are pure consumers of `ApprovalRequired` events. Cost: approval latency is human latency; runs can park indefinitely (an operational alert on old `AWAITING_APPROVAL` runs is the mitigation — see RUNBOOK.md).
