"""Shared value types used across the domain.

These are provider-neutral: the engine and the event log never contain
Anthropic- or OpenAI-shaped payloads. Each LLM adapter translates between
this neutral format and its wire format. That keeps the event log stable
even if we swap model providers (see ADR-0004).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    """Declared blast radius of a tool. Drives the policy engine.

    READ_ONLY   - cannot mutate anything (e.g. calculator, search)
    WRITE       - mutates state that is cheap to undo (e.g. write a file)
    DESTRUCTIVE - irreversible or externally visible (e.g. send email,
                  delete records, spend money)
    """

    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolCallSpec(BaseModel):
    """A tool invocation the model asked for. Immutable."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """Provider-neutral chat message.

    role="tool" carries a tool result back to the model and must set
    tool_call_id so the provider can correlate it with the request.
    """

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCallSpec, ...] = ()
    tool_call_id: str | None = None


class Usage(BaseModel):
    """Token/cost accounting for a single LLM call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
