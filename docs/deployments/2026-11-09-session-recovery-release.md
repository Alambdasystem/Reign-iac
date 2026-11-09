# REIGN browser session recovery release — 2026-11-09

## Source and validation

[PR #15](https://github.com/Alambdasystem/Reign-iac/pull/15), head
`63427e282bf66d9c54b1faa48cee1bcb03fbe509`, passed all four required checks in
[PR validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/42553163343)
and merged as source `566fab179f50e999f892285760a59d1b88864d7c`.
[Main validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/42553450110)
also passed all four checks on that source.

The [gated image release](https://github.com/Alambdasystem/Reign-iac/actions/runs/42553677503),
publish job `128954562651`, passed on the same source. Its
[verified release artifact](https://github.com/Alambdasystem/Reign-iac/actions/runs/42553677503/artifacts/13645584445)
is `tested-release-566fab179f50e999f892285760a59d1b88864d7c`, 1,158,921 bytes.
The downloaded ZIP matches GitHub's SHA-256
`114b161c5358e0f47448b0fae1f9ba69f2cacc9eab6f1893ce27174794f12ddf`.
The artifact's provenance and acceptance identify the same source; its tree is
`2e34035369f2e86722b1ec753fd9ddd19aa88421`.

The following sections record tested image selection and its baseline evidence.
Subsequent rollout results are recorded in the [actual deployment receipt](#actual-deployment-receipt)
below, with unfinished acceptance kept explicit.

## Tested acceptance

Main validation passed 88 Python tests, 37 Node voice-controller tests, and six
PostgreSQL integration tests. The gated release tested its exact four images
before publishing them; authenticated UI, live frames, takeover/handback,
stale-command rejection, cleanup, restart expiry, and Temporal history
persistence passed.

Real four-service session-recovery acceptance passed both empty and stale
session-storage cases. It verified blocked startup actions during discovery,
the same session/page/control owner/epoch afterward, continuing fresh frames,
no create/navigation/control dispatch during recovery, preserved human takeover
and stale-input fences, and the distinct occupied-browser HTTP 409.

The seven voice UI checks used simulated provider, graph, and microphone events.
The artifact explicitly records `live_voice_audio: not_run_no_provider_key`
and `human_acceptance: not_run`; these checks do not establish live speech or
physical audibility.

## Behavior in this release

An authenticated tab can discover the existing active browser when its local
session reference is absent, invalid, or expired. Recovery attaches to the existing
page and fetches fresh frames while preserving the session ID, URL, control owner, and
epoch. It does not create, navigate, close, or take control of the browser.
Workspace actions remain disabled while restoration is pending.

A full worker now returns the specific `browser_occupied` HTTP 409 instead of
being reported as an unavailable service. AX explains the occupied session and
can reconnect through read-only discovery; it never replays the open request.
LangGraph distinguishes capacity conflicts from control changes.

Restoring a completed conversation bound to a confirmed expired browser clears
only the tab's local conversation reference and explains the change. Running
or paused graph work remains tracked; recovery sends no cancellation or deletion.
Work and Tools labels describe configured capabilities without implying an
external-site login or a verified provider connection.

See [application contracts](../APPLICATION_CONTRACTS.md#browser-session-recovery)
for the authenticated single-administrator scope and recovery acceptance cases.

## Live baseline before this correction

These observations were made against the previous deployment; they do not
establish that the new discovery/recovery code is live.

- The first model-driven open encountered an occupied browser. The worker's
  capacity conflict was reported upstream as HTTP 503, motivating this fix.
- After the old session expired through its idle timeout, actual model
  `open_browser` and `browser_status` calls succeeded. The returned URL was the
  Git High root, the title was GITHI, and the owner was the agent. Usage was
  3,101 input / 79 output tokens.
- During human takeover, selecting Explore reached “Sign In - GITHI”. No
  credentials were entered.
- A model-requested navigation paused while the human held control. After
  Return control and Resume, `navigate_browser` and `browser_status` completed,
  with 3,249 input / 48 output tokens.
- The test browser session was closed through the UI.

## Tested images selected for Blueprint promotion

Reuse Blueprint `exs-dbofqkm417fc7384kou0` with `autoSync=false`. Only the tested
Core, AX, and Computer image pins change:

| Service | Immutable image selected for promotion |
| --- | --- |
| Core | `ghcr.io/alambdasystem/reign-core@sha256:a69575f23ad6dbae9dc03ecad615b04e36ed78132eea6b58133932b8d520b03f` |
| AX | `ghcr.io/alambdasystem/reign-ax@sha256:59377ed4706d8063db10c6a55ad7e199a2602eb49d6cf63347218491b955e1b6` |
| Computer | `ghcr.io/alambdasystem/reign-computer@sha256:b14eb99a2386a9080227b248a41ba5e30736a02699a8a0cd18a9bfdbcc1e1b52` |

Temporal retains
`ghcr.io/alambdasystem/reign-temporal@sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4`.
All instance plans, environment configuration, disk settings, and pre-deploy
commands remain unchanged. Workmates, Git High, Moby Alpha, and the shared
database are outside this promotion.

Core retains `preDeployCommand: python -m reign.voice_acceptance`. The real
provider/graph speech probe must pass for the new Core deployment. Its WebSocket
synthetic-audio check remains distinct from browser WebRTC and physical-device
audio acceptance.

The deployment and browser results below provide separate live evidence from
the source checks, container smoke, and pre-correction baseline above.

## Actual deployment receipt

### Promotion and rollout

[PR #16](https://github.com/Alambdasystem/Reign-iac/pull/16), head
`b79e6eb3c4bfb4c982f7482d5e01fd1ef7d47bf3`, passed all four required checks in
[promotion validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/42554716169)
and merged at `c3035f3b30136aefd68ec56c4d4d65d0a9d69912`.
Blueprint sync `exe-dbojjvbrjlhs73bm4qd0` was accepted on November 9 at
03:12:13Z. Its plan contained exactly the three image updates above, with no
manual environment changes.

| Service | Deploy | Observed status | Live time (UTC, November 9) |
| --- | --- | --- | --- |
| AX | `dep-dbojk3jrjlhs73bm55s0` | Live | 03:12:27.010927Z |
| Core | `dep-dbojk3jrjlhs73bm556g` | Live | 03:13:00.857632Z |
| Computer | `dep-dbojk3jrjlhs73bm55dg` | Live | 03:13:06.743581Z |

All four service image pins and plans were checked against Render service
metadata. Temporal retained its existing pin; the three corrected services use
the tested image digests above.

### New Core provider acceptance: passed

The real provider pre-deploy probe passed all nine stages from
**2026-11-09T03:12:23.570181Z** to **2026-11-09T03:12:35.499682Z**.
This is a new probe result for this rollout; the earlier
[voice acceptance JSON](2026-11-09-voice-acceptance.json) is preserved unchanged.
The exact sanitized result is saved in the
[new provider JSON](2026-11-09-session-recovery-voice-provider.json).

| Measurement | Observed result |
| --- | --- |
| Transport / input | WebSocket / `synthetic_pcm_manual_commit` |
| Realtime model / voice | `gpt-realtime-2.1` / `marin` |
| Input fixture | SHA-256 `f37494a5143fb194b212e03ada62400ae5b8432feb8e8f005bbbb3885d5d69eb`; 5.435 seconds; 24 kHz |
| Actual graph tool | LangGraph `gpt-6-astra`; `workspace_status` |
| Graph usage | 1,245 input / 36 output tokens |
| Returned PCM | 292,800 bytes; 6.1 seconds; normalized RMS 0.070144 |
| Output transcript | Present |
| Provider event count | 125 |
| Probe browser WebRTC / physical device | `not_run` / `not_run` |

### Corrected-deployment browser/tool acceptance: passed

The first authenticated AX reload at approximately 03:13:20Z reached the new
AX but an old Core instance, which returned HTTP 422 for active-session
discovery. At approximately 03:14:17Z, the new Core reached the retiring
Computer instance, which returned HTTP 404 for `/sessions/active`. Instance
correlation in the application logs established mixed-version routing during
the rolling overlap.

Old AX retired at 03:14:26.945060Z, old Core at 03:15:00.852962Z, and old Computer
at 03:15:06.658321Z. A reload after retirement reached Ready and enabled Start
voice. The prior graph thread expired at Core restart, as expected for
process-local conversation state; this is not evidence of persisted chat.

The completed test used ordinary prompt text through REIGN's LangGraph path:

| Stage | Observed live result |
| --- | --- |
| Open and inspect | `open_browser` and `browser_status` opened the Git High root, title GITHI, under agent control; 2,120 input / 80 output tokens |
| Take control | Owner changed to human, epoch 1, session `04b5a49a-f291-488e-91b3-7df6f57de75a` |
| Fresh-tab recovery, 03:16:08Z | The same session, page, human owner, epoch 1, and fresh frames appeared; the feed reported that page and control owner were preserved |
| Navigation while human-controlled, 03:16:30Z | A natural request to navigate to Explore entered a real paused state; Resume was disabled |
| Handback and resume | Return control restored agent ownership and enabled Resume at 03:16:40Z; Resume was clicked at 03:16:44Z |
| Resumed navigation | `navigate_browser` completed; actual title “Sign In - GITHI”; 736 input / 56 output tokens |
| Cleanup | Close session reported “Session closed and browser resources released”; the UI returned to Ready with no active session |

The navigation request targeted
[Git High Explore](https://alambda-forge.onrender.com/explore/repos) and expressly
excluded login or registration submission. The actual returned URL was
[https://alambda-forge.onrender.com/user/login?redirect_to=%2Fexplore%2Frepos](https://alambda-forge.onrender.com/user/login?redirect_to=%2Fexplore%2Frepos).
No form was submitted and no credentials were entered.

Independent application-log correlation confirmed successful discovery through
the new AX, Core, and Computer instances, including the fresh-tab request at
03:16:06.235–03:16:06.237Z. Recovery added no mutation calls. Model opening uses
internal CoreTools, so an HTTP session-create count is not its execution proof.
The resumed request returned HTTP 200 at 03:16:44.231Z. Cleanup returned DELETE
200 through all three layers at 03:17:16.747–03:17:16.752Z; the worker's subsequent
session GET returned 404 at 03:17:17.923Z, confirming removal.

Evidence: [reviewed final browser screenshot](2026-11-09-session-recovery-browser.jpg),
captured at **2026-11-09T03:17:16.212Z**, before session cleanup. It contains the
generic app/browser result and no credentials.

### Remaining boundaries

The live recovery test above used a fresh tab; stale-reference recovery is
separately covered by the real four-service CI checks. External account login,
account/repository creation, credential-vault use, and Outlook integration were
not completed. Reaching a sign-in page does not establish authentication.

The cloud browser's microphone remained unavailable. The real provider probe
does not establish browser WebRTC, spoken input through the deployed UI, or
physical microphone/speaker audibility. James's physical-device voice and
human takeover signoff remain separate from this automated browser acceptance.
