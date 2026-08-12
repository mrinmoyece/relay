"""The fold is pure, so these tests need no mocks, no async, no I/O.
If this file is green, the core invariants of the whole system hold."""

from __future__ import annotations

import json

import pytest

from relay.domain.budget import Budget
from relay.domain.events import (
    ApprovalDenied,
    ApprovalGranted,
    ApprovalRequired,
    BudgetExceeded,
    EventRecord,
    LLMResponded,
    RunCancelled,
    RunCompleted,
    RunCreated,
    ToolCallRequested,
    ToolExecutionStarted,
    ToolSucceeded,
    event_adapter,
    utcnow,
)
from relay.domain.run import InvalidTransition, RunState, RunStatus, apply, replay
from relay.domain.types import RiskLevel, ToolCallSpec, Usage

RUN = "r1"


def rec(seq: int, event) -> EventRecord:
    return EventRecord(run_id=RUN, seq=seq, recorded_at=utcnow(), event=event)


def created(**kw) -> RunCreated:
    defaults = dict(goal="test goal", model="mock", system_prompt="be helpful")
    defaults.update(kw)
    return RunCreated(**defaults)


def test_created_builds_transcript_and_runs():
    state = apply(RunState(run_id=RUN), rec(1, created()))
    assert state.status == RunStatus.RUNNING
    assert [m.role for m in state.transcript] == ["system", "user"]
    assert state.transcript[1].content == "test goal"
    assert state.last_seq == 1


def test_llm_response_accumulates_tokens_and_cost():
    state = apply(RunState(run_id=RUN), rec(1, created()))
    usage = Usage(input_tokens=100, output_tokens=50, cost_usd=0.01)
    state = apply(state, rec(2, LLMResponded(step=1, content="thinking", usage=usage)))
    state = apply(state, rec(3, LLMResponded(step=2, content="more", usage=usage)))
    assert state.tokens_used == 300
    assert state.cost_usd == pytest.approx(0.02)
    assert state.step == 2


def test_tool_lifecycle_appends_result_to_transcript():
    call = ToolCallSpec(call_id="c1", tool_name="calculator", arguments={"expression": "1+1"})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call,))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.READ_ONLY)))
    assert len(state.pending_calls) == 1
    started = ToolExecutionStarted(call_id="c1", tool_name="calculator")
    state = apply(state, rec(4, started))
    assert state.pending_calls[0].execution_started
    state = apply(state, rec(5, ToolSucceeded(call_id="c1", tool_name="calculator", output="2")))
    assert state.pending_calls == ()
    assert state.transcript[-1].role == "tool"
    assert state.transcript[-1].content == "2"
    assert state.transcript[-1].tool_call_id == "c1"
    assert event_adapter.validate_python(json.loads(started.model_dump_json())) == started


def test_legacy_tool_result_without_execution_claim_still_replays():
    call = ToolCallSpec(call_id="c1", tool_name="calculator", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, ToolCallRequested(step=1, call=call, risk=RiskLevel.READ_ONLY)))
    state = apply(
        state,
        rec(3, ToolSucceeded(call_id="c1", tool_name="calculator", output="2")),
    )
    assert state.pending_calls == ()
    assert state.transcript[-1].content == "2"


def test_legacy_duplicate_pending_call_ids_still_replay():
    call = ToolCallSpec(call_id="duplicate", tool_name="calculator", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call, call))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.READ_ONLY)))
    state = apply(state, rec(4, ToolCallRequested(step=1, call=call, risk=RiskLevel.READ_ONLY)))
    assert len(state.pending_calls) == 2


def test_approval_flow_parks_and_resumes():
    call = ToolCallSpec(call_id="c1", tool_name="send_email", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call,))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.DESTRUCTIVE)))
    state = apply(
        state,
        rec(
            4,
            ApprovalRequired(
                approval_id="a1", call=call, risk=RiskLevel.DESTRUCTIVE, reason="x"
            ),
        ),
    )
    assert state.status == RunStatus.AWAITING_APPROVAL
    state = apply(
        state, rec(5, ApprovalGranted(approval_id="a1", call_id="c1", approver="human"))
    )
    assert state.status == RunStatus.RUNNING
    assert state.pending_calls[0].approved is True


def test_denial_surfaces_error_to_model_and_continues():
    call = ToolCallSpec(call_id="c1", tool_name="send_email", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call,))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.DESTRUCTIVE)))
    state = apply(
        state,
        rec(
            4,
            ApprovalRequired(
                approval_id="a1", call=call, risk=RiskLevel.DESTRUCTIVE, reason="x"
            ),
        ),
    )
    state = apply(
        state, rec(5, ApprovalDenied(approval_id="a1", call_id="c1", approver="human", note="no"))
    )
    assert state.status == RunStatus.RUNNING  # denial does NOT fail the run
    assert state.pending_calls == ()
    assert state.transcript[-1].content.startswith("ERROR: tool call denied")


def test_wrong_approval_id_is_a_hard_error():
    call = ToolCallSpec(call_id="c1", tool_name="send_email", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call,))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.DESTRUCTIVE)))
    state = apply(
        state,
        rec(
            4,
            ApprovalRequired(
                approval_id="a1", call=call, risk=RiskLevel.DESTRUCTIVE, reason="x"
            ),
        ),
    )
    with pytest.raises(InvalidTransition):
        apply(state, rec(5, ApprovalGranted(approval_id="WRONG", call_id="c1", approver="h")))


def test_terminal_states_reject_further_events():
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, content="done")))
    state = apply(state, rec(3, RunCompleted(final_answer="done")))
    assert state.status.is_terminal
    with pytest.raises(InvalidTransition):
        apply(state, rec(4, LLMResponded(step=2, content="zombie")))


def test_budget_exceeded_is_terminal_with_reason():
    state = apply(RunState(run_id=RUN), rec(1, created(budget=Budget(max_steps=1))))
    state = apply(state, rec(2, BudgetExceeded(budget_kind="steps", limit=1, used=1)))
    assert state.status == RunStatus.BUDGET_EXCEEDED
    assert "steps" in state.error


def test_cancel_from_awaiting_approval():
    call = ToolCallSpec(call_id="c1", tool_name="send_email", arguments={})
    state = apply(RunState(run_id=RUN), rec(1, created()))
    state = apply(state, rec(2, LLMResponded(step=1, tool_calls=(call,))))
    state = apply(state, rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.DESTRUCTIVE)))
    state = apply(
        state,
        rec(
            4,
            ApprovalRequired(
                approval_id="a1", call=call, risk=RiskLevel.DESTRUCTIVE, reason="x"
            ),
        ),
    )
    state = apply(state, rec(5, RunCancelled(reason="operator")))
    assert state.status == RunStatus.CANCELLED


def test_replay_is_deterministic():
    call = ToolCallSpec(call_id="c1", tool_name="calculator", arguments={"expression": "1"})
    records = [
        rec(1, created()),
        rec(2, LLMResponded(step=1, tool_calls=(call,))),
        rec(3, ToolCallRequested(step=1, call=call, risk=RiskLevel.READ_ONLY)),
        rec(4, ToolExecutionStarted(call_id="c1", tool_name="calculator")),
        rec(5, ToolSucceeded(call_id="c1", tool_name="calculator", output="1")),
        rec(6, LLMResponded(step=2, content="1")),
        rec(7, RunCompleted(final_answer="1")),
    ]
    a, b = replay(RUN, records), replay(RUN, records)
    assert a == b
    assert a.status == RunStatus.COMPLETED
