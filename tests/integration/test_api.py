"""API tests over the real ASGI app with a scripted engine underneath."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from relay.api.app import create_app
from relay.config import Settings
from relay.domain.types import ToolCallSpec
from relay.engine.executor import ToolExecutor
from relay.engine.loop import AgentEngine
from relay.engine.manager import RunManager
from relay.llm.mock import MockLLMProvider, MockTurn
from relay.store.memory import InMemoryEventStore
from relay.tools.builtin import calculator, make_send_email
from relay.tools.registry import ToolRegistry


def build_manager(script) -> RunManager:
    store = InMemoryEventStore()
    registry = ToolRegistry([calculator, make_send_email()])
    settings = Settings(database_url=None, provider="mock")
    engine = AgentEngine(
        store=store,
        provider=MockLLMProvider(script=script),
        registry=registry,
        settings=settings,
        executor=ToolExecutor(settings, backoff_base_s=0.0),
    )
    return RunManager(engine=engine, store=store, registry=registry)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def client_for(manager: RunManager):
    app = create_app(manager)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), app


async def wait_for_terminal(client: httpx.AsyncClient, run_id: str, timeout: float = 5.0):
    """Poll until the background task finishes - the API is async by design."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/v1/runs/{run_id}")
        body = resp.json()
        if body["status"] not in ("running", "pending"):
            return body
        await asyncio.sleep(0.02)
    raise TimeoutError("run did not settle")


async def test_create_poll_complete_and_ledger():
    manager = build_manager(
        [
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="c1", tool_name="calculator", arguments={"expression": "2+2"}
                    ),
                )
            ),
            MockTurn(content="4"),
        ]
    )
    client, app = await client_for(manager)
    async with client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/v1/runs", json={"goal": "what is 2+2"})
            assert resp.status_code == 202
            run_id = resp.json()["run_id"]

            body = await wait_for_terminal(client, run_id)
            assert body["status"] == "completed"
            assert body["final_answer"] == "4"
            assert body["cost_usd"] > 0

            events = (await client.get(f"/v1/runs/{run_id}/events")).json()
            types = [e["type"] for e in events]
            assert types[0] == "run_created" and types[-1] == "run_completed"
            assert "tool_succeeded" in types


async def test_approval_over_http():
    manager = build_manager(
        [
            MockTurn(
                tool_calls=(
                    ToolCallSpec(
                        call_id="e1",
                        tool_name="send_email",
                        arguments={"to": "a@b.co", "subject": "s", "body": "b"},
                    ),
                )
            ),
            MockTurn(content="sent"),
        ]
    )
    client, app = await client_for(manager)
    async with client:
        async with app.router.lifespan_context(app):
            run_id = (await client.post("/v1/runs", json={"goal": "email"})).json()["run_id"]
            body = await wait_for_terminal(client, run_id)
            assert body["status"] == "awaiting_approval"
            approval = body["pending_approval"]
            assert approval["tool_name"] == "send_email"

            resp = await client.post(
                f"/v1/runs/{run_id}/approvals/{approval['approval_id']}",
                json={"decision": "approve", "approver": "mrinmoy"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

            # stale approval id now conflicts
            resp = await client.post(
                f"/v1/runs/{run_id}/approvals/{approval['approval_id']}",
                json={"decision": "approve", "approver": "mrinmoy"},
            )
            assert resp.status_code == 409


async def test_unknown_run_is_404_and_cancel_works():
    manager = build_manager([MockTurn(content="hi")])
    client, app = await client_for(manager)
    async with client:
        async with app.router.lifespan_context(app):
            assert (await client.get("/v1/runs/nope")).status_code == 404
            assert (await client.post("/v1/runs/nope/cancel")).status_code == 404
            assert (
                await client.post(
                    "/v1/runs/nope/approvals/nope",
                    json={"decision": "approve", "approver": "operator"},
                )
            ).status_code == 404
            assert (await client.get("/readyz")).status_code == 200

            run_id = (await client.post("/v1/runs", json={"goal": "g"})).json()["run_id"]
            await wait_for_terminal(client, run_id)
            resp = await client.post(f"/v1/runs/{run_id}/cancel")
            # already completed -> cancel is a no-op returning current state
            assert resp.json()["status"] == "completed"


async def test_readiness_returns_503_when_event_store_is_unavailable():
    class FailingReadStore(InMemoryEventStore):
        async def read(self, run_id: str, *, from_seq: int = 0):
            raise RuntimeError("database unavailable")

    manager = build_manager([])
    manager._store = FailingReadStore()  # noqa: SLF001 - inject dependency failure
    client, app = await client_for(manager)
    async with client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            assert resp.json() == {"detail": "event store unavailable"}
