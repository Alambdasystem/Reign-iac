# REIGN speech synchronization and camera descriptions: tested image selection

This promotion selects tested Core and AX images for audible REIGN replies,
audio-driven mouth/expression timing, and a direct Camera → Describe flow in
Workspace. It updates the existing Render Blueprint `exs-dbofqkm417fc7384kou0`
through `deploy/render-pilot.proposed.yaml`.

## Source and required checks

- Implementation: [PR #107](https://github.com/Alambdasystem/Reign-iac/pull/107).
- Reviewed PR head: `8cb670820a7f06c90940960aa602d04b4880f3fb`.
- Merged release source: `4258d4b051b92ecb2937fa8d3bb47149d97ff6ca`.
- [PR validation 44968352583](https://github.com/Alambdasystem/Reign-iac/actions/runs/44968352583): all four required jobs passed.
- [Main validation 44969027307](https://github.com/Alambdasystem/Reign-iac/actions/runs/44969027307): all four required jobs passed on the merged source.
- [Gated release 44969618980](https://github.com/Alambdasystem/Reign-iac/actions/runs/44969618980), job `137046297688`: current-main verification, exact image builds, four-image acceptance, publication and artifact upload passed.

Local validation passed all 158 JavaScript tests and 433 executed Python tests
(450 discovered, 17 environment-dependent skips). The camera smoke test was
corrected to wait for completed room admission before reloading; the final
exact-head gate passed without weakening the application behavior.

## Verified release artifact

- Artifact: [14733158160](https://github.com/Alambdasystem/Reign-iac/actions/runs/44969618980/artifacts/14733158160).
- Name: `tested-release-4258d4b051b92ecb2937fa8d3bb47149d97ff6ca`.
- ZIP size: 7,408,384 bytes.
- Downloaded ZIP SHA-256, matched to GitHub metadata: `20f68f6040ff0d93bd3e4b184a355303b8462d06c41030db3655ec6c1f7ffac1`.
- The release provenance, full acceptance receipt and both controlled-workspace
  phases identify the exact merged source and pass. Saved-account persistence
  after restart also passed.
- The original four-image release manifest is preserved in the artifact with
  `provision: false`. The retired OpenTofu manifests are not used.

## Image selection

| Service | Selection | Immutable digest |
| --- | --- | --- |
| Core | New tested source | `sha256:9ea055bea221c946c613535ce7f5630c5d14ea35cf4bd40ebd0148eb0078bc60` |
| AX | New tested source | `sha256:8583faf0867a996695f69a39f51f18b2db83d97af6ee7d99b740d9893bbb670b` |
| Computer | Retain deployed version | `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` |
| Temporal | Retain deployed version | `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` |

Repositories are `ghcr.io/alambdasystem/reign-{core,ax,computer,temporal}`.
The Blueprint diff contains exactly two image substitutions. Plans, commands,
environment, disks, credentials and service count are preserved; automatic
Blueprint syncing remains off.

The retained worker compatibility paths listed in
[the previous release](2026-11-21-workspace-reign-presence-release.md) are
unchanged between worker source `471ac3235b0cce0026bf0f45d05c192360dfab19`
and this release source. CI assembled all four newly built images from the
artifact. This selected mix retains the deployed Computer and Temporal versions,
so its compatibility evidence also requires the unchanged-path comparison and
the live worker protocol/predeploy gate below.

## Browser acceptance

The published images passed the real Chromium/four-service gate:

- Private narration reached the native audio element, with an active stream,
  unmuted playback and advancing media time. Verified sentence segments selected
  their model expressions; microphone input could not animate the portrait.
- Workspace speech reached the real Web Audio output. PCM silence, voiced
  portions and silent tails produced matching mouth states; sentence expressions
  followed the same output clock. Suspension/resumption, room cleanup, archive
  non-replay and an explicit text-only preference passed.
- Camera T17–T20 exercised a real Chromium synthetic video device, a bounded
  960×540 JPEG, actual authenticated upload 200, the actual sanitized unavailable
  provider 503, and explicit same-ID retry without recapturing or re-uploading.
  Stop, Close, room change and logout-before-acknowledgment released camera
  tracks. No microphone or peer connection was requested; no page errors.
- Existing account, role, room/document, group-call, controlled-browser,
  portrait-cache, neural-Lab and restart checks also passed.

The audio fixtures use synthetic provider/graph responses with real browser
audio playback. The camera fixture overrides only the capability advertisement
so the providerless CI backend can reject the actual image question honestly.
These fixtures do not establish physical-device behavior or live model vision.
The live-provider gate below supplies separate server-side provider evidence.

Mouth opening follows audio energy, with smoothed animation and four existing
semantic expressions. This release does not add phoneme/viseme timestamps.
Camera descriptions use one explicitly shared still; they do not imply
continuous visual awareness.

## Rollout gate

This file records the tested selection before live deployment. The promotion PR
must pass its four required checks. The existing Blueprint plan must identify
that merge and contain only the two selected image changes.

Core retains its required predeploy command:

```sh
env REIGN_HISTORY_MODE=disabled GENESIS_DRAFT_MODE=disabled REIGN_NODES_MODE=disabled REIGN_TEAM_MODE=disabled python -m reign.release_acceptance
```

It verifies the real voice/graph path, live Computer protocol and isolation, and
the real team image/document/expression/speech path. This release adds an exact
private-answer expression annotation check to that provider gate.

After rollout, verify both new Render deploys are live with the selected digests,
confirm the retained worker pins, inspect the sanitized provider receipts and
check the public Workspace. Record authenticated browser results separately
from server and CI evidence. Append the sanitized live receipt to the promotion
PR; credentials, raw plans and private room content do not belong in this public
repository.
