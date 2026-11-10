# Sovereign agent harness extraction (phase 1)

The first step is to separate **agent definition** from **execution harness** without changing REIGN's security boundary or Render deployment.

- `reign/agent_definition.py` defines immutable agent identity, instructions, constitutional version metadata, and capability labels. `REIGN_DEFINITION` preserves existing system instructions.
- `reign/agent.py` still owns the LangGraph execution loop, checkpoints, interruptions and bounded tool execution, but takes a validated `AgentDefinition` (default: REIGN).
- The private MCP tool catalog remains fixed and verified. Capability labels are **not** permission grants. This change does **not** support arbitrary agent MCP servers, tenant identities, dynamic provider selection, or independent deployment.
- The tool gateway, worker URL restrictions, human takeover fencing, vault protections, service credentials, and existing AX/Core routes remain unchanged.

## Next stages (separate reviewed PRs)

1. Define a versioned Agent Registry and constitution schema, with independent tenant/agent identity and immutable policy inheritance.
2. Add an out-of-process policy decision point and enforce scoped grants at every tool entry, including the worker's actual egress boundary. Default deny and no inheritance of parent-agent credentials.
3. Refactor the fixed tool contract into a policy-filtered catalog, while retaining strict schema validation and explicit approval of external MCP providers.
4. Add sandboxed multi-agent instantiation and provider adapters for Render first, then AWS/Azure. Do not treat an MCP server as an authorization boundary.
5. Test non-bypass by direct calls, redirects, delegation and conflicting provider guardrails before promoting any production change.

## Acceptance for phase 1

- Existing REIGN tests and four-container acceptance should pass unchanged.
- Added tests verify default REIGN definition and independent prompts for two harness instances.
- No new Render services, secrets, Blueprint changes, or live agent creation.
