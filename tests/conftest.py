"""Shared fixtures.

Everything runs against the in-memory store and the scripted mock
provider - the exact same code paths as production minus the network.
Settings are tuned so retry/backoff tests complete in milliseconds.
"""

from __future__ import annotations

import pytest

from relay.config import Settings
from relay.engine.executor import ToolExecutor
from relay.engine.loop import AgentEngine
from relay.llm.mock import MockLLMProvider
from relay.memory.store import InMemoryMemoryStore
from relay.store.memory import InMemoryEventStore
from relay.tools.builtin import calculator, make_send_email
from relay.tools.registry import ToolRegistry


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=None,
        provider="mock",
        tool_timeout_seconds=0.5,
        tool_max_attempts=3,
        llm_max_attempts=3,
    )


@pytest.fixture
def store() -> InMemoryEventStore:
    return InMemoryEventStore()


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([calculator, make_send_email()])


@pytest.fixture
def memory() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


@pytest.fixture
def make_engine(store, registry, settings, memory):
    """Factory: engines share the store so tests can simulate multiple
    processes (crash recovery) against one ledger."""

    def _make(provider: MockLLMProvider, *, with_memory: bool = False) -> AgentEngine:
        return AgentEngine(
            store=store,
            provider=provider,
            registry=registry,
            settings=settings,
            memory=memory if with_memory else None,
            executor=ToolExecutor(settings, backoff_base_s=0.0),
        )

    return _make
