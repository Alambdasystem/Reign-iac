# Rain team worker release — stage 1

## Selected tested image

Promote only the Computer image from source
`471ac3235b0cce0026bf0f45d05c192360dfab19`:

`ghcr.io/alambdasystem/reign-computer@sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1`

[PR #101](https://github.com/Alambdasystem/Reign-iac/pull/101) added saved team
rooms, employee access, calls, speaking portrait playback, explicit image sharing,
documents and a separately scoped managed browser. Its final
[source validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44885145788)
and [main validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44885698191)
passed all four required checks. The exact images were built, tested and published
by [gated release 44886154688](https://github.com/Alambdasystem/Reign-iac/actions/runs/44886154688).

The downloaded artifact `tested-release-471ac3235b0cce0026bf0f45d05c192360dfab19`,
ID `14700238213`, is 5,789,821 bytes. Its ZIP matched GitHub's SHA-256:

`a7a04c9d34199a9a3117b134e880381fd8ea8ed0ff43b9df7fcd7336e3448fa1`

Release provenance and acceptance identify that exact source. All twelve initial
team cases and four restart cases passed without JavaScript page errors. These
include real WebRTC transport between two isolated Chromium contexts using
synthetic devices, media cleanup and both viewers' indicator cleanup, durable
room/document recovery, employee revocation, and actual managed-browser frames,
scope isolation, takeover/handback and stale-input denial. Physical devices and
live provider speech were not exercised by this container gate.

## Scope and compatibility

Reuse Blueprint `exs-dbofqkm417fc7384kou0`, with auto-sync disabled. This stage
changes only Computer's image. Core, AX, Temporal, team feature flags, credentials,
plans, instances, disks and unrelated resources retain their reviewed settings.

The new worker preserves legacy owner workflows and HTTP routes. Dedicated team
workflow/activity types, room-scoped HTTP paths, and protocol acknowledgement 1
prevent new Core requests from falling back to an older unscoped implementation.
The worker must be live, and the original instance's Temporal pollers must have
stopped, before activating the team Core/AX release. A live HTTP cutover alone is
not proof that the old outbound worker has stopped.

The separate presence-context follow-up changes team store/API/assistant context,
tests and documentation. An independent comparison confirmed that Computer's
runtime import closure, Dockerfile, requirements, workflows, browser protocol,
Core transport and predeploy protocol check are byte-identical. Computer can
therefore retain this tested image while Core/AX select a later tested presence
release. Record each service's source, release run and digest in that activation
receipt; do not claim the mixed selection came from one release artifact.

The later Core predeploy wrapper must pass the existing voice probe, the read-only
live Computer protocol probes, and the synthetic room image/document/expression/
audio probe. It disables disk-backed modes only for the mountless predeploy
process. Live scoped-browser verification remains a separate check.

This receipt records the reviewed selection and stage order. Render deployment
history and shutdown logs establish the subsequent live rollout and worker drain.
