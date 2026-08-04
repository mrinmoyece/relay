---
name: add-event
description: Add a new domain event type to Relay's event-sourced ledger safely (schema evolution, fold, tests). Use whenever a change requires recording a new kind of fact about a run.
---

# Add a domain event to Relay

Events are the API of the ledger. Old ledgers must replay forever, so this is
the most invariant-sensitive change in the repo. Additive changes only —
never rename/remove/retype fields on existing events (AGENTS.md rule 3).

## Procedure

1. **Ask first: is it a fact?** Events are past-tense facts ("ToolSucceeded"),
   not commands ("ExecuteTool"). If you're modeling an instruction, it belongs
   in engine logic, with the *outcome* recorded as an event.
2. **Define** the frozen model in `src/relay/domain/events.py` with a unique
   `type` literal; add it to the `AnyEvent` union.
3. **Extend the fold** in `domain/run.py::apply`:
   - guard with `_require(state, ev, <allowed statuses>)` — decide explicitly
     which states may receive this event.
   - keep it pure: no I/O, no clocks, no randomness; timestamps come from
     `record.recorded_at`.
   - update `transcript`/`pending_calls`/counters as needed; return via
     `replace(state, ..., **base)`.
4. **Emit** it from the engine at the correct point — before the side effect
   if it records intent, after if it records an outcome.
5. **Tests** in `tests/unit/test_run_state_machine.py`:
   - the happy-path fold behavior
   - an illegal-status application raises `InvalidTransition`
   - extend `test_replay_is_deterministic` if the event participates in a
     typical run.
6. **Round-trip check**: the event must survive
   `event_adapter.validate_python(json.loads(e.model_dump_json()))` — add to
   the serialization test if one exists, otherwise assert it in your new test.
7. **Docs**: update the state-machine diagram in README/architecture.md if a
   transition changed; note new failure modes in `docs/FAILURE_MODES.md`.

## Checklist

- [ ] past-tense fact, frozen model, in `AnyEvent` union
- [ ] fold case added with explicit `_require` guard; fold stays pure
- [ ] `InvalidTransition` test for illegal states
- [ ] JSON round-trip verified
- [ ] no changes to existing event fields
- [ ] `make lint && make test && make evals` pass
