"""Policy enforcement façade for controlled Sovereign MCP tool invocation.

This is not yet wired into REIGN's production MCP server or Computer network
proxy. Protected tools must have no direct bypass path before live activation.
"""
from dataclasses import dataclass
from typing import Callable, Any

from reign.sovereign_identity import GrantAuthority, IdentityDenied
from reign.sovereign_policy import Constitution, decide
from reign.sovereign_registry import AgentRegistry

class GatewayDenied(PermissionError):
    pass

@dataclass(frozen=True)
class GatewayRequest:
    grant: str
    tool: str
    url: str | None = None

class ConstitutionalGateway:
    def __init__(self, *, authority: GrantAuthority, registry: AgentRegistry,
                 constitution: Constitution, handlers: dict[str, Callable[..., Any]]):
        self.authority = authority
        self.registry = registry
        self.constitution = constitution
        self._handlers = dict(handlers)

    def execute(self, request: GatewayRequest, *, now: int | None = None):
        # An invalid/missing identity MUST NOT reach any tool handler.
        try:
            principal = self.authority.verify(request.grant, now=now)
            decision = decide(
                self.registry, self.constitution,
                tenant_id=principal.tenant_id, agent_id=principal.agent_id,
                principal_id=principal.owner_id, tool=request.tool, url=request.url,
            )
        except (IdentityDenied, ValueError, TypeError, KeyError):
            raise GatewayDenied("Access denied") from None
        if not decision.allowed or request.tool not in self._handlers:
            raise GatewayDenied("Access denied")
        # Web tools require an explicit, checked target before dispatch. A
        # missing URL must not bypass the constitution host allowlist.
        if request.tool.startswith("web.") and not request.url:
            raise GatewayDenied("Access denied")
        # Only explicitly registered, pre-approved trusted handlers can execute.
        # URL must be checked again at the actual network dispatch boundary.
        return self._handlers[request.tool](request.url)
