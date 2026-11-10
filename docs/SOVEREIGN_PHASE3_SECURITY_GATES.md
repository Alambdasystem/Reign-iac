# Sovereign Phase 3 — durability, identity and enforcement prototypes

**Do not deploy this branch.** These modules are test-driven scaffolding, not a production security boundary. REIGN's Core, MCP workspace, Computer, Vault, UltraView AX and Render Blueprint are unchanged.

## Implemented

- Migration `006_sovereign_agents.sql`: tenant/agent-scoped registry table with revision, lifecycle, parent FK restricted to the same tenant, metadata, and row-level security. No migration is run against production by this PR.
- `sovereign_identity.py`: short-lived HMAC-signed, audience-bound workload grants. **This is not OIDC**, does not validate an external user's identity, and has no grant revocation list. The issuance path must be restricted to an authenticated trusted authority.
- `sovereign_gateway.py`: verifies grant and policy at a single controlled tool dispatch point, with no default tool handlers. A real trusted provider/MCP/computer dispatch integration is still required.
- Separate unit and disposable-Postgres integration suites.

## Security constraints before production

1. Authenticate humans and workloads against a real IdP, map signed claims to tenant/agent and separate trusted issuing authority from clients. Do not accept caller-supplied owner IDs.
2. Create a production DB role/grant model where tenants **cannot set or spoof RLS session context**. Application roles setting arbitrary custom GUCs can circumvent example RLS. Use restricted trusted stored procedures or a reviewed connection-layer enforcement model, as well as defense-in-depth RLS. Review migration sequencing and transactional lifecycle enforcement.
3. Implement real persistent policy store and immutable constitutional versions, as opposed to process-memory policy records.
4. Require authorization at actual MCP, Computer/proxy network and provider execution boundaries, disallowing direct service bypass; recheck destinations after redirects and prevent SSRF/DNS rebinding.
5. Bind signed grants to task, target tool/destination, nonces and revocation; add keys/JWKS rotation, KMS-backed secrets, issuer audit and correct clock skew handling. HMAC PoC is insufficient to protect live tools.
6. Add authoritative transactionally checked agent revocation and cross-provider policy intersection; fail closed when registry/identity/policy is unavailable.
7. Complete negative security tests under load, run human acceptance, obtain independent reviews and explicit cost/migration/Render/OpenTofu approval.

CI against a disposable `reign_ci` PostgreSQL database is not live production validation. Only the independent test harness can claim end-to-end *prototype* execution.
