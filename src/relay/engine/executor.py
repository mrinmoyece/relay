"""Tool executor: timeout, retry with exponential backoff, classification.

The executor - not the tool - owns reliability policy:
  * per-attempt timeout (asyncio.wait_for), from the tool or engine default
  * retry only errors declared retryable (ToolExecutionError.retryable
    or a timeout), with exponential backoff
  * everything else fails fast: retrying a permanent error just burns time

The executor never raises tool errors upward as exceptions to the loop;
it returns a result object. The loop's job is to turn results into events,
and exceptions make that flow easy to get wrong.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from relay.config import Settings
from relay.observability import get_logger, span
from relay.tools.base import Tool, ToolExecutionError

log = get_logger(__name__)

_MAX_OUTPUT_CHARS = 50_000  # tool output enters model context; cap it


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    output: str  # tool output, or error text if ok=False
    attempts: int
    duration_ms: int


class ToolExecutor:
    def __init__(self, settings: Settings, *, backoff_base_s: float = 0.1) -> None:
        self._settings = settings
        self._backoff_base_s = backoff_base_s  # injectable so tests run instantly

    async def execute(self, tool: Tool, arguments: dict) -> ExecutionResult:
        timeout = tool.timeout_s or self._settings.tool_timeout_seconds
        max_attempts = self._settings.tool_max_attempts
        started = time.monotonic()
        last_error = "unknown error"

        for attempt in range(1, max_attempts + 1):
            with span("tool.execute", tool=tool.name, attempt=attempt):
                try:
                    assert tool.handler is not None, f"tool {tool.name} has no handler"
                    output = await asyncio.wait_for(tool.handler(arguments), timeout=timeout)
                    return ExecutionResult(
                        ok=True,
                        output=output[:_MAX_OUTPUT_CHARS],
                        attempts=attempt,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                except asyncio.TimeoutError:
                    last_error = f"timed out after {timeout}s"
                    retryable = True
                except ToolExecutionError as e:
                    last_error = str(e)
                    retryable = e.retryable
                except Exception as e:  # noqa: BLE001 - tool code is untrusted
                    # A bug in a tool must never crash the run loop.
                    last_error = f"{type(e).__name__}: {e}"
                    retryable = False

            log.warning(
                "tool_attempt_failed",
                extra={
                    "ctx": {
                        "tool": tool.name,
                        "attempt": attempt,
                        "retryable": retryable,
                        "error": last_error[:500],
                    }
                },
            )
            if not retryable or attempt == max_attempts:
                break
            await asyncio.sleep(min(self._backoff_base_s * (2 ** (attempt - 1)), 5.0))

        return ExecutionResult(
            ok=False,
            output=last_error[:_MAX_OUTPUT_CHARS],
            attempts=attempt,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
