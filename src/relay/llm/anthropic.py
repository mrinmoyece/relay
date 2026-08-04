"""Anthropic adapter.

Translates Relay's neutral message format <-> the Anthropic Messages API.
All Anthropic-specific shapes (content blocks, tool_use/tool_result,
system-as-top-level-param) live ONLY in this file.

Cost is computed here from a static price table. In production you would
source prices from config so a price change doesn't need a deploy - noted
in LIMITATIONS.md.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from relay.domain.types import ChatMessage, ToolCallSpec, Usage
from relay.llm.base import ModelTurn, ProviderError, ToolDef

try:  # optional dependency: pip install "relay-runtime[anthropic]"
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

# USD per token (input, output). Keyed by model prefix, longest match wins.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (15 / 1e6, 75 / 1e6),
    "claude-sonnet": (3 / 1e6, 15 / 1e6),
    "claude-haiku": (0.80 / 1e6, 4 / 1e6),
}


def _price(model: str) -> tuple[float, float]:
    for prefix in sorted(_PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return _PRICES[prefix]
    return _PRICES["claude-sonnet"]  # conservative default


class AnthropicProvider:
    def __init__(self, api_key: str | None = None, max_output_tokens: int = 4096) -> None:
        if anthropic is None:  # pragma: no cover
            raise RuntimeError(
                "Anthropic support requires: pip install 'relay-runtime[anthropic]'"
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._max_output_tokens = max_output_tokens

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
    ) -> ModelTurn:
        system, api_messages = _to_anthropic(messages)
        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=self._max_output_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=api_messages,
                tools=[
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.parameters,
                    }
                    for t in tools
                ]
                or anthropic.NOT_GIVEN,
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(f"rate limited: {e}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(
                f"API error {e.status_code}: {e}", retryable=e.status_code >= 500
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"connection error: {e}", retryable=True) from e

        content_parts: list[str] = []
        tool_calls: list[ToolCallSpec] = []
        for block in resp.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallSpec(
                        call_id=block.id,
                        tool_name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )

        in_price, out_price = _price(model)
        usage = Usage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cost_usd=round(
                resp.usage.input_tokens * in_price + resp.usage.output_tokens * out_price, 6
            ),
        )
        return ModelTurn(
            content="\n".join(content_parts),
            tool_calls=tuple(tool_calls),
            usage=usage,
            stop_reason=resp.stop_reason or "",
        )


def _to_anthropic(
    messages: Sequence[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Neutral transcript -> (system_prompt, Anthropic messages).

    Anthropic requires tool results as user-role tool_result blocks that
    immediately follow the assistant tool_use turn; consecutive tool
    messages are merged into one user turn.
    """
    system = ""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system = m.content
        elif m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.call_id,
                        "name": tc.tool_name,
                        "input": tc.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks or m.content})
        elif m.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id,
                "content": m.content,
                "is_error": m.content.startswith("ERROR:"),
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return system, out


def format_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True)
