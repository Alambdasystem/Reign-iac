# Sovereign Console / Genesis — UI and policy architecture

## Naming
- **Sovereign**: platform and independent constitutional control plane.
- **Sovereign Console**: administrator UI, separate from REIGN's dedicated **UltraView AX** interface.
- **Genesis**: agent birth and registration workflow. A request is only a **draft** until approved and provisioned.

## Product navigation
1. **Agents** — identities, owners, harnesses, providers, versions, lifecycle, health and scoped capabilities.
2. **Genesis** — create-agent wizard: owner, provider, harness, constitution, capabilities, approval summary, deploy plan, activation receipt.
3. **Constitution** — immutable policy bundles; inheritance and exceptions must not weaken mandatory restrictions. Changes versioned and audited.
4. **Providers** (future) — Render, AWS AgentCore, Azure Foundry, external agent integrations via bounded adapters. MCP is a tool transport, not proof of trusted authorization.
5. **Approvals and Audit** (future) — approval queues, blocked requests, decision traces, signed runtime/credential events and revocation.

A read-only static UI prototype is at `prototypes/sovereign-console/index.html`. It does not call external services, hold credentials, create agents, or implement real policy enforcement.

## Required architecture before any deployment
- Authentication via trusted enterprise IdP (OIDC); verify claims and principal-to-tenant binding server-side. Do not accept identity from LLM or browser request fields.
- Registry stored transactionally in restricted PostgreSQL schema with optimistic revision, immutable constitution references, audit trail, tenant row isolation, and explicit database migration approval.
- Policy decision endpoint (PDP) with authenticated service identity; **deny on timeout/unavailability** and policy version mismatch. Explicit decisions tied to subject, owner, task, destination, tool, and expiry.
- Policy enforcement points (PEPs) at MCP gateway, provider adapter and Computer's actual browser/proxy egress. The PEP validates trusted context, applies current policy and cannot be bypassed by direct worker calls.
- Provider-native permissions (Render access tokens, AWS IAM, Azure RBAC) must **also** enforce restrictions. Sovereign never bypasses provider guardrails.
- Genesis must require explicit approval for billable resource creation, privilege expansion and irreversible actions, with a dry-run plan and rollback receipts.
- Do not reuse REIGN's existing single-admin tokens for new agents. No production access or new providers in this PR.

## Execution/test roadmap
1. **PR #28**: reviewed, tested extraction of REIGN's reusable harness.
2. **PR #30**: reviewed, tested in-memory registry prototype; never deploy as authority.
3. **This PR**: unit-test fail-closed constitutional decision examples and review admin UI in local browser. The UI and policy implementation are demonstrations, not integrated authorization.
4. **Next**: DB migration + transactional repository and tenant isolation tests; security review.
5. **Next**: identity middleware and policy-enforced gateway with direct-call, redirect, race, impersonation, revoke, timeout tests.
6. **Next**: Genesis lifecycle simulation with Render adapter and cost-approval controls.
7. **Last**: live acceptance, independently reviewed gated deploy. Render Blueprint remains paused and OpenTofu approval gates remain mandatory.
