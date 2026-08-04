"""GET /metrics over the real ASGI app: counters + scrape-time gauges."""

from __future__ import annotations

from tests.integration.test_api import build_manager, client_for, wait_for_terminal

from relay.domain.types import ToolCallSpec
from relay.llm.mock import MockTurn


async def test_metrics_endpoint_reflects_a_run():
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
            run_id = (await client.post("/v1/runs", json={"goal": "2+2"})).json()["run_id"]
            await wait_for_terminal(client, run_id)

            resp = await client.get("/metrics")
            assert resp.status_code == 200
            body = resp.text
            # counters driven by the event stream
            assert "relay_runs_started_total" in body
            assert 'relay_runs_finished_total{status="completed"}' in body
            assert 'relay_tool_executions_total{outcome="success",tool="calculator"}' in body
            assert "relay_llm_cost_usd_total" in body
            # scrape-time gauge from the projection
            assert 'relay_runs{status="completed"} 1' in body
            # cardinality guard: run ids never become labels
            assert run_id not in body
