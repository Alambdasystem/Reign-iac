# Continue REIGN from another ChatGPT session

Temporary repository: https://github.com/Alambdasystem/Reign-iac
Long-term destination: https://alambda-forge.onrender.com/

GitHub write access through the Work session's connector was verified with the
initial commit `dc88c05d29d9eec6fa9798e928c21e66ec9d99e6`. That does not prove a
different ChatGPT mode/session has write tools or the same account permissions.
Official product guidance says tools depend on plan/platform/workspace settings:
https://learn.chatgpt.com/docs/use-chatgpt

First ask the new session to read this repository and say which GitHub tools it
actually has. If it can write, make an isolated documentation branch/PR and read
back the commit SHA. If it can only read or draft, have it produce patches and use
a write-capable session or GitHub to apply them. Do not hand it passwords/tokens
in prompts and do not assume switching modes guarantees a particular credit cost.

## Copy this into the next session

> Continue Alambdasystem/Reign-iac. Read README.md, docs/DEPLOYMENT.md,
> docs/APPLICATION_CONTRACTS.md and the latest CI results first. Confirm your actual
> repository read/write capability. Keep infrastructure ownership in OpenTofu;
> GitHub is temporary and Git High is the intended long-term source of truth.
> Implement REIGN Core and UltraView AX as independent Docker services, complete
> and test the experimental Computer worker, and use the existing Temporal pilot
> container. First implement the configurable computer requirement below: let
> James create a managed computer or connect an existing one by URL plus secure
> remote-access setup. Keep browser and full desktop capabilities explicit and
> use one Core/AX session contract across adapters. Implement authenticated live
> screen viewing and human takeover with
> stale-command rejection. Build/publish immutable images through the pipeline,
> update deploy/release.json only with tested digests, and prepare a concrete
> costed plan before seeking approval for paid changes. Preserve Workmates and
> Git High; never commit secrets. Keep Tailscale/GPU workers deferred. Do not claim
> deployment or E2E success without actual evidence. Run the documented browser
> acceptance flow opening Git High, including James's real takeover and handback.

## Configurable computer requirement (James, 2026-11-08)

James approved adding this requirement to the handoff before continuing in a new
session. This update records the design; it does not implement or deploy it.
Add the connection layer before completing Core and AX so the selected computer
can change without rewriting the application.

### Two user choices

| Choice | Expected behavior |
|---|---|
| Create a computer | Start a managed browser worker, or a supported desktop environment when desktop capabilities are needed. Provision infrastructure through OpenTofu after the concrete cost is approved. |
| Connect my computer | Register an existing worker/computer using its connection URL and configured remote access. Test authentication, reachability and capabilities before allowing tasks. Do not provision a replacement silently. |

Distinguish browser access from full desktop access. The existing Computer
implementation launches its own headless Chromium browser; it does not yet
attach to an external browser or control a full desktop. A remote-browser
adapter can use a supported Playwright/CDP endpoint. Full desktop access needs
an installed connector or a supported RDP/VNC gateway and a separate desktop
adapter; a browser connection must never be reported as full desktop support.

A URL is only the connection address. A usable profile also needs a supported
connection type, server-side credentials and a reachable network path. A normal
website URL or remote-desktop login page alone is not a worker API. Keep remote
access behind authenticated, encrypted connections; do not expose raw desktop
or browser-debugging ports publicly or place credentials in URLs or Git.
Tailscale and Windows GPU/LLM integration remain deferred; defining external
connection support does not authorize enrolling devices or opening ports.

### Setup and shared behavior

User flow: **Computer -> Create or Connect -> Connection URL (Connect only) ->
Authenticate -> Test connection -> Save -> Select for a task**.
For Create, select the supported environment and show its cost before paid
provisioning. Save credentials through a secure server-side secret mechanism,
not in browser storage. Show connection status and browser/desktop capabilities.

- Introduce a provider/adapter interface for session creation or attachment,
  status, live frames, input, takeover, handback and session close. Core and AX
  use the same contract regardless of the chosen computer.
- Persist connection profiles and bind each session to its selected profile and
  authenticated owner. Keep Temporal dispatch and frame/input requests routed
  to the same worker/session; changing a default must not move an active session.
- Preserve authenticated live viewing, current control ownership and epoch-based
  stale-command rejection for every adapter. Agent actions stop during human
  takeover; stale human input is rejected after handback.
- On loss of connection, stop accepting actions and show the failure honestly.
  Reconnect only after rechecking session identity and ownership; do not replay
  queued input or silently switch to a different computer.
- Disconnecting from a user-owned computer releases only REIGN's connection and
  resources it created. Leave that computer running and preserve unrelated
  applications and browser sessions. Managed environment cleanup follows its
  explicit lifecycle and the existing infrastructure protections.
- Preserve the pilot's destination restrictions. Any broader desktop/network
  permissions need an explicit scope; do not remove the Git High allowlist as
  an incidental part of adding external connection support.

### Acceptance to add

Test both managed and externally connected browser paths, connection/auth
failures, capability checks, worker routing, live frames, takeover/handback,
stale input, reconnect and ownership-aware cleanup. Test a full desktop adapter
against a real supported desktop before marking it complete; report unavailable
adapters as unsupported or blocked. Mock tests do not prove remote access.
Retain the documented Git High E2E flow and James's actual human acceptance.

## Still outstanding

- Configurable computer profiles, managed/external adapters and connection setup;
  browser and desktop capabilities must be implemented and verified separately.
- Core/AX application implementations and Dockerfiles; full Computer tests.
- GitHub protected environments/branch policy, CI secrets, state server and lock
  verification; these require account configuration not accomplished by YAML.
- Four released container digests; Render private-registry credentials if needed.
- Current price review, explicit paid approval, actual OpenTofu plan/apply.
- Live per-service verification, browser E2E and human acceptance.
- Authenticated Git High source migration and later Tailscale integration.

Consult live CI for validation results on the current commit. Infrastructure
validation, mock provider tests and container smoke are separate from deployment.

## Approved deployment direction (2026-11-08)

James selected **Render first, Tailscale second, hybrid long-term**.
This is a deployment direction, not approval to bypass infrastructure safety gates
or an authorization to enroll devices, expose ports, or spend an unbounded amount.

1. Complete Core/AX/Computer/Temporal integration and real browser acceptance.
2. Deploy on Render with OpenTofu, immutable tested images, isolated credentials,
   protected state, and explicit protection for Workmates and Git High. Reuse an
   existing PostgreSQL instance only after identifying its workspace, confirming
   capacity and ownership, and isolating REIGN via a separate database/schema and
   restricted role. Keep the pilot Temporal disk unless a migration is tested.
3. Measure CPU, memory, storage, browser session limits and actual monthly cost
   after live validation. Optimize Render service sizing without silently changing
   the four-component architecture.
4. In a separate, reversible migration phase, connect an approved Render-side
   Tailscale connector with scoped ACLs to authorized worker hosts. Start with
   Computer workers; consider GPU workloads later. Never expose CDP/RDP/VNC or
   internal service ports publicly. Test authentication, identity binding,
   takeover/handback, stale command rejection, reconnect and cleanup over the
   private path.
5. Move one workload at a time with measured rollback and preserve the working
   Render deployment until the replacement passes live acceptance.

**Current blockers to first live apply:** select the Render workspace; confirm
the existing database's capacity/permissions; configure GitHub deployment
environments, secrets, locked OpenTofu state backend and registry pull access;
publish reviewed image digests and inspect an actual costed plan. CI success
alone is not live deployment evidence.
