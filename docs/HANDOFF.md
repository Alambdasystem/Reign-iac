## Live: approved UltraView + LangChain/LangGraph (2026-11-08)

Your original UltraView AX design is deployed at
https://reign-ax-pilot-d934.onrender.com/. Source cf95a713, PR7; promotion PR8.
All four CI checks and the tested-image release passed. The live authenticated
API acceptance at 23:54:42Z passed using actual OpenAI gpt-6-astra, inherited from
Workmates through Render secret/model references. No secret values were copied
into source, AX or browser storage. Workmates was unchanged.

LangChain ChatOpenAI uses the Responses API; LangGraph actively runs conversation
memory, bounded model/tool loops, trace events, token usage and checkpointed
pause/resume. Actual browser navigation paused under human control and resumed
through Temporal after handback. The live browser controls retain epoch fencing.

See [deployment receipt](deployments/2026-11-08-ultraview-langgraph.md) and
[sanitized live acceptance](deployments/2026-11-08-ultraview-acceptance.json).
The Blueprint uses normal AX startup, with the temporary verifier removed.
Human acceptance by James has not yet been performed. CI tested the authenticated UI at mobile and desktop
sizes; live verification exercised the public authenticated API chain.

Remaining: voice, durable LangGraph checkpoints, restricted REIGN DB access and
external desktop adapters. Current conversation memory is process-local. Keep
the existing Blueprint as sole owner, autoSync=false, and the same resource sizes.
The shared database and unrelated services remain unchanged.

Remote-computer requirement: support both provisioned managed workers and
user-supplied endpoints, with secure credential references, connectivity checks,
capability discovery and the same takeover/epoch fences. An arbitrary URL alone
is not a compatible desktop connection. Preserve this in the next implementation.

## Approved deployment checkpoint (2026-11-08)

James approved the Render Blueprint ownership change, four-service base budget
of approximately $64.25/month, and initial launch without REIGN database
persistence. PR #4 is merged at `0cd627ea483060b4b41570b9960a7e7a8cf5356f`.
The existing Blueprint `exs-dbofqkm417fc7384kou0` was applied from main with
auto-sync off. Do not ask for the same approval again or create a second pilot.
The legacy OpenTofu workflow is disabled; do not run both owners.

Read [DEPLOYMENT_RECEIPT.md](DEPLOYMENT_RECEIPT.md) for the latest live service,
deploy, startup-fix and verification evidence. That receipt supersedes the
historical pre-deployment recovery notes below. The shared database and existing
Workmates, Git High and Moby Alpha services are outside this deployment.

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
> repository read/write capability. Keep pilot infrastructure ownership in Render Blueprint;
> GitHub is temporary and Git High is the intended long-term source of truth.
> Preserve the tested four-service managed-browser pilot and its release digests.
> Verify the live receipt and finish outstanding acceptance. Continue the
> configurable computer requirement in a separate application change: let
> James create a managed computer or connect an existing one by URL plus secure
> remote-access setup. Keep browser and full desktop capabilities explicit and
> use one Core/AX session contract across adapters. Implement authenticated live
> screen viewing and human takeover with
> stale-command rejection. Build/publish immutable images through the pipeline,
> update the Blueprint only with tested digests, and prepare a concrete
> costed plan before seeking approval for paid changes. Preserve Workmates and
> Git High; never commit secrets. Keep Tailscale/GPU workers deferred. Do not claim
> deployment or E2E success without actual evidence. Run the documented browser
> acceptance flow opening Git High, including James's real takeover and handback.

## Configurable computer requirement (James, 2026-11-08)

James approved adding this requirement to the handoff before continuing in a new
session. This update records the design; it does not implement or deploy it.
Profile/interface types now exist, but functional external adapters and the
Create/Connect setup UI still need implementation. Preserve the shared contract
so the selected computer can change without rewriting Core and AX.

### Two user choices

| Choice | Expected behavior |
|---|---|
| Create a computer | Start a managed browser worker, or a supported desktop environment when desktop capabilities are needed. Provision through the approved single infrastructure owner after any new cost is approved. |
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

## Historical recovery checkpoint, before live apply (2026-11-08)

