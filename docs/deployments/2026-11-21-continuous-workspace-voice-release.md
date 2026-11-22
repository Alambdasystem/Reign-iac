# Continuous Workspace voice: release evidence and image selection

This receipt selects tested Core and AX images for continuous microphone
conversation with REIGN in Workspace. It targets the existing Render Blueprint
`exs-dbofqkm417fc7384kou0` through `deploy/render-pilot.proposed.yaml`, with
automatic Blueprint syncing off. This is a predeployment record. Publication,
digest selection and live rollout remain separate evidence checkpoints.

## Source and required checks

- Implementation: [PR #115](https://github.com/Alambdasystem/Reign-iac/pull/115).
- Reviewed PR head: `554d99df24e1ff55effa5bcaf931c30a518ef584`.
- Merged release source: `1a2b163eb4634db7877dd8a6e32728582c7b3d59`.
- Source tree: `66cdeafbbfd4e552611322827ece37baf94709c7`.
- [PR validation 44984142370](https://github.com/Alambdasystem/Reign-iac/actions/runs/44984142370): all four required jobs passed.
- [Main validation 44984740314](https://github.com/Alambdasystem/Reign-iac/actions/runs/44984740314): all four required jobs passed for the merged source.
- [Gated release 44985236471](https://github.com/Alambdasystem/Reign-iac/actions/runs/44985236471), job `137094257281`: exact-source verification, image builds, offline daemon smoke, full acceptance, publication and artifact upload passed.

The source includes continuous room voice, its lifecycle regressions, bounded
shutdown and the merged Tailscale runtime-storage repair. These source checks do
not establish Render activation, tailnet enrollment or physical-device behavior.

## Verified PR and release artifacts

The downloaded PR-validation artifact is
[14739710545](https://github.com/Alambdasystem/Reign-iac/actions/runs/44984142370/artifacts/14739710545),
7,384,507 bytes. Its ZIP SHA-256 is:

`a04be850d48eb9afe73a00a16904d1ff618695427a1e8dc65e9bb81f9a0871b5`

This artifact supports the validation and browser observations below. It is not
the gated release manifest or evidence of published image digests.

The verified CD artifact is
[14738669773](https://github.com/Alambdasystem/Reign-iac/actions/runs/44985236471/artifacts/14738669773),
named `tested-release-1a2b163eb4634db7877dd8a6e32728582c7b3d59`.
Its ZIP is 7,389,673 bytes; its downloaded SHA-256 matches GitHub metadata:

`da0a8aec2fa10b2271e2a12451d503cd99d8f1af35a3c49c12a68af15d522cac`

Release provenance, full acceptance and both controlled-workspace phases identify
the exact merged source and pass. Saved-account persistence after restart passed.
The original four-image manifest remains in the artifact with `provision: false`.
The CD job separately passed the offline nonroot daemon/storage restart check.
No failure receipts were present. These checks support the immutable selections
below; the legacy OpenTofu manifest is not applied.

## Behavior and browser acceptance

**Microphone on** establishes a persistent WebRTC audio connection to the model.
Pauses trigger turns without closing the microphone. **Mute mic** disables its
track; **Unmute mic** resumes it; **Microphone off** releases local media and closes the
owned session. Dictate remains a separate reviewed recording workflow. The live
path does not use a recorder, utterance upload or twenty-second cutoff.

Each connection belongs to its authenticated member, room and viewer lease.
Spoken requests enter the room-authorized assistant; accepted message IDs retain
their retry semantics without replaying work. The assistant supplies the verified
answer and expression segments. The initiating caller hears native WebRTC
narration, while its own cached room reply is suppressed to prevent duplicate
audio. Other viewers can receive the existing verified sentence clips.

The gated-release continuous-voice browser fixture measured **21,504 ms** of capture,
**three turns, one offer, one microphone acquisition and zero recorders**. It used
real loopback peer connections, SDP, SCTP and RTP with generated browser audio.
Receiver bytes, packets and audio energy showed that audio reached the provider
fixture before the scripted utterance ended and continued across turns.

The fixture also verified native playback with an active stream and advancing
audio time, model expression changes, interruption clearing audio and mouth
movement, mute/unmute without reconnecting, and microphone release on Off, room
change and logout before server acknowledgment. No recorded-transcription
upload or duplicate cached reply playback was requested. All seventeen initial
shared-workspace cases and five restart cases passed, including real group-call
transport and leave cleanup. There were no page errors. Private speech provenance
and nonblocking expression annotation also passed; the synthetic shared-PCM
fixture measured a 61.61 ms scheduled sentence gap.

Provider VAD/model events and room API answers were scripted. These measurements
prove the tested browser transport and lifecycle behavior; they do not measure
physical microphone/speaker performance, live-model recognition, real-world
response latency or a deployed user conversation. Mouth movement follows output
energy, and expressions follow the same playback clock. This adds no phoneme or
viseme timestamp model.

## Process shutdown is separate from speech duration

Core's image gives Uvicorn two seconds to drain requests; tests verify the pinned
CLI honors that environment setting in normal and diagnostic startup. TeamVoice
then shares an 18-second cleanup deadline across calls. Known hangups run beside
room cancellation; confirmed closes are not repeated, and failed settlement
remains retryable. Validated provider IDs reach their internal owner before the
SDP body completes. The optional supervisor grants children 25 seconds, within
Render's documented 30-second window for this disk-backed service.

Failed hangups and unknown allocations remain unconfirmed. These are bounded,
best-effort process cleanup rules, never utterance limits. Shared calls retain
the provider's one-hour session maximum. See [VOICE.md](../VOICE.md) for behavior
and source references.

## Image selection and compatibility

| Service | Selection | Immutable digest |
| --- | --- | --- |
| Core | New gated release | `sha256:e05987e4e92adc68548ecc3c0f641feb58e8a642ac428fb2ac7cb850c04ad31a` |
| AX | New gated release | `sha256:6ed0d8192bdb4f275a1f3bae9684412fd2d2b0806c3b927af2bd669ca4a7987d` |
| Computer | Retain current Blueprint pin | `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` |
| Temporal | Retain current Blueprint pin | `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4` |

Repositories are `ghcr.io/alambdasystem/reign-{core,ax,computer,temporal}`.
The fifteen runtime/build/transport paths listed in the
[presence release](2026-11-21-workspace-reign-presence-release.md) are identical
between worker source `471ac3235b0cce0026bf0f45d05c192360dfab19` and this source.
CD assembles all four newly built images; the proposed selection retains older
worker pins. Do not describe that mixed selection as one tested CD assembly.

## Promotion and live verification gate

The promotion preserves [PR #117](https://github.com/Alambdasystem/Reign-iac/pull/117),
merged as `3b9e94a1da0f578232ea5141976a781cfb05a004`: Tailscale stays enabled and
Core uses its normal image command. Only the Blueprint and Tailscale documentation
changed between the tested source and that merge; application, image-build,
script and workflow paths are identical. The selected Core contains both the
storage repair and the continuous-voice shutdown changes.

The artifact checks above are complete. Preserve the latest
approved Tailscale configuration, commands and predeploy gate; do not restore an
older flag or diagnostic override over a concurrent rollout. If main gains
runtime changes after this source, obtain a combined validated release before
promotion. Preserve plans, disks, credentials and service count.

The promotion PR must pass required checks. Its Blueprint plan must identify the
approved merge and intended image substitutions. After applying it, separately
verify live Render digests, provider/predeploy receipts, authenticated Workspace
access and deployed microphone behavior. Record sanitized live evidence without
credentials or private room content. This document makes no live deployment claim.
