"""In-memory Sovereign agent registry proof of concept.

Not an authentication service or a tool enforcement point. Callers must supply
an independently verified principal; production use requires durable storage,
transactional authorization, and enforcement at MCP and network boundaries.
"""
from dataclasses import dataclass, replace
import re

_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_CAP = re.compile(r"^[a-z][a-z0-9_.:-]{1,80}$")
_STATES = {"draft", "active", "suspended", "retired"}
_TRANSITIONS = {
    "draft": {"active", "retired"},
    "active": {"suspended", "retired"},
    "suspended": {"active", "retired"},
    "retired": set(),
}


class RegistryDenied(PermissionError):
    """Invalid or unauthorized registry operation; no resource details."""


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    tenant_id: str
    owner_id: str
    name: str
    harness: str
    provider: str
    constitution_version: str
    capabilities: frozenset[str]
    parent_id: str | None = None
    state: str = "draft"
    revision: int = 1

    def __post_init__(self):
        for value in (self.agent_id, self.tenant_id, self.owner_id):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError("Invalid agent, tenant, or owner ID")
        if self.parent_id is not None and not _ID.fullmatch(self.parent_id):
            raise ValueError("Invalid parent ID")
        if not self.name or not self.name.strip() or len(self.name) > 120:
            raise ValueError("Invalid agent name")
        if self.harness not in {"langgraph", "external"}:
            raise ValueError("Unsupported harness adapter")
        if self.provider not in {"render", "aws", "azure", "external"}:
            raise ValueError("Unsupported provider")
        if not self.constitution_version or len(self.constitution_version) > 80:
            raise ValueError("Constitution version required")
        if self.state not in _STATES or self.revision < 1:
            raise ValueError("Invalid lifecycle or revision")
        if not isinstance(self.capabilities, frozenset) or any(
            not isinstance(c, str) or not _CAP.fullmatch(c)
            for c in self.capabilities
        ):
            raise ValueError("Invalid capability identifiers")


class AgentRegistry:
    """Fail-closed registry with tenant/owner isolation and parent attenuation."""

    def __init__(self):
        self._records: dict[tuple[str, str], AgentRecord] = {}

    def _owned(self, tenant_id: str, agent_id: str, principal_id: str) -> AgentRecord:
        record = self._records.get((tenant_id, agent_id))
        if record is None or record.owner_id != principal_id:
            raise RegistryDenied("Agent not found or access denied")
        return record

    def create(self, record: AgentRecord, *, principal_id: str) -> AgentRecord:
        if principal_id != record.owner_id or record.state != "draft" or record.revision != 1:
            raise RegistryDenied("Registration denied")
        key = (record.tenant_id, record.agent_id)
        if key in self._records:
            raise RegistryDenied("Agent ID already registered")
        if record.parent_id is not None:
            if record.parent_id == record.agent_id:
                raise RegistryDenied("Agent cannot parent itself")
            parent = self._owned(record.tenant_id, record.parent_id, principal_id)
            if parent.state != "active":
                raise RegistryDenied("Parent is not active")
            if parent.constitution_version != record.constitution_version:
                raise RegistryDenied("Constitution version mismatch")
            if not record.capabilities.issubset(self.effective_capabilities(
                parent.tenant_id, parent.agent_id, principal_id=principal_id
            )):
                raise RegistryDenied("Child cannot expand parent permissions")
        self._records[key] = record
        return record

    def get(self, tenant_id: str, agent_id: str, *, principal_id: str) -> AgentRecord:
        return self._owned(tenant_id, agent_id, principal_id)

    def list_owned(self, tenant_id: str, *, principal_id: str) -> tuple[AgentRecord, ...]:
        return tuple(sorted(
            (r for r in self._records.values()
             if r.tenant_id == tenant_id and r.owner_id == principal_id),
            key=lambda r: r.agent_id,
        ))

    def transition(self, tenant_id: str, agent_id: str, state: str, *,
                   principal_id: str, expected_revision: int) -> AgentRecord:
        existing = self._owned(tenant_id, agent_id, principal_id)
        if existing.revision != expected_revision or state not in _TRANSITIONS[existing.state]:
            raise RegistryDenied("Invalid lifecycle transition or stale revision")
        updated = replace(existing, state=state, revision=existing.revision + 1)
        self._records[(tenant_id, agent_id)] = updated
        return updated

    def effective_capabilities(self, tenant_id: str, agent_id: str, *,
                               principal_id: str) -> frozenset[str]:
        record = self._owned(tenant_id, agent_id, principal_id)
        effective = record.capabilities
        visited: set[str] = set()
        current = record
        while True:
            if current.state != "active" or current.agent_id in visited:
                return frozenset()
            visited.add(current.agent_id)
            if current.parent_id is None:
                return effective
            parent = self._owned(tenant_id, current.parent_id, principal_id)
            if parent.constitution_version != record.constitution_version:
                return frozenset()
            effective = effective.intersection(parent.capabilities)
            current = parent

    def permitted(self, tenant_id: str, agent_id: str, capability: str, *,
                  principal_id: str) -> bool:
        return capability in self.effective_capabilities(
            tenant_id, agent_id, principal_id=principal_id
        )
