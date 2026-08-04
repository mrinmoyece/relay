"""End-to-end demo of every core capability - no API key, no database.

    python scripts/demo.py

Acts out four stories against the in-memory store + scripted mock model:
  1. a normal multi-step run (calculator tools -> final answer)
  2. a destructive tool parking the run for human approval, then approval
  3. a simulated crash mid-run + recovery (idempotent -> auto-resume)
  4. long-term memory influencing the next run's system prompt
"""

from __future__ import annotations

import asyncio

from relay.config import Settings
from relay.domain.budget import Budget
from relay.domain.types import ToolCallSpec
from relay.engine.loop import AgentEngine
from relay.engine.recovery import recover_interrupted_runs
from relay.llm.mock import MockLLMProvider, MockTurn
from relay.memory.store import InMemoryMemoryStore
from relay.store.memory import InMemoryEventStore
from relay.tools.builtin import calculator, make_send_email
from relay.tools.registry import ToolRegistry


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def print_ledger(store: InMemoryEventStore, run_id: str) -> None:
    print(f"\n  Event ledger for run {run_id[:8]}:")
    for r in await store.read(run_id):
        summary = ""
        ev = r.event
        if ev.type == "llm_responded":
            calls = ", ".join(tc.tool_name for tc in ev.tool_calls) or "final answer"
            summary = f"step={ev.step} -> {calls} (${ev.usage.cost_usd:.4f})"
        elif ev.type == "tool_succeeded":
            summary = f"{ev.tool_name} -> {ev.output[:40]!r}"
        elif ev.type == "tool_failed":
            summary = f"{ev.tool_name} -> {ev.error[:50]!r}"
        elif ev.type == "approval_required":
            summary = f"{ev.call.tool_name} parked: {ev.reason[:60]}"
        elif ev.type == "run_completed":
            summary = ev.final_answer[:60]
        print(f"    [{r.seq:>2}] {ev.type:<22} {summary}")


def make_engine(store, provider, memory=None) -> tuple[AgentEngine, ToolRegistry]:
    registry = ToolRegistry([calculator, make_send_email()])
    settings = Settings(database_url=None, provider="mock")
    engine = AgentEngine(
        store=store, provider=provider, registry=registry, settings=settings, memory=memory
    )
    return engine, registry


async def story_1_happy_path(memory: InMemoryMemoryStore) -> None:
    banner("STORY 1: multi-step run with tools, budgets, cost accounting")
    store = InMemoryEventStore()
    provider = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="c1", tool_name="calculator", arguments={"expression": "17*23"}
                    ),
                )
            ),
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="c2", tool_name="calculator", arguments={"expression": "391+9"}
                    ),
                )
            ),
            MockTurn(content="17*23 = 391, plus 9 gives 400."),
        ]
    )
    engine, _ = make_engine(store, provider, memory)
    run_id = await engine.create_run(
        goal="Compute 17*23 then add 9", budget=Budget(max_steps=10)
    )
    state = await engine.drive(run_id)
    print(f"\n  status={state.status.value}  steps={state.step}  cost=${state.cost_usd:.4f}")
    print(f"  answer: {state.final_answer}")
    await print_ledger(store, run_id)


async def story_2_human_approval() -> None:
    banner("STORY 2: destructive tool -> human-in-the-loop approval gate")
    store = InMemoryEventStore()
    provider = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="e1",
                        tool_name="send_email",
                        arguments={
                            "to": "cfo@example.com",
                            "subject": "Q3 report",
                            "body": "Attached.",
                        },
                    ),
                )
            ),
            MockTurn(content="Email sent to the CFO."),
        ]
    )
    engine, _ = make_engine(store, provider)
    run_id = await engine.create_run(goal="Email the Q3 report to the CFO")
    state = await engine.drive(run_id)
    assert state.pending_approval is not None
    print(f"\n  run parked: status={state.status.value}")
    print(f"  awaiting approval: {state.pending_approval.reason}")
    print("  ... human reviews in dashboard, clicks approve ...")
    state = await engine.approve(
        run_id, state.pending_approval.approval_id, approver="mrinmoy", note="looks right"
    )
    print(f"  after approval: status={state.status.value} answer={state.final_answer!r}")
    await print_ledger(store, run_id)


async def story_3_crash_recovery() -> None:
    banner("STORY 3: worker crashes mid-run -> recovery resumes from the log")
    store = InMemoryEventStore()
    # First 'process': model asks for a calculation, then the worker dies
    # after persisting the request but before executing the tool.
    provider_a = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="c1", tool_name="calculator", arguments={"expression": "6*7"}
                    ),
                )
            ),
        ]
    )
    engine_a, _ = make_engine(store, provider_a)
    run_id = await engine_a.create_run(goal="What is 6*7?")
    # Simulate the crash: advance exactly two appends (LLM turn + request),
    # then abandon the process without executing the tool.
    state = await engine_a.get_state(run_id)
    await engine_a._advance_with_model(state)  # noqa: SLF001 - simulating a partial run
    state = await engine_a.get_state(run_id)
    print(f"\n  'crashed' with status={state.status.value}, "
          f"{len(state.pending_calls)} tool call in flight")

    # Second 'process' starts up and runs recovery.
    provider_b = MockLLMProvider(script=[MockTurn(content="6*7 = 42.")])
    engine_b, registry = make_engine(store, provider_b)
    recovered = await recover_interrupted_runs(
        store=store, engine=engine_b, registry=registry
    )
    state = await engine_b.get_state(run_id)
    print(f"  recovered runs: {[r[:8] for r in recovered]}")
    print(f"  final: status={state.status.value} answer={state.final_answer!r}")
    print("  (calculator is idempotent -> re-executed automatically;")
    print("   a non-idempotent tool would have been escalated to a human)")
    await print_ledger(store, run_id)


async def story_4_memory(memory: InMemoryMemoryStore) -> None:
    banner("STORY 4: long-term memory - lessons from run 1 shape this run")
    store = InMemoryEventStore()
    provider = MockLLMProvider(script=[MockTurn(content="Reusing prior approach: 400.")])
    engine, _ = make_engine(store, provider, memory)
    run_id = await engine.create_run(goal="Compute 17*23 plus 9 again")
    state = await engine.get_state(run_id)
    injected = "<relevant_experience>" in state.system_prompt
    print(f"\n  memory injected into system prompt: {injected}")
    if injected:
        block = state.system_prompt.split("<relevant_experience>")[1][:200]
        print(f"  injected block (truncated): {block.strip()[:180]}...")
    state = await engine.drive(run_id)
    print(f"  status={state.status.value} answer={state.final_answer!r}")


async def main() -> None:
    memory = InMemoryMemoryStore()
    await story_1_happy_path(memory)
    await story_2_human_approval()
    await story_3_crash_recovery()
    await story_4_memory(memory)
    banner("All four stories completed. Read the ledgers above - that IS the system.")


if __name__ == "__main__":
    asyncio.run(main())
