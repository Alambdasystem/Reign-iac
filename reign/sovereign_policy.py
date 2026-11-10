"""Sovereign policy decision prototype — independent of the execution gateway.

This evaluates a narrowly scoped policy document against a registry record.
It is NOT a production enforcement point: provider/network boundaries must
require and verify authorizations independently, with fail-closed behavior.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit

from reign.sovereign_registry import AgentRegistry, RegistryDenied


@dataclass(frozen=True)
class Constitution:
    version: str
    allowed_tools: frozenset[str]
    blocked_tools: frozenset[str]
    allowed_hosts: frozenset[str]

    def __post_init__(self):
        if not self.version or self.allowed_tools.intersection(self.blocked_tools):
            raise ValueError("Invalid constitution")
        if any(not host or "/" in host or ":" in host for host in self.allowed_hosts):
            raise ValueError("Exact host names required")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    constitution_version: str


def decide(registry: AgentRegistry, constitution: Constitution, *,
           tenant_id: str, agent_id: str, principal_id: str,
           tool: str, url: str | None = None) -> PolicyDecision:
    def deny(reason: str) -> PolicyDecision:
        return PolicyDecision(False, reason, constitution.version)

    try:
        agent = registry.get(tenant_id, agent_id, principal_id=principal_id)
        if agent.constitution_version != constitution.version:
            return deny("constitution_mismatch")
        if not registry.permitted(tenant_id, agent_id, tool, principal_id=principal_id):
            return deny("agent_scope_denied")
    except (RegistryDenied, ValueError, TypeError):
        return deny("identity_or_registry_denied")
    if tool in constitution.blocked_tools or tool not in constitution.allowed_tools:
        return deny("constitutional_tool_denied")
    if url is not None:
        try:
            target = urlsplit(url)
            valid = (target.scheme == "https" and target.hostname in constitution.allowed_hosts
                     and target.port in (None, 443) and not target.username
                     and not target.password and not target.fragment)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            return deny("destination_denied")
    return PolicyDecision(True, "approved", constitution.version)
