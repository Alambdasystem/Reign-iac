# Workspace call and conversation UI: tested frontend image

This receipt selects the tested AX image for the resizable Workspace UI.
It targets the existing Render Blueprint `exs-dbofqkm417fc7384kou0` through
`deploy/render-pilot.proposed.yaml`, with automatic Blueprint syncing off.
This is an image-selection record; live deployment is verified separately.

## Source and release checks

- Implementation: [PR #119](https://github.com/Alambdasystem/Reign-iac/pull/119).
- Reviewed PR head: `cdf8084bac2144867fc4ad88282af6d3d77d52c0`.
- Merged release source: `8044ffd24d9cff92bd701c77aedfbf3ef09fbd43`.
- Source tree: `556b33e31a3a8a18d6e2f0628c71b9d1cd48a8c5`.
- [PR validation 44998148962](https://github.com/Alambdasystem/Reign-iac/actions/runs/44998148962): all four required jobs passed.
- [Main validation 44998942989](https://github.com/Alambdasystem/Reign-iac/actions/runs/44998942989): all four required jobs passed.
- [Gated release 44999444416](https://github.com/Alambdasystem/Reign-iac/actions/runs/44999444416), job `137137523344`: exact-source guard, image builds, offline daemon smoke, acceptance, image publication and artifact upload passed.

The downloaded CD artifact is
[14745272694](https://github.com/Alambdasystem/Reign-iac/actions/runs/44999444416/artifacts/14745272694),
named `tested-release-8044ffd24d9cff92bd701c77aedfbf3ef09fbd43`.
Its 6,888,634-byte ZIP matches GitHub's artifact metadata with SHA-256:

`4336d17e7edc0d0eceaf9680a86dd299d07baa4ccd49b32cdc138417f00d357f`

The release manifest retains `provision: false`. Provenance and full acceptance
identify the exact merged source. Both controlled-workspace phases pass, including
saved-account persistence after process restart. No failure receipts are present.

## UI behavior and acceptance

The layout places settings at the upper left, the rooms menu at the upper right,
End and microphone at the lower left, and the composer and its add control at the
lower right. The existing REIGN portrait supports Chat, Together and Face views
through drag, tap, keyboard controls and explicit view buttons. Face hides the
transcript; shrinking the portrait restores it.

The presentation layer moves the original controls and media nodes. It retains
the existing room, account, document, browser, camera and voice controllers.
Send and Ask REIGN remain distinct actions. The shared mute and End controls
delegate to enabled underlying call controls, including both calls when REIGN
voice and group calling are active.

The gated-release browser receipts show:

- **19 initial and five restart cases passed**, with zero page errors.
- Group-call mode changes retained the same two participant videos, media streams,
  active tracks, draft, transcript, portrait canvas and scroll position.
- Chat, Together and Face captures waited for completed layout transitions.
  Full Face geometry matched the viewport and its transcript was hidden and inert.
- Existing long drafts resized at **360, 390, 430 and 1280 CSS pixels** without
  requiring another input event; message actions remained clear of the transcript.
- Continuous voice captured **21,510 ms**, **three turns**, **one offer**,
  **one microphone acquisition** and **zero recorders**. It received 86,544 bytes
  in 1,080 RTP packets while mode changes preserved the connection and local state.
- Existing camera gesture, cleanup, room authorization, revocation, durable chat,
  document conflict, browser-control and logout cases passed.

These are real browser and loopback media transports using synthetic devices
and scripted providers. They do not establish physical microphone or speaker
performance, live-model responses, or a deployed user conversation.

## AX-only image selection

| Service | Selection | Immutable digest |
| --- | --- | --- |
| AX | New tested release | `sha256:1759dc0578467f4e0988083756ee5d3ca77552703d4843b2fc1370ac06e37c3e` |
| Core | Retain current Blueprint pin | `sha256:e05987e4e92adc68548ecc3c0f641feb58e8a642ac428fb2ac7cb850c04ad31a` |
| Computer | Retain current Blueprint pin | `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` |
| Temporal | Retain current Blueprint pin | `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` |

Image repositories are `ghcr.io/alambdasystem/reign-{ax,core,computer,temporal}`.
The previous AX pin is `sha256:6ed0d8192bdb4f275a1f3bae9684412fd2d2b0806c3b927af2bd669ca4a7987d`.

Only AX presentation assets and its HTML integration change in the application.
The `reign`, `services/core`, `services/computer` and `services/temporal` trees
are identical between the selected Core source
`1a2b163eb4634db7877dd8a6e32728582c7b3d59` and this release source. Existing
worker pins are retained as approved in the preceding continuous-voice release.
The CD acceptance assembled all four freshly built images; this selective
deployment is not represented as that same four-image assembly.

## Promotion and live verification

The promotion changes one AX image URL and adds this receipt. It preserves
fresh-main Core/Tailscale settings, startup commands, predeploy gate, plans,
disks, credentials and service count. If runtime changes land after the tested
source, obtain a combined validated release before promotion.

The promotion PR must pass the existing required checks. Review the existing
Blueprint plan for a single AX image update, then apply the authorized rollout.
Verify the resulting Render deployment is live with the selected immutable image,
that health succeeds, and that served UI assets match this source. Authenticated
layout checks and physical-device testing are separate observations; this record
makes no claim that those live checks have already happened.
