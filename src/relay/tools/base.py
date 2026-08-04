"""Tool contract.

A tool declares, up front, the three properties the runtime needs to run
it safely WITHOUT trusting the tool author's code to behave:

  risk        - blast radius (drives policy: allow / approve / deny)
  idempotent  - safe to re-execute? (drives crash recovery: an idempotent
                call that may or may not have run is simply re-run; a
                non-idempotent one is escalated to a human)
  timeout_s   - per-attempt wall-clock cap (enforced by the executor,
                not by the tool)

The handler is an async callable receiving validated arguments and
returning a string for the model. Exceptions are classified by the
executor into retryable vs fatal via ToolExecutionError.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from relay.domain.types import RiskLevel
from relay.llm.base import ToolDef

Handler = Callable[[dict[str, Any]], Awaitable[str]]


class ToolExecutionError(Exception):
    """Raise from a handler to control retry behavior explicitly.

    retryable=True  -> transient (network blip, lock contention): executor
                       retries with exponential backoff up to max attempts.
    retryable=False -> permanent (bad arguments, missing resource): fail
                       immediately, surface the error to the model.
    """

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    risk: RiskLevel = RiskLevel.READ_ONLY
    idempotent: bool = True
    timeout_s: float | None = None  # None -> engine default
    handler: Handler | None = None

    def to_def(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=self.parameters)
