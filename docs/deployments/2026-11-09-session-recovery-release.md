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

This receipt selects tested images for promotion. It records no live deployment
of the session-recovery correction.

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

After promotion, record the actual Render deploy results and provider receipt,
then verify that a fresh signed-in tab recovers the same browser without page or
ownership changes. Do not substitute source checks, container smoke, or the
previous live baseline for that deployment acceptance.