- PR #1 is merged at `bd01832f2b29c5587e763adbc6f17481fba74f64`.
- Main CI [42527808432](https://github.com/Alambdasystem/Reign-iac/actions/runs/42527808432)
  passed infrastructure validation/mock tests, 45 Python tests, six disposable
  PostgreSQL integration tests, and all four Docker builds plus browser acceptance.
- Release run [42527962724](https://github.com/Alambdasystem/Reign-iac/actions/runs/42527962724)
  rebuilt and tested the exact images before publishing all four to GHCR.
  `deploy/release.json` pins those registry digests with `provision:false`.
  `deploy/provenance.json` records the source and actual acceptance results.
  The downloaded release artifact SHA-256 matched GitHub's artifact digest.
- Anonymous GHCR manifest reads returned HTTP 200 and the exact recorded digest
  for all four images. No private registry credential is needed for these public
  images; actual Render image pulls remain part of live deployment verification.
- Automated acceptance exercised AX login, live frames, Core/Temporal dispatch,
  takeover/handback, stale-command rejection, cleanup, session expiry after a
  worker restart and persisted Temporal history. James's human acceptance and
  live Render acceptance are still **not run**.
- GitHub environments `reign-plan`, `reign-release` and `reign-apply` now exist
  and accept only branch `main`. Apply requires `Alambdasystem` review and
  disables administrator bypass. Self-review remains allowed for the sole
  configured reviewer; no independent second approver has been designated.
- Main branch protection is saved after owner account verification. The API
  confirms `protected:true` and all four required GitHub Actions checks with
  enforcement for everyone. The rule requires PRs, up-to-date branches and
  resolved conversations; administrator bypass, force pushes and deletion are
  disabled. Additional PR reviewer approval is not required for this sole-owner
  pilot; paid infrastructure still requires the separate apply review.
- Jim's Render workspace is `tea-dbdccfpsrm7s73b8rugg`. No REIGN pilot services
  exist there. No live Render services or shared database objects were changed.

## Still outstanding

- Authenticated live dependency readiness and actual Render browser acceptance,
  including restart recovery; consult the receipt for which checks have run.
- James's real takeover and handback signoff, distinct from automated CI checks.
- Configurable managed/external profiles and connection setup. External browser
  and full-desktop adapters remain unsupported until implemented and tested.
- Restricted REIGN database role/schema, migrations and private-network validation
  before enabling database persistence. Do not open the shared DB broadly.
- Measure resource usage, persistent-disk behavior and actual cost after acceptance.
- Authenticated Git High source migration; later separately scoped Tailscale work.

CI success and Render deployment status are different evidence. Never substitute
mocked tests or the public AX health endpoint for authenticated end-to-end testing.

## Approved deployment direction (2026-11-08)

James selected **Render first, Tailscale second, hybrid long-term**.
This is a deployment direction, not approval to bypass infrastructure safety gates
or an authorization to enroll devices, expose ports, or spend an unbounded amount.

1. Complete Core/AX/Computer/Temporal integration and real browser acceptance.
2. Deploy on Render with the approved Blueprint, immutable tested images and
   generated isolated credentials; preserve Workmates and Git High. Reuse an
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

**Current follow-up:** complete the receipt's live acceptance checks and the
separate restricted database setup. The hosted DB connector cannot reach the
shared instance because its external allowlist is empty; do not open it broadly.
OpenTofu state/provider setup is no longer a prerequisite for this Blueprint pilot.

## Historical recovery changes before Blueprint approval (2026-11-08)

The application branch was reconciled with main's four scaffolding commits.
Two incorrect async HTTP response mocks were fixed. CI is consolidated under
Validate REIGN infrastructure, calling reusable application validation. The
four-service smoke exercises the actual AX browser UI and Temporal activities,
including takeover, stale input and restart recovery. CD publishes tested image
digests as a disabled candidate; OpenTofu remains the only deployment owner.
No Render services or live database objects were changed during this recovery.
Live configuration remains blocked on GitHub deployment secrets,
a verified locked HTTP state backend and cost approval.
The hosted DB connector cannot reach the private database.
