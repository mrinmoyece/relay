"""Request/response DTOs.

Deliberately decoupled from domain objects: the API contract can stay
stable while the domain evolves, and domain internals (transcript,
pending calls) are exposed only in curated form.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from relay.domain.budget import Budget
from relay.domain.run import RunState


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10_000)
    model: str | None = None
    system_prompt: str = ""
    budget: Budget = Field(default_factory=Budget)
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateRunResponse(BaseModel):
    run_id: str
    status: str = "accepted"


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"]
    approver: str = Field(min_length=1)
    note: str = ""


class PendingApprovalView(BaseModel):
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk: str
    reason: str


class RunView(BaseModel):
    run_id: str
    status: str
    goal: str
    model: str
    step: int
    tokens_used: int
    cost_usd: float
    final_answer: str
    error: str
    pending_approval: PendingApprovalView | None
    last_seq: int

    @classmethod
    def from_state(cls, state: RunState) -> RunView:
        pa = state.pending_approval
        return cls(
            run_id=state.run_id,
            status=state.status.value,
            goal=state.goal,
            model=state.model,
            step=state.step,
            tokens_used=state.tokens_used,
            cost_usd=state.cost_usd,
            final_answer=state.final_answer,
            error=state.error,
            pending_approval=(
                PendingApprovalView(
                    approval_id=pa.approval_id,
                    tool_name=pa.call.tool_name,
                    arguments=pa.call.arguments,
                    risk=pa.risk.value,
                    reason=pa.reason,
                )
                if pa
                else None
            ),
            last_seq=state.last_seq,
        )


class EventView(BaseModel):
    seq: int
    recorded_at: str
    type: str
    data: dict[str, Any]
