"""Human-in-the-loop gates and crash recovery - the two features that only
work because state is an event log."""

from __future__ import annotations

import pytest

from relay.domain.run import RunStatus
from relay.domain.types import ToolCallSpec
from relay.engine.recovery import recover_interrupted_runs
from relay.llm.mock import MockLLMProvider, MockTurn

EMAIL_CALL = ToolCallSpec(
    call_id="e1",
    tool_name="send_email",
    arguments={"to": "cfo@example.com", "subject": "q3", "body": "report"},
)


async def test_destructive_tool_parks_then_approval_completes(make_engine):
    provider = MockLLMProvider(
        script=[MockTurn(tool_calls=(EMAIL_CALL,)), MockTurn(content="sent!")]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="email the cfo")
    state = await engine.drive(run_id)

    assert state.status == RunStatus.AWAITING_APPROVAL
    assert state.pending_approval is not None
    assert state.pending_approval.call.tool_name == "send_email"

    state = await engine.approve(
        run_id, state.pending_approval.approval_id, approver="mrinmoy"
    )
    assert state.status == RunStatus.COMPLETED
    assert state.final_answer == "sent!"
    # ledger shows the email actually executed after approval
    assert any(
        m.role == "tool" and "email sent" in m.content for m in state.transcript
    )


async def test_denial_lets_model_choose_another_path(make_engine):
    provider = MockLLMProvider(
        script=[
            MockTurn(tool_calls=(EMAIL_CALL,)),
            MockTurn(content="Understood - I drafted the email but did not send it."),
        ]
    )
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="email the cfo")
    state = await engine.drive(run_id)
    state = await engine.deny(
        run_id, state.pending_approval.approval_id, approver="mrinmoy", note="wrong recipient"
    )
    assert state.status == RunStatus.COMPLETED
    assert "did not send" in state.final_answer
    assert any("denied by human" in m.content for m in state.transcript)


async def test_approval_with_wrong_id_is_rejected(make_engine):
    provider = MockLLMProvider(script=[MockTurn(tool_calls=(EMAIL_CALL,))])
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="email")
    await engine.drive(run_id)
    with pytest.raises(ValueError):
        await engine.approve(run_id, "bogus-id", approver="x")


async def test_cancel_while_awaiting_approval(make_engine):
    provider = MockLLMProvider(script=[MockTurn(tool_calls=(EMAIL_CALL,))])
    engine = make_engine(provider)
    run_id = await engine.create_run(goal="email")
    await engine.drive(run_id)
    state = await engine.cancel(run_id, reason="operator abort")
    assert state.status == RunStatus.CANCELLED


# ---------------------------------------------------------------- recovery


async def test_crash_with_idempotent_call_resumes_automatically(make_engine, store, registry):
    calc = ToolCallSpec(call_id="c1", tool_name="calculator", arguments={"expression": "6*7"})
    # process A: persists the LLM turn + tool request, then "crashes"
    engine_a = make_engine(MockLLMProvider(script=[MockTurn(tool_calls=(calc,))]))
    run_id = await engine_a.create_run(goal="what is 6*7")
    state = await engine_a.get_state(run_id)
    await engine_a._advance_with_model(state)  # noqa: SLF001 - partial progress, then crash
    assert (await engine_a.get_state(run_id)).pending_calls  # in-flight call left behind

    # process B: fresh engine, runs startup recovery
    engine_b = make_engine(MockLLMProvider(script=[MockTurn(content="42")]))
    recovered = await recover_interrupted_runs(store=store, engine=engine_b, registry=registry)
    assert recovered == [run_id]
    state = await engine_b.get_state(run_id)
    assert state.status == RunStatus.COMPLETED
    assert state.final_answer == "42"
    # ledger records the resume
    types = [r.event.type for r in await store.read(run_id)]
    assert "run_resumed" in types


async def test_crash_with_non_idempotent_call_escalates_to_human(
    make_engine, store, registry
):
    # process A: model asked to send an email; request persisted; crash
    # BEFORE execution. Did it send? The ledger cannot know.
    engine_a = make_engine(MockLLMProvider(script=[MockTurn(tool_calls=(EMAIL_CALL,))]))
    run_id = await engine_a.create_run(
        goal="email the cfo",
        # approve WRITE/DESTRUCTIVE implicitly for this test: pretend the
        # call was already human-approved before the crash.
    )
    state = await engine_a.get_state(run_id)
    await engine_a._advance_with_model(state)  # noqa: SLF001

    # process B recovers: must NOT blindly re-send. Escalates instead.
    engine_b = make_engine(MockLLMProvider(script=[MockTurn(content="sent")]))
    await recover_interrupted_runs(store=store, engine=engine_b, registry=registry)
    state = await engine_b.get_state(run_id)
    assert state.status == RunStatus.AWAITING_APPROVAL
    assert "crash recovery" in state.pending_approval.reason

    # human says "yes, run it (again)" -> completes
    state = await engine_b.approve(
        run_id, state.pending_approval.approval_id, approver="mrinmoy"
    )
    assert state.status == RunStatus.COMPLETED


async def test_recovery_ignores_healthy_runs(make_engine, store, registry):
    engine = make_engine(MockLLMProvider(script=[MockTurn(content="done")]))
    run_id = await engine.create_run(goal="quick")
    await engine.drive(run_id)
    recovered = await recover_interrupted_runs(store=store, engine=engine, registry=registry)
    assert recovered == []
    assert (await engine.get_state(run_id)).status == RunStatus.COMPLETED
