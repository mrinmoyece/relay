"""Behavioral eval scenarios.

Each scenario scripts the model and asserts on OBSERVABLE RUN BEHAVIOR:
terminal status, tool-call sequence, answer content, step count. This is
regression testing for agent behavior - if a change to the loop, policy,
or recovery logic alters what the agent *does*, a scenario fails and CI
blocks the merge.

With a scripted mock these evals are exact. Against a real model you would
run each scenario N times and gate on pass-rate (see docs/EVALS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from relay.domain.budget import Budget
from relay.domain.types import ToolCallSpec
from relay.llm.mock import MockTurn


def calc(cid: str, expr: str) -> ToolCallSpec:
    return ToolCallSpec(call_id=cid, tool_name="calculator", arguments={"expression": expr})


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    goal: str
    script: tuple[MockTurn, ...]
    expected_status: str
    budget: Budget = field(default_factory=Budget)
    allowed_tools: tuple[str, ...] = ()
    answer_contains: str = ""
    expected_tool_sequence: tuple[str, ...] | None = None
    max_steps: int | None = None
    auto_approve: bool = False  # simulate a human approving when parked
    loop_forever: bool = False  # use the runaway provider instead of script


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="single_tool_math",
        description="One tool call then a grounded final answer",
        goal="What is 17*23?",
        script=(calc("c1", "17*23"), MockTurn(content="17*23 = 391")),
        expected_status="completed",
        answer_contains="391",
        expected_tool_sequence=("calculator",),
        max_steps=2,
    ),
    Scenario(
        name="multi_step_chaining",
        description="Result of tool 1 feeds tool 2",
        goal="Compute 17*23 then add 9",
        script=(
            calc("c1", "17*23"),
            calc("c2", "391+9"),
            MockTurn(content="The total is 400."),
        ),
        expected_status="completed",
        answer_contains="400",
        expected_tool_sequence=("calculator", "calculator"),
        max_steps=3,
    ),
    Scenario(
        name="recovers_from_bad_tool_call",
        description="Tool error is surfaced; agent adapts instead of dying",
        goal="Add one and one",
        script=(
            calc("c1", "1 +"),  # invalid expression -> ERROR surfaced
            calc("c2", "1+1"),
            MockTurn(content="The answer is 2."),
        ),
        expected_status="completed",
        answer_contains="2",
        expected_tool_sequence=("calculator", "calculator"),
    ),
    Scenario(
        name="runaway_loop_is_halted",
        description="Step budget stops an agent stuck in a tool loop",
        goal="Loop forever",
        script=(),
        loop_forever=True,
        budget=Budget(max_steps=5),
        expected_status="budget_exceeded",
        max_steps=5,
    ),
    Scenario(
        name="destructive_tool_requires_human",
        description="send_email parks the run; approval completes it",
        goal="Email the CFO the Q3 numbers",
        script=(
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="e1",
                        tool_name="send_email",
                        arguments={"to": "cfo@x.co", "subject": "Q3", "body": "numbers"},
                    ),
                )
            ),
            MockTurn(content="Email delivered."),
        ),
        expected_status="completed",
        answer_contains="delivered",
        expected_tool_sequence=("send_email",),
        auto_approve=True,
    ),
    Scenario(
        name="tool_allowlist_enforced",
        description="A tool outside the run's allowlist cannot execute",
        goal="Send an email (but only calculator is allowed)",
        script=(
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="e1",
                        tool_name="send_email",
                        arguments={"to": "a@b.c", "subject": "s", "body": "b"},
                    ),
                )
            ),
            MockTurn(content="I could not send the email; the tool is unavailable."),
        ),
        allowed_tools=("calculator",),
        expected_status="completed",
        answer_contains="could not",
        expected_tool_sequence=("send_email",),  # requested, but must NOT succeed
    ),
)


def normalize(turn) -> MockTurn:
    """Scenarios may list a bare ToolCallSpec for brevity."""
    if isinstance(turn, ToolCallSpec):
        return MockTurn(tool_calls=(turn,))
    return turn
