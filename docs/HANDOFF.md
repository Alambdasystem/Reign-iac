## Current checkpoint: live browser/vault acceptance; source fixes pending (2026-11-09)

Deployed application source `08bad55dc8bd483fe78b13f90ae249872ace71af` has now
executed live prompt-driven `workspace_status` and `vault_status` checks through
the authenticated AX interface. The vault reported `ready:true` and
`durable:true`. Live browser prompts opened and observed Git High, then used a
visible Sign In control to reach its login page. Further live checks completed
navigation, scrolling, ordinary fill/key input, private test-login fill, reuse
of the same vault entry in a fresh browser, human takeover/handback/resume,
stale-observation rejection and recovery, and paused-task cancellation.
Disallowed destinations and invalid vault requests were rejected. A signed-out
account-list request required login. The disposable vault entry was removed and
the managed browser was closed after testing. These observations do not establish
external account authentication or creation, repository creation, live at-rest
cryptographic inspection, or cancellation of an already dispatched activity.

Two narrow source corrections are prepared and **pending deployment**: ordinary
requested links may use fresh element references with the existing destination
guard, and a confirmed expired chat clears only the tab's conversation reference
while preserving its recovered browser. Recovery does not cancel or replay work;
running/paused conversations and temporary lookup failures remain tracked.
Focused local validation passed 41 Node tests and 19 agent tests. The detailed
live acceptance report and browser evidence remain outside this public repository.

## Previous release-planning checkpoint: before vault activation (2026-11-09)

The following section records the pre-activation plan. Its deployment and
approval status is historical; the live checkpoint above records later evidence.

James confirmed **voice works on his own device**. That supersedes the earlier
remaining user-device acceptance item; the cloud test's missing microphone stays
recorded as historical evidence. Outlook is explicitly deferred until browser
login or a Graph connection is requested. “SCP” is interpreted as MCP from context.

