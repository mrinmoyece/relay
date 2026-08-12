# Interview study guide

How to *own* this project in a senior/staff interview. Each section maps a component to the questions it answers, with the depth cues interviewers probe for. Study order: read the code file, run the relevant test, then rehearse the Q&A out loud.

> Golden rule: never present a feature without its failure mode and its trade-off. "It does X" is mid-level; "It does X, which breaks under Y, which I mitigated with Z at the cost of W" is staff.

---

## 1. Event sourcing (`domain/events.py`, `domain/run.py`, ADR-0001)

**Q: Why event-source agent runs? Isn't that overkill?**
Because four hard requirements collapse into one mechanism: crash recovery (replay the log), indefinite human pauses (state is durable, no process waits), audit ("why did it do that" = read the ledger), and replay debugging. A mutable row gives you none of these without bolting on an audit table that drifts.

**Q: What's the cost?**
Reads are O(events) replays — negligible at tens of events per run; snapshots are the documented fix if that grows. The real cost is discipline: the fold must be pure and exhaustive, and the event schema becomes an API (additive changes only; field changes need versioned upcasting).

**Q: Why not Temporal?**
I would use it inside a company that runs it — its event-history model is this pattern industrialized. Building it myself was the point here, and it keeps the runtime a single lightweight service. Know both answers; refusing Temporal dogmatically is a red flag, as is not knowing what it gives you (durable timers, signals, versioned workflows, a battle-tested matching engine).

**Drill:** delete a field from `LLMResponded` and watch which tests break; explain how you'd migrate instead.

## 2. Concurrency (`store/base.py`, `store/postgres.py`, ADR-0003)

**Q: Two workers pick up the same run. Walk me through it.**
Both replay to version N. Worker A appends with `expected_version=N`, wins. B's append hits the version check inside the `FOR UPDATE` critical section, gets `ConcurrencyError`, re-reads the ledger, and defers to whatever it says. Worst case is wasted compute, never corrupted state. The composite PK `(run_id, seq)` makes duplicate seqs physically impossible even if the locking logic regressed.

**Q: Why not a distributed lock?**
Fencing. A worker that pauses (GC, VM migration) past its lock expiry can still write under a lock scheme unless every write checks a fencing token — and an expected-version check IS a fencing token, enforced at the only place that matters. Locks/leases remain useful for *efficiency* (not re-doing work); that's the documented multi-node path.

**Q: How would you scale to N workers?**
Lease columns (`claimed_by`, `lease_expires_at`) on the projection; claim via conditional UPDATE; heartbeat; recovery = expired-lease scan. Correctness doesn't change — that's the payoff of putting it in the store.

## 3. Exactly-once and crash recovery (`engine/recovery.py`, ADR-0003)

**Q: Your worker dies right after calling the email API but before recording the result. What happens?**
The ledger shows `ToolCallRequested` with no result — genuinely ambiguous, and no protocol can close that window (this is the two-generals problem wearing a business suit). Relay resolves it by contract: idempotent tools re-execute; non-idempotent ones escalate to a human with full context ("may or may not have run — approve to run again, deny to skip"). The runbook tells the operator to check the mail provider's outbox first.

**Q: Couldn't you use idempotency keys?**
Yes — when the downstream API supports them, the tool author uses them and declares the tool idempotent; the contract composes. The escalation path exists for the systems that don't.

**Drill:** run `scripts/demo.py` story 3; then modify it to crash *after* `ToolSucceeded` and observe recovery does nothing (already consistent).

## 4. Safety: budgets, policy, HITL (`domain/budget.py`, `tools/policy.py`, ADR-0005, ADR-0006)

**Q: How do you stop an agent from spending $10k?**
Runtime circuit breaker, not prompt engineering: steps/tokens/USD checked before every model call; violation → terminal `BudgetExceeded` event recording exactly why. Adversarial eval: a provider scripted to loop forever halts at exactly the step budget. Honest caveat: overshoot window of one call (checked-before, not predicted) — and I can describe the pre-call estimation fix.

**Q: Prompt injection?**
Structural containment: the model can only invoke allowlisted tools (registry scoping — the tool defs aren't even advertised), destructive actions gate on a human, budgets bound the waste. Injection can still shape answer *content*; that's an application-layer validation concern above the runtime. Don't claim more than this — interviewers respect the boundary.

**Q: Why does denial not fail the run?**
The reviewer's note is surfaced to the model as a tool error so it can adapt — killing the run would discard all prior progress and retrain users to rubber-stamp approvals.

## 5. Testing & evals (`tests/`, `evals/`, docs/EVALS.md)

**Q: How do you test something as nondeterministic as an agent?**
Split the problem. The runtime is deterministic given a model script → scripted mock provider → exact assertions on behavior (tool sequences from the ledger, terminal status, recovery paths) as CI-gated evals. The *model's* quality is a separate, statistical eval (same harness, live adapter, N trials, pass-rate gates). Most candidates conflate these; separating them cleanly is a differentiator.

**Q: What's your favorite test in this repo?**
Have one. Good pick: `test_crash_with_non_idempotent_call_escalates_to_human` — it exercises event sourcing, recovery, idempotency contracts, and HITL in one scenario. Or the eval invariant that non-allowlisted tools never emit `tool_succeeded` in ANY scenario.

## 6. Memory (`memory/store.py`)

**Q: Does the agent learn?**
Precisely: the model is stateless; improvement is engineered. Three tiers — working (transcript, rebuilt by the fold), episodic (immutable ledgers), long-term (distilled lessons retrieved into future prompts). Injection happens before `RunCreated` is written, so it's auditable. Retrieval is keyword-overlap behind a protocol; embeddings/pgvector is the swap-in upgrade. Distillation is deterministic today; LLM-written lessons are the richer/costlier option.

## 7. Systems-design transfer questions

You'll be asked to design adjacent systems. Relay gives you reusable answers:
- *"Design a workflow engine / a Zapier / a CI system"* → event-sourced execution + idempotent steps + leases: same skeleton.
- *"Design an approval/review pipeline"* → durable parked states + decisions as events (ADR-0005).
- *"How do you bill for LLM usage?"* → cost as part of the provider contract, accumulated in the fold, enforced as budget.
- *"Multi-tenant agent platform?"* → per-run allowlists/policies become per-tenant; the projection gets a tenant column; budgets become quotas.

## 8. Honest "what would you do differently"

Rehearse from LIMITATIONS.md — lead with: lease columns designed in from day one, cache-aware `Usage` accounting, `PolicyDecided` events for ALLOW decisions, and the external code sandbox as the biggest prod gap. Volunteering limitations before being asked is the strongest staff signal in the interview.

## 15-minute walkthrough script (for "tell me about a project")

1. *Problem* (1 min): agent demos die in prod — crashes lose state, loops burn money, destructive actions ship unreviewed, nothing is debuggable.
2. *Core idea* (2 min): a run is a ledger; state is a fold; everything else falls out.
3. *Demo* (4 min): `make demo` — narrate the four stories, pausing on the ledger printouts.
4. *Hard part deep-dive* (5 min): pick ONE — usually crash recovery + idempotency — and go to the bottom (two-generals, contracts, human escalation).
5. *Honesty* (2 min): limitations + upgrade paths.
6. *Numbers* (1 min): 60 tests, 6/6 CI-gated behavioral evals, zero-config runnable, and 4 core dependencies.
