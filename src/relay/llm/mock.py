"""Deterministic mock provider.

Not a toy: this is what makes the agent loop *testable*. Real-model tests
are flaky, slow, and cost money; the mock lets CI assert exact behavior
(exactly these tool calls, in this order, with this recovery path). The
eval harness and every integration test run on it.

Usage:
    provider = MockLLMProvider(script=[
        MockTurn(tool_calls=(ToolCallSpec(call_id="c1", tool_name="calculator",
                                          arguments={"expression": "2+2"}),)),
        MockTurn(content="The answer is 4."),
    ])

Each complete() pops the next scripted turn. Token usage is derived
deterministically from message lengths so budget logic is exercised too.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from relay.domain.types import ChatMessage, ToolCallSpec, Usage
from relay.llm.base import ModelTurn, ProviderError, ToolDef


class MockTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCallSpec, ...] = ()
    # Simulate provider failures: raise before returning, N times.
    fail_times: int = 0
    fail_retryable: bool = True


_COST_PER_TOKEN = 0.000002  # fake but stable pricing for budget tests


class MockLLMProvider:
    def __init__(self, script: Sequence[MockTurn] | None = None) -> None:
        self._script = list(script or [])
        self._cursor = 0
        self._fail_counts: dict[int, int] = {}
        self.calls = 0  # observability for tests

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
    ) -> ModelTurn:
        self.calls += 1
        if self._cursor >= len(self._script):
            # Sensible default: finish the run. Prevents accidental
            # infinite loops when a script is shorter than the run.
            turn = MockTurn(content="Done (mock default answer).")
        else:
            turn = self._script[self._cursor]

        failed_so_far = self._fail_counts.get(self._cursor, 0)
        if failed_so_far < turn.fail_times:
            self._fail_counts[self._cursor] = failed_so_far + 1
            raise ProviderError(
                f"mock provider failure {failed_so_far + 1}/{turn.fail_times}",
                retryable=turn.fail_retryable,
            )

        self._cursor += 1
        input_tokens = sum(len(m.content) for m in messages) // 4 + 10
        output_tokens = (len(turn.content) // 4) + 5 * len(turn.tool_calls) + 5
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round((input_tokens + output_tokens) * _COST_PER_TOKEN, 6),
        )
        return ModelTurn(
            content=turn.content,
            tool_calls=turn.tool_calls,
            usage=usage,
            stop_reason="tool_use" if turn.tool_calls else "end_turn",
        )


def looping_tool_call(tool_name: str, arguments: dict) -> MockLLMProvider:
    """A provider that requests the same tool forever - used in tests to
    prove the step budget actually halts runaway agents."""
    counter = itertools.count()

    class _Looping(MockLLMProvider):
        async def complete(self, *, model, messages, tools):  # type: ignore[override]
            self.calls += 1
            n = next(counter)
            return ModelTurn(
                tool_calls=(
                    ToolCallSpec(call_id=f"loop-{n}", tool_name=tool_name, arguments=arguments),
                ),
                usage=Usage(input_tokens=50, output_tokens=10, cost_usd=0.0001),
                stop_reason="tool_use",
            )

    return _Looping()
