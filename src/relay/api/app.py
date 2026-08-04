"""FastAPI application.

Composition root: settings decide the store (Postgres vs memory) and the
provider (Anthropic vs mock); everything else is injected. `create_app()`
also accepts a prebuilt RunManager so tests can wire in scripted mocks.

Execution model: POST /v1/runs persists RunCreated and returns 202
immediately; the loop runs as a background task. Startup runs crash
recovery; shutdown cancels tasks (safe - durability means nothing is lost).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from relay.api.schemas import (
    ApprovalDecisionRequest,
    CreateRunRequest,
    CreateRunResponse,
    EventView,
    RunView,
)
from relay.config import Settings, get_settings
from relay.domain.run import RunStatus
from relay.engine.loop import AgentEngine
from relay.engine.manager import RunManager
from relay.memory.store import JsonlMemoryStore
from relay.observability import get_logger, setup_logging, setup_tracing
from relay.observability.metrics import registry as metrics_registry
from relay.store.memory import InMemoryEventStore
from relay.tools.builtin import calculator, make_http_get, make_send_email, make_write_file
from relay.tools.policy import PolicyEngine
from relay.tools.registry import ToolRegistry

log = get_logger(__name__)


def build_default_registry() -> ToolRegistry:
    workspace = Path("./workspace")
    return ToolRegistry(
        [
            calculator,
            make_http_get(allowed_domains=("example.com", "wikipedia.org")),
            make_write_file(workspace),
            make_send_email(),
        ]
    )


async def build_manager(settings: Settings) -> RunManager:
    if settings.durable:
        from relay.store.postgres import PostgresEventStore

        store = await PostgresEventStore.connect(settings.database_url)  # type: ignore[arg-type]
    else:
        store = InMemoryEventStore()
        log.warning(
            "using_in_memory_store",
            extra={"ctx": {"hint": "set RELAY_DATABASE_URL for durability"}},
        )

    if settings.provider == "anthropic":
        from relay.llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key=settings.anthropic_api_key)
    else:
        from relay.llm.mock import MockLLMProvider

        provider = MockLLMProvider()

    registry = build_default_registry()
    engine = AgentEngine(
        store=store,
        provider=provider,
        registry=registry,
        policy=PolicyEngine(),
        settings=settings,
        memory=JsonlMemoryStore(Path("./workspace/.relay-memory.jsonl")),
    )
    return RunManager(engine=engine, store=store, registry=registry)


def create_app(manager: RunManager | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        setup_logging()
        setup_tracing(service_name=settings.service_name, endpoint=settings.otel_endpoint)
        mgr = manager if manager is not None else await build_manager(settings)
        app.state.manager = mgr
        recovered = await mgr.recover()
        if recovered:
            log.info("startup_recovery", extra={"ctx": {"recovered_runs": recovered}})
        yield
        await mgr.shutdown()

    app = FastAPI(
        title="Relay",
        description="A durable, event-sourced runtime for AI agents",
        version="0.1.0",
        lifespan=lifespan,
    )

    def mgr() -> RunManager:
        return app.state.manager

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        # Counters come from the event stream (recorded post-append, so they
        # can't disagree with the ledger); run-status gauges are computed at
        # scrape time from the projection (cheap indexed read).
        gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = {"relay_runs": {}}
        for status in RunStatus:
            run_ids = await mgr()._store.list_runs(status=status.value)  # noqa: SLF001
            gauges["relay_runs"][(("status", status.value),)] = float(len(run_ids))
        return metrics_registry.render(gauges=gauges)

    @app.post("/v1/runs", response_model=CreateRunResponse, status_code=202)
    async def create_run(req: CreateRunRequest) -> CreateRunResponse:
        run_id = await mgr().engine.create_run(
            goal=req.goal,
            model=req.model,
            system_prompt=req.system_prompt,
            budget=req.budget,
            allowed_tools=req.allowed_tools,
            metadata=req.metadata,
        )
        mgr().schedule(run_id)
        return CreateRunResponse(run_id=run_id)

    @app.get("/v1/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str) -> RunView:
        state = await mgr().engine.get_state(run_id)
        if state.status == RunStatus.PENDING and state.last_seq == 0:
            raise HTTPException(404, f"run {run_id} not found")
        return RunView.from_state(state)

    @app.get("/v1/runs/{run_id}/events", response_model=list[EventView])
    async def get_events(run_id: str) -> list[EventView]:
        records = await mgr()._store.read(run_id)  # noqa: SLF001
        if not records:
            raise HTTPException(404, f"run {run_id} not found")
        return [
            EventView(
                seq=r.seq,
                recorded_at=r.recorded_at.isoformat(),
                type=r.event.type,
                data=r.event.model_dump(mode="json"),
            )
            for r in records
        ]

    @app.post("/v1/runs/{run_id}/approvals/{approval_id}", response_model=RunView)
    async def decide_approval(
        run_id: str, approval_id: str, req: ApprovalDecisionRequest
    ) -> RunView:
        try:
            if req.decision == "approve":
                state = await mgr().engine.approve(
                    run_id, approval_id, approver=req.approver, note=req.note
                )
            else:
                state = await mgr().engine.deny(
                    run_id, approval_id, approver=req.approver, note=req.note
                )
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return RunView.from_state(state)

    @app.post("/v1/runs/{run_id}/cancel", response_model=RunView)
    async def cancel_run(run_id: str) -> RunView:
        state = await mgr().engine.cancel(run_id)
        return RunView.from_state(state)

    @app.get("/v1/runs", response_model=list[str])
    async def list_runs(status: str | None = None) -> list[str]:
        return await mgr()._store.list_runs(status=status)  # noqa: SLF001

    return app


app = create_app()
