"""Genesis registration and Guardrail Manager preview.

Prototype-only: trusted callers must supply a verified principal from an
authenticated control plane. No provider deployment, API endpoints, credential
issuance, or cloud-resource creation is implemented here. Drafts cannot execute.
"""
from __future__ import annotations

from dataclasses import dataclass

from reign.sovereign_policy import Constitution, PolicyDecision, decide
from reign.sovereign_registry import AgentRecord, AgentRegistry, RegistryDenied


@dataclass(frozen=True)
class GenesisRequest:
    agent_id: str
    name: str
    harness: str
    provider: str
    capabilities: frozenset[str]
    parent_id: str | None = None


@dataclass(frozen=True)
class GenesisPreview:
    agent_id: str
    state: str
    constitution_version: str
    allowed_capabilities: tuple[str, ...]
    rejected_capabilities: tuple[str, ...]
    needs_approval: bool
    provider_provisioned: bool


class GenesisRegistration:
    """Draft registration only, with deny-by-default constitution checks."""

    def __init__(self, registry: AgentRegistry, constitution: Constitution):
        self.registry, self.constitution = registry, constitution

    def preview(self, request: GenesisRequest, *, tenant_id: str,
                owner_id: str, principal_id: str) -> GenesisPreview:
        if principal_id != owner_id:
            raise RegistryDenied("Unverified registration owner")
        # Require a valid proposed record before computing a preview.
        record = AgentRecord(
            agent_id=request.agent_id, tenant_id=tenant_id, owner_id=owner_id,
            name=request.name, harness=request.harness, provider=request.provider,
            constitution_version=self.constitution.version,
            capabilities=request.capabilities, parent_id=request.parent_id)
        # The preview is an approval candidate, not a promise to provision.
        # Reject locally provable invalid registrations before presenting it.
        if record.parent_id == record.agent_id:
            raise RegistryDenied("Agent cannot parent itself")
        try:
            self.registry.get(tenant_id, record.agent_id, principal_id=principal_id)
        except RegistryDenied:
            # Access-denied and absent records are intentionally indistinguishable.
            # create() still checks uniqueness against all owners at commit time.
            pass
        else:
            raise RegistryDenied("Agent already registered")
        allowed = record.capabilities & self.constitution.allowed_tools
        allowed -= self.constitution.blocked_tools
        if record.parent_id is not None:
            parent = self.registry.get(tenant_id, record.parent_id, principal_id=principal_id)
            if parent.constitution_version != self.constitution.version or parent.state != "active":
                raise RegistryDenied("Unapproved parent")
            allowed &= self.registry.effective_capabilities(tenant_id, parent.agent_id,
                                                              principal_id=principal_id)
        rejected = record.capabilities - allowed
        return GenesisPreview(
            agent_id=record.agent_id, state="draft",
            constitution_version=self.constitution.version,
            allowed_capabilities=tuple(sorted(allowed)),
            rejected_capabilities=tuple(sorted(rejected)),
            needs_approval=True, provider_provisioned=False)

    def register_draft(self, request: GenesisRequest, *, tenant_id: str,
                       owner_id: str, principal_id: str) -> AgentRecord:
        preview = self.preview(request, tenant_id=tenant_id, owner_id=owner_id,
                               principal_id=principal_id)
        if preview.rejected_capabilities:
            raise RegistryDenied("Requested capabilities exceed guardrails")
        record = AgentRecord(
            agent_id=request.agent_id, tenant_id=tenant_id, owner_id=owner_id,
            name=request.name, harness=request.harness, provider=request.provider,
            constitution_version=self.constitution.version,
            capabilities=request.capabilities, parent_id=request.parent_id)
        # Only draft registration is supported. Activation requires separately
        # reviewed workload identity and provider evidence. No status shortcut.
        return self.registry.create(record, principal_id=principal_id)

    def guardrail_preview(self, *, tenant_id: str, agent_id: str,
                          principal_id: str, tool: str,
                          url: str | None = None) -> PolicyDecision:
        return decide(self.registry, self.constitution,
                      tenant_id=tenant_id, agent_id=agent_id,
                      principal_id=principal_id, tool=tool, url=url)
