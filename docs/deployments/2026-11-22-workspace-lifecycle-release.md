# Workspace layout lifecycle: tested frontend update

This selects the tested AX build that makes the call-space layout an owned
feature of the existing Workspace application. The design and its CSS are
unchanged from the preceding UI rollout. The existing Render Blueprint
`exs-dbofqkm417fc7384kou0` remains the deployment owner with auto-sync off.

## Source and checks

- Implementation: [PR #121](https://github.com/Alambdasystem/Reign-iac/pull/121).
- Reviewed head: `0b7e882cea539f7f916d2be3a71f5d945ad4cf9b`.
- Merged source: `1e628b14354a187d6eb7d2fc4adbee9b5a794d7c`.
- Source tree: `fa8f123fc0603593f14f1b708cfa314df117cd71`.
- [PR validation 45001575836](https://github.com/Alambdasystem/Reign-iac/actions/runs/45001575836) and
  [main validation 45002166199](https://github.com/Alambdasystem/Reign-iac/actions/runs/45002166199):
  all four required jobs passed.
- [Gated release 45002740630](https://github.com/Alambdasystem/Reign-iac/actions/runs/45002740630),
  job `137147607276`: exact-source guard, builds, offline daemon check,
  browser acceptance, publication and artifact upload passed.

Artifact [14747025253](https://github.com/Alambdasystem/Reign-iac/actions/runs/45002740630/artifacts/14747025253)
is named `tested-release-1e628b14354a187d6eb7d2fc4adbee9b5a794d7c`.
The downloaded ZIP is **6,893,869 bytes** and its SHA-256 matches GitHub metadata:

`3988c8c96703cf76432adc481e300c9858b03c4916e7910faf2122fcbbc54452`

The manifest retains `provision: false`. Provenance and full acceptance bind to
the merged source. Both controlled-workspace phases passed, including saved-account
persistence after restart. There are no failure receipts.

## Native Workspace integration

`team.js` remains the single HTML module entry point. Its existing
`createTeamWorkspace` factory now constructs the layout after controllers and
listeners are initialized, passes the same document/window, and disposes it once
through the existing guarded teardown. The layout module no longer starts itself.

The application continues to own its actual form, message feed, portrait canvas,
audio/video nodes, permission-gated buttons, room state and media controllers.
The correction adds no replacement API, authentication or media transport.
The unchanged call-space CSS has SHA-256
`045f8fd1bcfd6782c059f9b24aa9483f0140bdb201b6e03e4618675d3246f954`.

All **115 Workspace Node tests** passed before publication. Focused regressions
cover single construction, shared document/window, room and boot continuity,
cleanup order and idempotency, page exit, cross-tab sign-out, failed logout,
access-loss recovery, and importing the layout without an independent DOM boot.

The release browser evidence passed **19 initial and five restart cases** with
zero page errors. Chat/Together/Face changes preserve media, streams, active
tracks, drafts, transcript position and the portrait. Long-draft reflow passed
at 360/390/430/1280 CSS-pixel widths. Continuous voice measured **21,512 ms**,
three turns, one offer, one microphone acquisition and zero recorders, receiving
86,561 bytes in 1,080 packets. These checks use real browser/loopback transports
with synthetic devices and scripted providers; physical devices and live-model
responses are separate observations.

## AX-only selection

| Service | Selection | Immutable digest |
| --- | --- | --- |
| AX | New tested release | `sha256:1cccbcc4036f2a78bd122e7452799f87317c2c0513623435d31ffdea3070b068` |
| Core | Retain existing pin | `sha256:e05987e4e92adc68548ecc3c0f641feb58e8a642ac428fb2ac7cb850c04ad31a` |
| Computer | Retain existing pin | `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` |
| Temporal | Retain existing pin | `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` |

Repositories are `ghcr.io/alambdasystem/reign-{ax,core,computer,temporal}`.
The previous AX digest is `sha256:1759dc0578467f4e0988083756ee5d3ca77552703d4843b2fc1370ac06e37c3e`.
Core, Computer, Temporal and shared `reign` runtime trees are unchanged from the
currently selected Core source `1a2b163eb4634db7877dd8a6e32728582c7b3d59`.
CD tested a fresh four-image assembly; this AX-only selection is not represented
as that identical four-image assembly.

## Deployment evidence and gate

The preceding UI rollout was confirmed live as Render deployment
`dep-dc1a2f1ksfps738ak470`. At 2026-11-22 08:02:24 UTC, public health returned
HTTP 200 with `status: ok`, and the served call-space JS/CSS matched that release's
source bytes. That baseline observation does not establish activation of this
new lifecycle correction.

This promotion changes exactly one AX image URL and adds this receipt.
Preserve all other fresh-main configuration, commands, predeploy gates, plans,
disks, credentials and resources. Required promotion checks must pass, followed
by review of the existing Blueprint plan for one AX image update. After applying,
verify the new deployment is live, health succeeds, and all three served
`team.js`, `team-callspace.js` and `team-callspace.css` files match this source.
Authenticated live layout checks remain a separate verification step.
