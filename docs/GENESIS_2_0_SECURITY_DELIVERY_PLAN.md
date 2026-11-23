# REIGN / Genesis 2.0: security-first delivery plan
Status: **design baseline and non-executing privacy contract in progress**. No new policy is live.
Source review date: 2026-11-13. Keep the existing four Render services and Blueprint owner.

## Verified baseline

- `reign/history_crypto.py`: AES-256-GCM encrypted finished-turn transcript records with record binding and versioned keyring. Production uses the Core disk. This does **not** encrypt in-memory model inputs, MCP responses, Temporal history, every attachment, or durable graph checkpoints.
- `reign/agent.py`: existing LangGraph chat agent with bounded tool calls, in-memory checkpoints, and privacy-safe in-memory trace metadata. `model_client()` sends message text to an external model; no production PHI/PII egress filter is wired into that path today.
- `reign/mcp_client.py`, `reign/mcp_workspace.py`, `reign/tool_contracts.py`: fixed local MCP stdio tool allowlist; dynamic MCP registration is not supported.
- `reign/genesis_pilot.py`, `reign/genesis_catalog.py`, `reign/genesis_policies.py`: durable draft agents, graph/KB definitions and policy simulations. **No approved Genesis agent execution, retrieval ingestion or enforced policy assignment**.
- `reign/distributed_nodes.py`: authenticated node inventory and route preview; no dispatch.
- `reign/ax.py`: single admin session (no individual RBAC). `reign/telemetry.py`: metadata spans, no persistent tracing/collector. `REIGN_DATABASE_URL` is optional REIGN-only preflight; do not use Moby Alpha, Blueberry or Workmates databases.
- GitHub PR exact-head required checks: OpenTofu validation, Python, Postgres integration and four-container browser acceptance. Gated GHCR publication and manually planned Blueprint with autoSync disabled.

## Design principles and limitations

1. **No PHI/PII EGRESS by default** for unclassified or sensitive information. A regex-only scan cannot prove absence of PHI or PII: privacy classification provenance, content detectors, user disclosure scope, approval, and deny-by-default provider/tool controls are needed.
2. Treat *all* untrusted inbound text, browser pages, retrieved documents, file attachments, speech transcripts, tool output, and agent-generated text as potentially restricted until classified. Recheck prior to each external model or MCP/tool call. File uploads require type-aware parsing with no OCR bypass.
3. Encryption at rest is necessary but insufficient: use TLS/private authenticated transports, per-user/tenant cryptographic binding, key rotation, replay protection, managed secrets, secure retention/erasure and restore verification. Never put secrets or raw payloads into general logs or traces.
4. Independent policy **enforcement** must sit at existing REIGN Core model/tool boundaries, stdio MCP server dispatch, Temporal/Computer effect admission and any approved provider adapter. Policy *simulations* cannot grant execution.
5. The user **removed the proposal for a new agent gateway and agent-routing system**. Do not add one. The existing Core/MCP/Temporal call paths may be hardened; distributed node inventory remains inventory-only unless separately reviewed.
6. For regulated health processing, require organizational HIPAA risk assessment, BAAs where applicable, vendor verification and real deployment controls. Never advertise HIPAA compliance based solely on this implementation.
7. The draft-only `reign/privacy_policy.py` deliberately **is not used in live chat or tool dispatch**. It is a testable policy contract only. Activating only this regex layer would create a false security assurance.

## Work packages and gates

| Package | Deliverable | Required tests / evidence | Gate |
| --- | --- | --- | --- |
| P0 | Threat model, data-flow inventory, owners, classification vocabulary, deny defaults | Every AX, chat, MCP, voice, browser, history, vault, external model and Temporal flow mapped with trusted boundary | Review before production enforcement |
| P1 | Privacy policy evaluation, restricted-data scan adapters, safe decisions and traces | Synthetic identifiers, adversarial obfuscation, false negatives, over-limit, parser failures, prompt injection, no raw payloads in logs | Fail closed |
| P2 | End-to-end encryption design, owner/tenant scoped authenticated encryption for conversations/checkpoints/attachments | Encrypted persistence, restart/restore, tamper detection, wrong-owner and wrong-key denials, rotation, replay, backup/retention/delete | No production content migration without independent validation |
| P3 | AX Guardrails button, policy status and explainable deny/approval UI | Auth/CSRF, no bypass via direct Core/MCP API, accessible/browser screenshots, no sensitive values in client trace | Default deny |
| P4 | Per-user identity and RBAC (owner, admin, operator, reader) | Chrishina/Mayur scoped accounts; session rotation, revocation, cross-user isolation, no shared admin token | Identity before multi-user data |
| P5 | MCP tool registration and audit catalog (draft first, then approved allowlisted adapters) | Schema validation, manifest provenance, capabilities, origin restrictions, network SSRF/redirect denial, timeouts/cancel, negative calls | No arbitrary shell/network server launch |
| P6 | Genesis agent lifecycle and reviewed policy assignment | Draft -> reviewed -> approved -> active -> suspended/retired; spoofed identity/tenant/revision rejection; full audit | Live activation only with P1-P5 gates |
| P7 | Knowledge ingestion/retrieval and typed LangGraph execution with durable checkpoints | Source ACL, retrieval leakage/cross-user, human approval, exactly-once/uncertain effects, restart/replay, downgrade/rollback | No unsafe side-effect replay |
| P8 | Persistent privacy-safe OpenTelemetry-style traces and operator observability | Multi-service trace correlation, allowed-field schema, denial count, zero raw content, access audit, retention, failure injection | Observability cannot leak PHI/PII |
| P9 | Signed production acceptance and staged rollout | Exact-head 4 CI gates, GHCR digest proof, reviewed Blueprint diff, controlled test users, rollback, and 21 live AX prompt-to-tool cases with evidence | Production sign-off |

## First PR (this branch)

- Draft-only `reign/privacy_policy.py`: explicit classification input; conservative outbound allow decision; synthetic sensitive-indicator backstop; metadata-only responses.
- `tests/test_privacy_policy.py`: default-deny, allowed explicit public/approved case, synthetic detection, malformed inputs, metadata leakage.
- This document. No live enforcement activation, env changes, service changes, secrets, additional costs or migration.

## Release process for each later package

1. Separate branch/PR, exact-head tests, security review and recorded negative tests; no fake PASS from mocks.
2. Build/tests publish immutable images via gated main workflow. Review release receipt for the exact source SHA.
3. Plan the single existing Render Blueprint, inspect actions and changed costs. Approve only expected actions.
4. Verify live deployments and authenticated AX UI. Save sanitized evidence and run rollback checks.
5. Do not claim PHI/PII safety or production multi-agent capabilities until independent verification and each gate passes.

## Review questions

- Should **all** external models and MCP actions remain blocked for unclassified content until manual classification, or allow an explicitly reviewed content-specific classification service?
- Should PHI be allowed **only** under approved restricted BAA-supported processing, with separate tenant isolation? Default proposal: no external PHI.
- What retention periods and allowed export destinations should each workspace enforce? No blanket retention assumption.
- Existing Core disk first versus a dedicated encrypted REIGN database for durable checkpoints and trace metadata (no other product database reuse)?
