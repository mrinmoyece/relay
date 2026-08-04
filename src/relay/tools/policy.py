"""Policy engine: decides what happens when the model wants to run a tool.

Deny-by-default posture:
  * unknown tool                     -> DENY
  * tool not in the run's allowlist -> DENY (enforced by registry scoping)
  * DESTRUCTIVE risk                 -> REQUIRE_APPROVAL (human gate)
  * WRITE risk                       -> ALLOW by default, configurable
  * READ_ONLY                        -> ALLOW

Per-tool overrides let an operator tighten (or, explicitly and auditably,
loosen) any tool without touching code. The decision is recorded in the
event log either as an executed call, an ApprovalRequired, or a ToolFailed
with a policy error - so every decision is auditable after the fact.

A call that a human already approved (state.pending_calls[i].approved)
bypasses the policy re-check - the human IS the policy for that call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from relay.domain.types import RiskLevel
from relay.tools.base import Tool


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyEngine:
    # Default decision per risk level.
    risk_defaults: dict[RiskLevel, PolicyDecision] = field(
        default_factory=lambda: {
            RiskLevel.READ_ONLY: PolicyDecision.ALLOW,
            RiskLevel.WRITE: PolicyDecision.ALLOW,
            RiskLevel.DESTRUCTIVE: PolicyDecision.REQUIRE_APPROVAL,
        }
    )
    # Per-tool overrides win over risk defaults.
    tool_overrides: dict[str, PolicyDecision] = field(default_factory=dict)

    def decide(self, tool: Tool) -> PolicyDecision:
        if tool.name in self.tool_overrides:
            return self.tool_overrides[tool.name]
        return self.risk_defaults.get(tool.risk, PolicyDecision.REQUIRE_APPROVAL)
