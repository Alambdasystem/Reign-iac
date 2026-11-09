# REIGN browser tools and durable vault release — 2026-11-09

**Release prepared; not deployed. The additional disk cost and Blueprint
application still require concrete user approval.** This document records tested
source and image provenance, not a live deployment.

## Source and verified PR evidence

[PR #18](https://github.com/Alambdasystem/Reign-iac/pull/18), head
`22a05516ab139c095220774e67150da33962f2a5`, passed all four required checks in
[validation run 42562768728](https://github.com/Alambdasystem/Reign-iac/actions/runs/42562768728).
It merged as main source **`08bad55dc8bd483fe78b13f90ae249872ace71af`**.

That PR workflow checked out synthetic merge commit
`25530e769050622573a4fe2227ae52412fa18298`, merging the PR head into base
`289e06e3ba1bbc132e9ea6594e40368fdba6dc74`. The PR smoke receipts identify that
synthetic merge SHA. Exact-source main
[validation run 42563465419](https://github.com/Alambdasystem/Reign-iac/actions/runs/42563465419)
subsequently passed all four required checks for `08bad55dc8bd483fe78b13f90ae249872ace71af`.
The exact-source gated release also passed; its archive, manifest and acceptance
provenance were verified separately below.

The successful [container job 128982861573](https://github.com/Alambdasystem/Reign-iac/actions/runs/42562768728/job/128982861573)
executed **35 cases in 9.515 seconds: 19 boundary cases and 16 real Chromium cases**.

| Executed coverage | Cases |
| --- | ---: |
| Action boundary checks | 9 |
| Proxy boundary checks | 10 |
| Real Chromium browser actions | 13 |
| Real Chromium HTTPS redirects | 3 |

The redirect cases verified allowed 303 redirects with cookie and page-URL
preservation, a forbidden 307 blocked before the target received a POST, and
credential-origin pinning against another otherwise permitted host.

## Actual stack and controlled acceptance

The real four-service smoke passed authenticated AX access, live frames,
takeover/handback, stale-command fencing, session recovery, and cleanup. Accounts
UI checks covered secure save/list, duplicate preservation, confirmed removal,
password clearing, authentication expiry, mobile layout, and absence of account
data from browser storage/history/chat. The final `accounts_restart.status`
was `passed`: metadata survived Core restart and the disposable account was
removed. This completed the initial UI receipt's `awaiting_restart_check` state.

The separate receipts explicitly identify **`scripted_model`** and
**`controlled_site`**. They used real Core/Computer HTTP, stdio MCP, LangGraph,
Temporal, Playwright, the authenticated destination proxy, a loopback HTTPS
fixture server, and an encrypted SQLite vault on a disposable mounted volume.
The controlled runner used an internal Docker network without public egress.

| Controlled receipt | Initial | Fresh-container restart |
| --- | ---: | ---: |
| Result | Passed | Passed |
| Credential-save requests | 1 | 0 |
| Previously saved account reused | First save | Passed; same account ID |
| Completed workflows | 7 | 7 |
| Temporal history events scanned | 77 | 77 |
| Controlled private repository creations | 1 | 1 |

Each phase completed one `OpenBrowser`, one `FillAccount`, one
`NavigateBrowser`, and four `BrowserAction` workflows. Both native
POST → 303 → GET chains were confirmed through browser redirect references,
cookie authentication, and final authenticated/private-repository DOM. Human
takeover paused the graph, resume was rejected while human control remained,
and handback resumed the navigation once. Credential scans passed for
model/tool messages, graph checkpoints, public responses, vault files, and
Temporal histories.

## Release behavior and boundaries

Browser tools operate through the private MCP server and the existing LangGraph
control checkpoints. Browser effects retain Temporal and ownership-epoch fences.
The destination proxy constrains redirect hops as well as initial requests.

Manual credentials enter the authenticated Accounts form. The vault encrypts
stored credentials and issues short-lived, single-use, page-bound grants to the
worker. The model receives account references. Exact repeated saves are
idempotent; changed duplicates return `account_exists` without replacing the
saved login. Signup preparation creates a saved credential only. External signup
finalization, terms, MFA, CAPTCHA, and passkeys remain human-controlled steps.

The controlled receipts report **zero external accounts and repositories
created** and `live_provider_tested: false`. They establish neither live-model
planning nor successful login/signup/repository creation on a real external
account. Voice UI interlock checks used synthetic provider/microphone inputs;
this CI did not exercise physical audio or a live voice provider. Prior voice
acceptance is recorded separately. Outlook integration remains deferred.

## Verified main release provenance

| Evidence | Verified value |
| --- | --- |
| Main source | `08bad55dc8bd483fe78b13f90ae249872ace71af` |
| Main source tree | `82ab68e4935cd95c70d66b184200dc5414f55e8c` |
| Exact-source main validation | [Run 42563465419](https://github.com/Alambdasystem/Reign-iac/actions/runs/42563465419): all four required checks passed |
| Gated image release | [Run 42563778434](https://github.com/Alambdasystem/Reign-iac/actions/runs/42563778434), publish job `128985712829`: passed |
| Release artifact | [Artifact 13650572954](https://github.com/Alambdasystem/Reign-iac/actions/runs/42563778434/artifacts/13650572954), `tested-release-08bad55dc8bd483fe78b13f90ae249872ace71af`, 1,148,723 bytes |
| Archive integrity | Locally verified SHA-256 `fe6cea1369411d12da7aeff053f3f9fc5429cd079846e1add598056f85408f15` matches GitHub artifact digest |
| Manifest/acceptance provenance | Four-service acceptance and both controlled phases passed for the exact main source; Core-restart account reuse passed |
| Core | `ghcr.io/alambdasystem/reign-core@sha256:88df97143dfdea2796dd9035a54cec7fe0defbbfdee3a7f55cdd4ed4ad9e8046` |
| AX | `ghcr.io/alambdasystem/reign-ax@sha256:86a2805e6f07da294c084fd994d4f3b7a662b91cad4ce88a599f9e46dfd73ba1` |
| Computer | `ghcr.io/alambdasystem/reign-computer@sha256:b2dbbefe732e56d2526391dcb9116051c3c10f5bc674e3751f91ebc1f0128faa` |

Machine-readable evidence: [verified provenance](2026-11-09-browser-vault-provenance.json).
The release gate rebuilt and tested the exact published images before push.

Retain the current Temporal pin:
`ghcr.io/alambdasystem/reign-temporal@sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4`.

## Proposed Blueprint promotion — not authorized or applied

The existing Blueprint `exs-dbofqkm417fc7384kou0` remains the sole owner, with
`autoSync=false`. The proposed change retains four services and their current
plans, region, and instance counts. It promotes only verified Core, AX, and
Computer images and adds a **1 GB Core disk at `/var/data/reign-vault`**.
Temporal storage, Workmates, Git High, Moby Alpha, and the shared database remain
outside the change.

The proposed disk adds **$0.25/month**, changing the approved **$64.25/month**
pilot base to approximately **$64.50/month**, before existing variable charges.
Approval of that concrete increase is pending. The vault key stays in Core;
a separate credential-channel key is shared only with Computer. Secret values
must not appear in this receipt or the Blueprint.

[Render disks](https://render.com/docs/disks) make Core deployments interrupt
service and are unavailable to pre-deploy commands. Keep the existing voice
pre-deploy gate, and verify the vault's authenticated `ready` and `durable`
status after deployment. The runtime entrypoint must leave the actual service
process under UID 10001. Service references, generated-key wiring, and the exact
resource diff require validation in the existing Blueprint's plan.

Activation still requires reviewing the exact plan from the configured main branch,
obtaining the concrete cost/application approval, and observing the rollout.
No Blueprint sync or deployed vault/browser acceptance is represented by this
release receipt. Record those separately after activation. Wait for retiring
Computer workers to drain before testing the new action workflows.
