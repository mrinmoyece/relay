"""LLMProvider protocol - the seam between Relay and any model vendor.

The engine speaks only this interface. Adapters (anthropic.py, mock.py)
translate between Relay's neutral ChatMessage/ToolCallSpec format and each
vendor's wire format. Adding Bedrock/Azure/Gemini support means writing
one new adapter; nothing in domain/, engine/, or store/ changes (ADR-0004).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from relay.domain.types import ChatMessage, ToolCallSpec, Usage


class ToolDef(BaseModel):
    """How a tool is advertised to the model (name + JSON schema)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ModelTurn(BaseModel):
    """One completed model response, provider-neutral."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCallSpec, ...] = ()
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = ""


class ProviderError(Exception):
    """Wraps any vendor error. retryable=True -> engine may retry with
    backoff (rate limits, 5xx, timeouts); False -> fail fast (auth,
    invalid request)."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDef],
    ) -> ModelTurn:
        """One model call. Must compute Usage (tokens + USD cost) so the
        runtime can enforce budgets - cost accounting is not optional."""
        ...
