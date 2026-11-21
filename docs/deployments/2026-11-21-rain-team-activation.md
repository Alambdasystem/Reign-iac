# Rain team activation — stage 2

## Tested sources and selected images

Activate the saved Rain workspace at `/team`, including employee logins, shared
messages and documents, small calls, explicit camera/screen still sharing,
spoken responses with a lightweight animated portrait, and the scoped browser.
Each reply receives a current authorized participant snapshot, separate from
observed image content and prior-message authors.

Select Core and AX from source `e2397129baad380cb038920f7fbbb8b70510629d`.
[PR #102](https://github.com/Alambdasystem/Reign-iac/pull/102), its
[source checks](https://github.com/Alambdasystem/Reign-iac/actions/runs/44886580358),
[main checks](https://github.com/Alambdasystem/Reign-iac/actions/runs/44887087932),
and [gated release 44887639938](https://github.com/Alambdasystem/Reign-iac/actions/runs/44887639938)
all passed. The current-main revision gate succeeded before the independently
reviewed Computer promotion changed main.

Artifact `14701146201`,
`tested-release-e2397129baad380cb038920f7fbbb8b70510629d`, is 5,784,685 bytes.
Its downloaded ZIP matched GitHub's SHA-256:

`92e3e238c3302fc14e1248fae970f83e9e61ac42b8fcd9d79254114854f21c38`

Release provenance and acceptance identify that source. All twelve initial and
four restart team cases passed without page errors, including actual Chromium
WebRTC transport using synthetic devices, media cleanup, current indicators,
durable room/document recovery, employee revocation, and scoped browser frames
and takeover/handback. These are separate from live provider and device checks.

| Service | Selection | Tested source / release |
| --- | --- | --- |
| Core | `ghcr.io/alambdasystem/reign-core@sha256:34ec7a44cd4558c2a5a119ff5b27577b1acb6e5f0a5228ee1fc5a74a940121b7` | `e2397129baad380cb038920f7fbbb8b70510629d` / `44887639938` |
| AX | `ghcr.io/alambdasystem/reign-ax@sha256:ab1ea129dbfacebb58902cb17ee166af2826a911ce89ce138763e1b5f34b0244` | `e2397129baad380cb038920f7fbbb8b70510629d` / `44887639938` |
| Computer | Retain `ghcr.io/alambdasystem/reign-computer@sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` | `471ac3235b0cce0026bf0f45d05c192360dfab19` / `44886154688` |
| Temporal | Retain `ghcr.io/alambdasystem/reign-temporal@sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` | Existing tested baseline; no Temporal change |

The Computer source and presence source have byte-identical worker runtime
imports, Dockerfile/requirements, workflows, browser transport/protocol, and
predeploy protocol check. Sixteen relevant file blobs were compared directly.
The per-service selection uses separately tested releases; it is not claimed to
be a single all-new-image CI matrix. The live protocol gate is mandatory, with
functional live browser verification following activation when the room is quiet.

## Completed worker stage

[PR #103](https://github.com/Alambdasystem/Reign-iac/pull/103) passed all four
[promotion checks](https://github.com/Alambdasystem/Reign-iac/actions/runs/44887135446)
and merged at `bfee9d82f4c3ecc6463baa30dca37e0ec35ec13d`.
The reviewed Render sync `exe-dc0lsfmk1f9s739ihukg` changed only Computer's image.
Deploy `dep-dc0lsiss728c73am5330` became live at
`2026-11-21T09:03:50.765206Z` with the selected digest.

The old instance `srv-dbofvbrrjlhs73bathc0-gj264` logged shutdown completion at
`2026-11-21T09:05:50.744502438Z` and finished server process `[1]` at
`2026-11-21T09:05:50.744695883Z`. This establishes old-process retirement before
team activation. The new instance is `srv-dbofvbrrjlhs73bathc0-zxxzs`.

## Activation scope and gates

Reuse Blueprint `exs-dbofqkm417fc7384kou0` with auto-sync disabled. Change only
the existing Core/AX images, add runtime `REIGN_TEAM_MODE=disk` and
`AX_TEAM_ENABLED=1`, and replace Core's predeploy command with:

```sh
env REIGN_HISTORY_MODE=disabled GENESIS_DRAFT_MODE=disabled REIGN_NODES_MODE=disabled REIGN_TEAM_MODE=disabled python -m reign.release_acceptance
```

The existing private Core disk stores encrypted team records. There are no new
services, plans, disks, database permissions, credentials or remote adapters.
The mountless predeploy process must pass, in order:

1. Existing real provider voice/graph acceptance.
2. Read-only live Computer protocol 1 and wrong-scope rejection checks.
3. A real provider synthetic image, structured expression, document and matching
   non-silent speech probe.

Failure blocks the new Core deployment. Runtime keeps its durable store modes
enabled. After both services are live, verify exact deployed assets, owner and
employee access/revocation, a document write/read, and one real spoken room reply.
Exercise the live shared browser only when doing so will not interrupt an active
room or private browser; distinguish a skipped check from a passed one.

## Pilot limits

The team page loads the approximately 320 KB portrait atlas rather than an ONNX
model. Speech is turn-based with sentence expression cues and audio-driven mouth
movement, not phoneme alignment. The separate Avatar Lab still performs local
neural inference with motion interpolation. Images sent to REIGN are explicitly
shared stills; group camera sharing does not grant continuous AI video access.
Four human call connections use STUN without a configured TURN relay. Camera,
screen and voice recordings are not archived. Remote employee desktops remain
deferred, and the managed browser retains its existing host allowlist and single
session capacity. Physical-device call quality remains a separate human check.

This file records the reviewed activation selection. The merged promotion's
verification notes and Render deployment history establish subsequent live results.
