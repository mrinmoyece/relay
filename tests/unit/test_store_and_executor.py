from __future__ import annotations

import asyncio

import pytest

from relay.config import Settings
from relay.domain.events import RunCreated
from relay.engine.executor import ToolExecutor
from relay.store.base import ConcurrencyError
from relay.store.memory import InMemoryEventStore
from relay.tools.base import Tool, ToolExecutionError

# ------------------------------------------------------------- event store


async def test_append_assigns_gapless_seq():
    store = InMemoryEventStore()
    r1 = await store.append(
        "r", [RunCreated(goal="g", model="m")], expected_version=0, new_status="running"
    )
    assert [x.seq for x in r1] == [1]
    r2 = await store.append(
        "r",
        [RunCreated(goal="g2", model="m"), RunCreated(goal="g3", model="m")],
        expected_version=1,
        new_status="running",
    )
    assert [x.seq for x in r2] == [2, 3]


async def test_optimistic_concurrency_rejects_stale_writer():
    store = InMemoryEventStore()
    await store.append(
        "r", [RunCreated(goal="g", model="m")], expected_version=0, new_status="running"
    )
    with pytest.raises(ConcurrencyError):
        # A second writer that read version 0 must lose.
        await store.append(
            "r", [RunCreated(goal="x", model="m")], expected_version=0, new_status="running"
        )


async def test_read_from_seq():
    store = InMemoryEventStore()
    await store.append(
        "r",
        [RunCreated(goal=str(i), model="m") for i in range(3)],
        expected_version=0,
        new_status="running",
    )
    assert [r.seq for r in await store.read("r", from_seq=1)] == [2, 3]


async def test_projection_lists_by_status():
    store = InMemoryEventStore()
    await store.append(
        "a", [RunCreated(goal="g", model="m")], expected_version=0, new_status="running"
    )
    await store.append(
        "b", [RunCreated(goal="g", model="m")], expected_version=0, new_status="completed"
    )
    assert await store.list_runs(status="running") == ["a"]
    assert set(await store.list_runs()) == {"a", "b"}


# ---------------------------------------------------------------- executor


def make_executor() -> ToolExecutor:
    settings = Settings(tool_timeout_seconds=0.2, tool_max_attempts=3)
    return ToolExecutor(settings, backoff_base_s=0.0)


async def test_retries_transient_errors_until_success():
    attempts = 0

    async def flaky(args):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolExecutionError("transient", retryable=True)
        return "ok"

    tool = Tool(name="flaky", description="", handler=flaky)
    result = await make_executor().execute(tool, {})
    assert result.ok and result.attempts == 3


async def test_permanent_errors_fail_fast_without_retry():
    attempts = 0

    async def broken(args):
        nonlocal attempts
        attempts += 1
        raise ToolExecutionError("bad arguments", retryable=False)

    tool = Tool(name="broken", description="", handler=broken)
    result = await make_executor().execute(tool, {})
    assert not result.ok
    assert attempts == 1  # no pointless retries
    assert "bad arguments" in result.output


async def test_timeout_is_enforced_and_retried():
    async def slow(args):
        await asyncio.sleep(10)
        return "never"

    tool = Tool(name="slow", description="", handler=slow, timeout_s=0.05)
    result = await make_executor().execute(tool, {})
    assert not result.ok
    assert result.attempts == 3  # timeouts are transient -> retried
    assert "timed out" in result.output


async def test_unexpected_exception_never_escapes():
    async def buggy(args):
        raise ZeroDivisionError("tool author bug")

    tool = Tool(name="buggy", description="", handler=buggy)
    result = await make_executor().execute(tool, {})
    assert not result.ok
    assert "ZeroDivisionError" in result.output