[PR #18](https://github.com/Alambdasystem/Reign-iac/pull/18) merged the fourteen
private MCP tools, LangChain/LangGraph integration, interactive Playwright actions,
encrypted account vault and Work → Accounts dialog. Chat and Work remain; the
Agents/Tools tabs and shortcuts are removed. The exact Git High destination,
human takeover and control epochs are retained. Chat checkpoints and browser
cookies remain process-local; saved-account durability is separate.

Main source `08bad55dc8bd483fe78b13f90ae249872ace71af` passed all four required
checks in run `42563465419`. Gated release `42563778434` rebuilt, tested and
published immutable images. Artifact `13650572954` was downloaded and its SHA-256
verified. The promotion proposal selects that release's Core, AX and Computer
images while retaining the existing Temporal image. See the
[verified release receipt](deployments/2026-11-09-browser-vault-release.md),
[tool/account guide](TOOL_AND_ACCOUNT_REQUIREMENTS.md) and
[application contracts](APPLICATION_CONTRACTS.md).

**These changes are not deployed.** The existing Blueprint remains the only
infrastructure owner with `autoSync=false`. The proposal adds a dedicated 1 GB
Core disk and two managed keys, with no compute-plan changes. This adds about
$0.25/month to the previously approved ~$64.25/month base. AGENTS.md reserves
concrete authorization for that cost increase and Blueprint application.
Prepare and review the actual plan from main before asking for the final approval;
do not apply without it. Do not use ephemeral storage, Temporal's disk, Workmates
or the shared database for the vault.

The passing controlled login and private-repository cases use a scripted model
and local HTTPS site through real MCP, LangGraph, Temporal and Chromium. Both
initial and fresh-container restart passed, including saved-account reuse and
credential-leak checks. The real four-service Accounts UI save/list/remove and
Core-restart persistence also passed. Actual external account creation, live-model
login/repository creation and deployed vault readiness still need their own
proof after activation. The physical-device voice result is James's confirmation,
not a CI claim. Keep the real voice pre-deploy gate. After approval and rollout,
verify vault `ready:true` / `durable:true` and let old Computer workers drain before
exercising the new browser actions.

## Previous deployed checkpoint: session recovery and browser/tool acceptance (2026-11-09)

Session recovery source `566fab179f50e999f892285760a59d1b88864d7c` (PR #15)
passed validation and its tested-image release. Promotion PR #16 merged at
`c3035f3b30136aefd68ec56c4d4d65d0a9d69912` after all four required checks passed.
Blueprint sync `exe-dbojjvbrjlhs73bm4qd0` was accepted at 03:12:13Z with exactly
three image updates: Core, AX, and Computer. Temporal, plans, environment
configuration, disks, and pre-deploy commands remain unchanged.

AX is live at 03:12:27.010927Z, Core at 03:13:00.857632Z, and Computer at
03:13:06.743581Z. All four service image pins and plans were verified, including
the retained Temporal pin. Initial recovery requests hit retiring Core/Computer
instances during rolling overlap; retries succeeded after those instances
stopped. The old process-local graph thread expired at Core restart. Browser
session recovery does not provide persisted chat continuity.
See the [release and rollout receipt](deployments/2026-11-09-session-recovery-release.md#actual-deployment-receipt).

Final live browser/tool acceptance passed. An ordinary prompt ran actual
`open_browser` and `browser_status` tools against the Git High root (2,120 input /
80 output tokens). After Take control, a fresh signed-in tab recovered the same
browser, page, human owner, epoch 1, and fresh frames at 03:16:08Z. A subsequent
natural navigation request paused under human control with Resume disabled.
Return control enabled Resume; the resumed `navigate_browser` reached
“Sign In - GITHI” through the Explore redirect (736 input / 56 output tokens).
No login or registration form was submitted. UI confirmation plus successful
delete responses and a subsequent session 404 verified cleanup by 03:17:17.923Z.
See the [reviewed final browser result](deployments/2026-11-09-session-recovery-browser.jpg).

The new Core's real voice pre-deploy gate passed all nine stages at
03:12:23.570181–03:12:35.499682Z. Realtime `gpt-realtime-2.1` / `marin` delegated
to actual LangGraph `gpt-6-astra` execution of `workspace_status` (1,245 input /
36 output tokens) and returned 6.1 seconds of non-silent PCM speech. This remains
a WebSocket synthetic-input/manual-commit probe; browser WebRTC and physical
microphone/speaker acceptance are not run by it. The
[new sanitized provider receipt](deployments/2026-11-09-session-recovery-voice-provider.json)
is separate from the unchanged earlier receipt.

The correction reconnects signed-in tabs to the existing browser without
changing its page, control owner, or epoch. It preserves a distinct occupied
session error and avoids replaying open requests or cancelling another graph
run. CI also passed stale-reference recovery, 88 Python tests, 37 Node tests,
and six PostgreSQL integration tests. That additional stale-reference case is
CI evidence; the live recovery observation above used a fresh tab.

Remaining: test voice in a browser with an available microphone and complete
James's physical microphone/speaker check. The cloud browser has no input
device, so browser WebRTC/voice delivery remain unverified. External login,
account/repository creation, saved-credential vault use, and Outlook integration
were not completed; the current agent exposes status and allowlisted browser
open/navigation tools only. Durable graph checkpoints and external desktop
adapters remain outstanding.

## Previous checkpoint: voice deployed; cloud browser has no microphone (2026-11-09)

The corrected Core and AX voice release is live at
[REIGN](https://reign-ax-pilot-d934.onrender.com/). Implementation PR #10 and
correction PR #12 produced application source
`322089a80640441808e439c7e5a6d6cf4adb9311`. Promotion PR #13 merged at
`6ab1e83413473ac65b22c9b4d45cda3d69ea4177`. Required validation and the tested-image
release passed; AX became live at 01:24:23Z and Core at 01:25:01Z on November 9.
See the [deployment and acceptance receipt](deployments/2026-11-09-voice-acceptance.md)
and [sanitized provider receipt](deployments/2026-11-09-voice-acceptance.json).

The real provider gate passed all nine stages at 01:24:24–01:24:36Z. Synthetic
speech reached `gpt-realtime-2.1` / `marin`, delegated to actual LangGraph
`gpt-6-astra` execution of `workspace_status`, and returned 5.6 seconds of
non-silent PCM speech. The first attempt failed because audio preceded the graph
result. The correction uses text-only delegation and enables audio explicitly
for verified result narration; the premature-audio failure check remains intact.

This proves the provider/graph speech path over WebSocket with synthetic PCM and
manual turn commitment. Secure browser reauthentication subsequently succeeded,
resolving the earlier sign-in blocker. The corrected live workspace displayed
Ready and enabled Start voice. Starting voice reported “No microphone was
found. Connect or enable a microphone and try again.” Start voice returned;
Mute and End call were removed. This input-device failure occurs before peer
creation, SDP gathering, or the offer request. Browser WebRTC, media delivery,
Mute/End call, and physical audibility remain untested.

A subsequent typed request ran the actual `workspace_status` tool through the
authenticated live UI. Core, Temporal, and Computer all reported Ready; usage
was 1,087 input / 38 output tokens. The
[reviewed screenshot](deployments/2026-11-09-voice-browser.jpg), captured at
02:23:05.838Z, records the browser outcome without authentication values.

Next action: use a browser with an available microphone to test Start voice,
the spoken greeting, a spoken shared-conversation request, Mute, interruption,
and End call, recording actual playback and cleanup. James's phone microphone
and speaker have not been tested. CI passed 78 Python tests, 37 Node voice tests,
six PostgreSQL integration tests, and seven simulated voice UI checks; those
simulations do not replace this browser/device pass.

The existing Blueprint remains the sole owner with `autoSync=false`. Only Core
and AX image pins changed. Computer/Temporal pins, instance plans, Workmates,
and the shared database remain unchanged. Durable checkpoints, restricted REIGN
database access, and external computer adapters are still outstanding.

## Historical voice implementation candidate (2026-11-08)

This source-only checkpoint predates the deployed release above.

James requested that REIGN's missing voice be implemented using the Workmates
integration. The new candidate adds a server-only Realtime SDP broker and native
WebRTC voice controls. Every spoken request is delegated to the existing
LangGraph conversation, with the same browser session and takeover fences.
See [VOICE.md](VOICE.md) for configuration, lifecycle, and verification levels.

This checkpoint recorded source work without claiming deployment or audible
device acceptance. Its promotion requirements were passing validation/release
gates and tested Core/AX images applied through the existing Blueprint, preserving
Computer/Temporal digests, resource sizes, Workmates, and the shared database.
The current checkpoint above records the subsequent deployment and provider test.

## Historical live checkpoint: UltraView + LangChain/LangGraph (2026-11-08)

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

Remaining at this checkpoint: voice, durable LangGraph checkpoints, restricted
REIGN DB access and external desktop adapters. The current checkpoint above
supersedes the voice status. Conversation memory is process-local. Keep
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

Read [DEPLOYMENT_RECEIPT.md](DEPLOYMENT_RECEIPT.md) for the initial live service,
deploy, startup-fix and verification evidence; use the current checkpoint above
for subsequent releases. The initial receipt supersedes the historical
pre-deployment recovery notes below. The shared database and existing
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

## Remaining work after the 2026-11-09 voice release

- Browser voice acceptance on the corrected live release using an available
  microphone; the cloud browser's missing input device blocked this attempt
  before SDP negotiation. James's physical-device audio check remains pending.
  Consult the deployment receipts for completed dependency checks and which
  deployed browser/restart acceptance remains unverified.
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
