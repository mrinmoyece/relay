"""Eval harness: run every scenario, assert behavior, report, gate CI.

    python -m evals.run_evals

Exit code 0 only if every scenario passes - CI treats agent behavior as a
first-class regression surface. Results are also written to
evals/results.md so the latest run is visible in the repo.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from evals.scenarios import SCENARIOS, Scenario, normalize
from relay.config import Settings
from relay.engine.executor import ToolExecutor
from relay.engine.loop import AgentEngine
from relay.llm.mock import MockLLMProvider, looping_tool_call
from relay.store.memory import InMemoryEventStore
from relay.tools.builtin import calculator, make_send_email
from relay.tools.registry import ToolRegistry


async def run_scenario(sc: Scenario) -> list[str]:
    """Returns a list of failure messages (empty = pass)."""
    store = InMemoryEventStore()
    registry = ToolRegistry([calculator, make_send_email()])
    settings = Settings(database_url=None, provider="mock")
    provider = (
        looping_tool_call("calculator", {"expression": "1+1"})
        if sc.loop_forever
        else MockLLMProvider(script=[normalize(t) for t in sc.script])
    )
    engine = AgentEngine(
        store=store,
        provider=provider,
        registry=registry,
        settings=settings,
        executor=ToolExecutor(settings, backoff_base_s=0.0),
    )

    run_id = await engine.create_run(
        goal=sc.goal, budget=sc.budget, allowed_tools=sc.allowed_tools
    )
    state = await engine.drive(run_id)

    # Simulate the human where the scenario expects one.
    approvals = 0
    while state.status.value == "awaiting_approval" and sc.auto_approve and approvals < 5:
        state = await engine.approve(
            run_id, state.pending_approval.approval_id, approver="eval-harness"
        )
        approvals += 1

    failures: list[str] = []
    if state.status.value != sc.expected_status:
        failures.append(f"status: expected {sc.expected_status}, got {state.status.value}")
    if sc.answer_contains and sc.answer_contains.lower() not in state.final_answer.lower():
        failures.append(
            f"answer: expected to contain {sc.answer_contains!r}, got {state.final_answer!r}"
        )
    if sc.max_steps is not None and state.step > sc.max_steps:
        failures.append(f"efficiency: took {state.step} steps, budgeted {sc.max_steps}")

    if sc.expected_tool_sequence is not None:
        requested = [
            r.event.call.tool_name
            for r in await store.read(run_id)
            if r.event.type == "tool_call_requested"
        ]
        if tuple(requested) != sc.expected_tool_sequence:
            failures.append(
                f"tools: expected {sc.expected_tool_sequence}, got {tuple(requested)}"
            )

    # Universal invariant: allowlisted-out tools must never SUCCEED.
    if sc.allowed_tools:
        succeeded = [
            r.event.tool_name
            for r in await store.read(run_id)
            if r.event.type == "tool_succeeded"
        ]
        illegal = [t for t in succeeded if t not in sc.allowed_tools]
        if illegal:
            failures.append(f"SECURITY: non-allowlisted tools executed: {illegal}")

    # Universal concurrency invariant: every successful side effect must have
    # won a persisted execution claim first.
    records = await store.read(run_id)
    claimed: set[str] = set()
    for record in records:
        if record.event.type == "tool_execution_started":
            claimed.add(record.event.call_id)
        elif record.event.type == "tool_succeeded" and record.event.call_id not in claimed:
            failures.append(
                f"CONCURRENCY: tool {record.event.call_id!r} succeeded without a claim"
            )
        elif (
            record.event.type == "tool_failed"
            and record.event.attempts > 0
            and record.event.call_id not in claimed
        ):
            failures.append(
                f"CONCURRENCY: tool {record.event.call_id!r} executed without a claim"
            )

    return failures


async def main() -> int:
    started = time.monotonic()
    rows: list[tuple[str, bool, list[str]]] = []
    for sc in SCENARIOS:
        failures = await run_scenario(sc)
        rows.append((sc.name, not failures, failures))

    passed = sum(1 for _, ok, _ in rows if ok)
    total = len(rows)
    elapsed = time.monotonic() - started

    print(f"\nRelay behavioral evals: {passed}/{total} passed in {elapsed:.2f}s\n")
    lines = [
        "# Eval results",
        "",
        "Behavioral regression suite over the agent runtime (scripted mock provider,",
        "so results are exact and deterministic). Regenerate: `make evals`.",
        "",
        f"**{passed}/{total} scenarios passed** ({elapsed:.2f}s)",
        "",
        "| Scenario | Result | Notes |",
        "|---|---|---|",
    ]
    for (name, ok, failures), sc in zip(rows, SCENARIOS, strict=True):
        mark = "PASS" if ok else "FAIL"
        note = sc.description if ok else "; ".join(failures)
        print(f"  [{mark}] {name:<35} {note if not ok else ''}")
        lines.append(f"| `{name}` | {mark} | {note} |")

    Path(__file__).with_name("results.md").write_text("\n".join(lines) + "\n")
    print("\nresults written to evals/results.md")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
