# Sovereign Agent Registry — Phase 2 prototype

This module is a **design/test prototype**, not production authorization.

## Boundaries

- `AgentRecord`: immutable, versioned identity and declared capability metadata.
- `AgentRegistry`: in-memory, per-tenant owner isolation; default-denied capabilities until activation; no child privilege expansion; constitutional version match; suspension/retirement deny the effective permissions of descendants.
- The principal ID passed to each method **must already have been authenticated by a trusted identity provider**. The module cannot verify it itself. Do not expose direct endpoints or take a principal from model, prompt, request body, or MCP tool arguments.
- Capability evaluation is **not a security enforcement gateway**: protect every MCP tool, provider adapter, browser network effect and credential redemption independently; deny if policy authority unavailable. Do not use the prototype alone to authorize production actions.
- No DB persistence, network endpoints, provider resources, credential issuance, deployment, new agents, or change to REIGN Core/UltraView AX.

## Test gates

`python -m unittest tests.test_sovereign_registry -v`

The test suite checks two isolated definitions, draft default denial, duplicate/invalid IDs, cross-tenant and cross-owner access, parent constitution and scope attenuation, suspended parent cascading denial, lifecycle revision checks and terminal retirement.

## Next integration PRs

1. Durable, transactional registry scoped by verified tenant and workload identity; migrations require restricted database review.
2. Immutable constitutional policy bundles and formal policy-as-code decision API, with comprehensive deny-by-default decisions.
3. Authenticated identity issuing and bounded agent creation via Genesis workflow with human approval.
4. Actual gateway enforcement with no direct provider-side or network bypass, and signed policy decisions tied to task and agent.
5. Render provider adapter for simulation first. AWS/Azure require separate resource and billing approval.

PR #28 must be independently reviewed before merging Phase 2. Do not create new paid services or bypass OpenTofu gates.
