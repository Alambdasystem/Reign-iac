# Workspace and REIGN presence: tested image selection

This promotion selects the tested Core and AX images for automatic REIGN
portrait loading, private/team speech animation, Workspace naming, and bounded
browser-local Lab preferences. It updates the existing Render Blueprint
`exs-dbofqkm417fc7384kou0` through `deploy/render-pilot.proposed.yaml`.

## Source and required checks

- Source implementation: [PR #105](https://github.com/Alambdasystem/Reign-iac/pull/105).
- Reviewed PR head: `5cbb24e2300e920b31761b351fcdb241d8cfb8e9`.
- Merged release source: `5bbd4712a908099c7ec4498a10b616b51215cb9c`.
- [PR validation 44957497304](https://github.com/Alambdasystem/Reign-iac/actions/runs/44957497304): all four required jobs passed.
- [Main validation 44958248547](https://github.com/Alambdasystem/Reign-iac/actions/runs/44958248547): all four required jobs passed for the merged source.
- [Gated release 44958772353](https://github.com/Alambdasystem/Reign-iac/actions/runs/44958772353), job `137012677645`: current-main verification, exact image builds, four-image acceptance, publication and artifact upload all passed.

The validation includes 435 Python tests and the voice, portrait, team and
browser lifecycle suites. Independent implementation review checked image-load
failure behavior, output-only speech metering, account selection, stale saved
image restoration, reset ordering, and resource cleanup.

## Verified release artifact

- Artifact: [14728599564](https://github.com/Alambdasystem/Reign-iac/actions/runs/44958772353/artifacts/14728599564).
- Name: `tested-release-5bbd4712a908099c7ec4498a10b616b51215cb9c`.
- ZIP size: 6,957,900 bytes.
- Downloaded ZIP SHA-256, matched to GitHub metadata:
  `46148a1db2c2eae597dc4803edbf26411ad56b77fb289f8a23ee92bd56fbad20`.
- `release/provenance.json`, its acceptance receipt and both controlled-workspace
  phases identify the exact merged source. All passed; saved-account persistence
  after restart passed.
- The original four-image release manifest is preserved in the artifact with
  `provision: false`. The retired checked-in baseline/OpenTofu manifests are not
  used or changed by this Blueprint promotion.

## Image selection

| Service | Selection | Immutable digest |
| --- | --- | --- |
| Core | New tested source | `sha256:60b7a1e6b5ff21972ffb54f4c5c332f97d8d533db18c1badb1c1bf923df037cf` |
| AX | New tested source | `sha256:dda4c788a316a050bf513b3eb80e95212b2905175dfb32ecb02b632809a3ee4b` |
| Computer | Retain deployed version | `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` |
| Temporal | Retain deployed version | `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` |

Repositories are `ghcr.io/alambdasystem/reign-{core,ax,computer,temporal}`.
The Blueprint diff contains exactly the Core and AX image substitutions.
Existing plans, commands, environment, disk, credentials and service count are
preserved; automatic Blueprint syncing remains off.

The retained Computer version's prior activation is documented in
[the worker release](2026-11-21-team-worker-release.md). The following runtime,
build and transport paths are unchanged between its source
`471ac3235b0cce0026bf0f45d05c192360dfab19` and this release source:

```text
reign/__init__.py
reign/browser_actions.py
reign/browser_proxy.py
reign/computer.py
reign/config.py
reign/credential_transport.py
reign/owned_workflow.py
reign/session_errors.py
reign/team_browser.py
reign/team_browser_acceptance.py
reign/tool_errors.py
reign/workflows.py
services/computer/Dockerfile
services/computer/requirements.txt
services/temporal/Dockerfile
```

The CI assembly used all four newly built images from the artifact, including
Computer `sha256:8d1ef39636fb9370f9c632f530f28f6d609c38e9b65f07dec04dc9a348766bf8`
and Temporal `sha256:de5b6803b75a46dc516443dbf77320927f8fba2b3527bb44470ba6707b6f1e2b`.
The selected deployment retains the older Computer and Temporal pins above.
That exact mixed-image assembly has not been rerun as one CI assembly. Its
compatibility evidence is the unchanged runtime/build/transport paths and the
required live protocol/predeploy and post-deployment checks described below.

## Browser acceptance

The exact published images passed the existing real Chromium/four-service gate.
The default REIGN portrait appeared automatically in the private voice area,
shared room and Lab. No neural model/runtime request occurred for the default
portrait. The custom Lab photo and crop survived reload with mean sampled pixel
error 0, stayed isolated across the two tested accounts, and reset correctly.
The optional real neural model still rendered distinct frames and intermediate
motion, with bounded workers and logout cleanup.

The private voice fixture exercised real synthetic microphone and output audio
energy through the assembled controller. Only verified output animated the
mouth; microphone input could not animate it, and output/call completion cleared
the movement. Controller regressions also cover interruption and blocked
playback. The browser fixture simulates provider/graph events and is not
physical-device or live-provider acceptance.

All 13 initial and 5 restart team cases passed with no page errors, including
saved rooms/documents, employee boundaries, synthetic-device WebRTC transport,
the existing controlled browser, mobile layouts, Workspace display naming and
automatic portrait readiness. The team receipts are bound to the source by the
verified release artifact; they do not contain independent source-SHA fields.

## Rollout gate

This file records a tested selection, before live deployment. The promotion PR
must pass its four required checks before merge. The existing Blueprint plan
must identify that merge and contain only the two selected image changes.

Core retains its required predeploy command:

```sh
env REIGN_HISTORY_MODE=disabled GENESIS_DRAFT_MODE=disabled REIGN_NODES_MODE=disabled REIGN_TEAM_MODE=disabled python -m reign.release_acceptance
```

That command checks the real voice/graph path, the live Computer protocol and
isolation boundary, then the real team image/document/expression/speech path.
After deployment, verify the actual Render image digests and live states,
authenticated owner sign-in, Workspace identity and saved-room access, deployed
frontend bytes, public portrait caching and configured voice. Record the
sanitized live receipt on the promotion PR; no credentials or raw plans belong
in this public repository.
