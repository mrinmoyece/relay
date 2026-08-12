"""Whole-loop tests: engine + store + executor + policy, only the model
is scripted. These assert *behavior*, not implementation."""

from __future__ import annotations

import asyncio

from relay.config import Settings
from relay.domain.budget import Budget
from relay.domain.events import ToolExecutionStarted
from relay.domain.run import RunStatus
from relay.domain.types import ToolCallSpec
from relay.engine.loop import AgentEngine
from relay.llm.mock import MockLLMProvider, MockTurn, looping_tool_call
from relay.tools.base import Tool
from relay.tools.registry import ToolRegistry


def call(cid: str, expr: str) -> ToolCallSpec:
    return ToolCallSpec(call_id=cid, tool_name="calculator", arguments={"expression": expr})


async def test_multi_step_run_completes_with_cost_accounting(make_engine):
    provider = MockLLMProvider(
        script=[
            MockTurn(tool_calls=(call("c1", "17*23"),)),
            MockTurn(tool_calls=(call("c2", "391+9"),)),
            MockTurn(content="The answer is 400."),
        ]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="compute 17*23+9")
    state = await engine.drive(run_id)

    assert state.status == RunStatus.COMPLETED
    assert state.final_answer == "The answer is 400."
    assert state.step == 3
    assert state.tokens_used > 0 and state.cost_usd > 0
    # the tool results actually reached the model's transcript
    tool_msgs = [m for m in state.transcript if m.role == "tool"]
    assert [m.content for m in tool_msgs] == ["391", "400"]


async def test_tool_error_is_surfaced_and_model_recovers(make_engine):
    provider = MockLLMProvider(
        script=[
            MockTurn(tool_calls=(call("c1", "1 +"),)),  # syntax error
            MockTurn(tool_calls=(call("c2", "1+1"),)),  # model retries correctly
            MockTurn(content="2"),
        ]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="add")
    state = await engine.drive(run_id)

    assert state.status == RunStatus.COMPLETED
    errors = [m for m in state.transcript if m.role == "tool" and m.content.startswith("ERROR:")]
    assert len(errors) == 1  # the failure is in the ledger, not swallowed


async def test_unknown_tool_does_not_crash_the_run(make_engine):
    provider = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(call_id="c1", tool_name="rm_rf_slash", arguments={}),
                )
            ),
            MockTurn(content="okay, without that tool then"),
        ]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="hack")
    state = await engine.drive(run_id)
    assert state.status == RunStatus.COMPLETED
    assert any("unknown or not-permitted" in m.content for m in state.transcript)


async def test_step_budget_halts_a_looping_agent(make_engine):
    provider = looping_tool_call("calculator", {"expression": "1+1"})
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="loop forever", budget=Budget(max_steps=4))
    state = await engine.drive(run_id)

    assert state.status == RunStatus.BUDGET_EXCEEDED
    assert state.step == 4  # exactly the budget, not one more
    assert "steps" in state.error


async def test_allowlist_blocks_tools_outside_scope(make_engine):
    provider = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="c1",
                        tool_name="send_email",
                        arguments={"to": "a@b.c", "subject": "s", "body": "b"},
                    ),
                )
            ),
            MockTurn(content="fine"),
        ]
    )
    engine = make_engine(provider)
    # run may ONLY use the calculator
    run_id = await engine.create_run(goal="email", allowed_tools=("calculator",))
    state = await engine.drive(run_id)
    assert state.status == RunStatus.COMPLETED
    assert any("unknown or not-permitted" in m.content for m in state.transcript)


async def test_retryable_provider_error_is_retried(make_engine):
    provider = MockLLMProvider(
        script=[MockTurn(content="recovered", fail_times=2, fail_retryable=True)]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="x")
    state = await engine.drive(run_id)
    assert state.status == RunStatus.COMPLETED
    assert provider.calls == 3  # 2 failures + 1 success


async def test_non_retryable_provider_error_fails_run(make_engine):
    provider = MockLLMProvider(
        script=[MockTurn(content="never", fail_times=5, fail_retryable=False)]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="x")
    state = await engine.drive(run_id)
    assert state.status == RunStatus.FAILED
    assert "provider_error" in state.error
    assert provider.calls == 1  # failed fast


async def test_provider_timeout_is_bounded_and_recorded(store):
    class HangingProvider:
        async def complete(self, *, model, messages, tools):
            await asyncio.sleep(10)

    settings = Settings(
        llm_timeout_seconds=0.01,
        llm_max_attempts=2,
        llm_retry_backoff_seconds=0,
    )
    engine = AgentEngine(
        store=store,
        provider=HangingProvider(),
        registry=ToolRegistry(),
        settings=settings,
    )
    run_id = await engine.create_run(goal="never return")
    state = await engine.drive(run_id)
    assert state.status == RunStatus.FAILED
    assert "timed out" in state.error


async def test_unexpected_provider_exception_becomes_recorded_failure(store):
    class BuggyProvider:
        async def complete(self, *, model, messages, tools):
            raise TypeError("adapter implementation bug")

    engine = AgentEngine(
        store=store,
        provider=BuggyProvider(),
        registry=ToolRegistry(),
    )
    run_id = await engine.create_run(goal="contain provider bugs")
    state = await engine.drive(run_id)
    assert state.status == RunStatus.FAILED
    assert state.error == "provider_error: unexpected provider error: TypeError"
    assert [record.event.type for record in await store.read(run_id)][-1] == "run_failed"


async def test_persisted_execution_claim_fences_second_driver(store):
    executions = 0

    async def handler(arguments):
        nonlocal executions
        executions += 1
        return "done"

    tool = Tool(name="counted", description="", handler=handler)
    registry = ToolRegistry([tool])
    provider = MockLLMProvider(
        script=[
            MockTurn(
                tool_calls=(
                    ToolCallSpec(call_id="c1", tool_name="counted", arguments={}),
                )
            )
        ]
    )
    engine = AgentEngine(store=store, provider=provider, registry=registry)
    run_id = await engine.create_run(goal="claim")
    state = await engine.get_state(run_id)
    await engine._advance_with_model(state)  # noqa: SLF001
    state = await engine.get_state(run_id)
    await engine._append(  # noqa: SLF001
        state,
        [ToolExecutionStarted(call_id="c1", tool_name="counted")],
    )

    parked = await engine.drive(run_id)
    assert parked.pending_calls[0].execution_started
    assert executions == 0


async def test_completed_run_writes_long_term_memory(make_engine, memory):
    provider = MockLLMProvider(
        script=[
            MockTurn(tool_calls=(call("c1", "6*7"),)),
            MockTurn(content="42"),
        ]
    )
    engine = make_engine(provider, with_memory=True)
    run_id = await engine.create_run(goal="multiply six by seven")
    await engine.drive(run_id)

    hits = await memory.search("multiply seven")
    assert len(hits) == 1
    assert "calculator" in hits[0].lessons

    # ...and a NEW run on a similar goal gets those lessons injected.
    engine2 = make_engine(MockLLMProvider(script=[MockTurn(content="42")]), with_memory=True)
    run2 = await engine2.create_run(goal="multiply six by seven again")
    state2 = await engine2.get_state(run2)
    assert "<relevant_experience>" in state2.system_prompt
