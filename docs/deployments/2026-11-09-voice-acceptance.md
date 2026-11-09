# REIGN voice deployment and acceptance — 2026-11-09

## Deployment: passed

The corrected Core and AX images are live at
[REIGN](https://reign-ax-pilot-d934.onrender.com/). This receipt supersedes the
promotion-pending status in [release provenance](2026-11-09-voice-release.md).

- Implementation: [PR #10](https://github.com/Alambdasystem/Reign-iac/pull/10).
- Correction: [PR #12](https://github.com/Alambdasystem/Reign-iac/pull/12), application source `322089a80640441808e439c7e5a6d6cf4adb9311`.
- Promotion: [PR #13](https://github.com/Alambdasystem/Reign-iac/pull/13), main merge `6ab1e83413473ac65b22c9b4d45cda3d69ea4177`.
- [Required validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/42544283495), [tested-image release](https://github.com/Alambdasystem/Reign-iac/actions/runs/42544521523), and [verified artifact](https://github.com/Alambdasystem/Reign-iac/actions/runs/42544521523/artifacts/13643390090): passed.
- Checks: 78 Python tests, 37 Node voice tests, six PostgreSQL integration tests, and seven simulated voice UI checks passed. Simulated voice checks are not live provider or hardware evidence.

The existing Blueprint `exs-dbofqkm417fc7384kou0`, sync
`exe-dboi19dg1s2s73f8s8qg`, applied exactly two image updates with
`autoSync=false`. Computer/Temporal image pins, instance plans, Workmates, and
the shared database were unchanged.

| Service | Deploy | Live time (UTC, November 9) |
| --- | --- | --- |
| Core | `dep-dboi1ebrjlhs73bh6nog` | 01:25:01Z |
| AX | `dep-dboi1ebrjlhs73bh6nng` | 01:24:23Z |

| Service | Live immutable image |
| --- | --- |
| Core | `ghcr.io/alambdasystem/reign-core@sha256:41ad398a769d7227f3cf4179fa61df1cfba50196cf8bfb0a6095191dce1259b1` |
| AX | `ghcr.io/alambdasystem/reign-ax@sha256:7340c6defcf91123ddee15b5c232d8d6527901580a65f29ebbf00564e300877b` |

## Real provider and graph acceptance: passed

Core's pre-deploy probe passed all nine stages from 01:24:24Z to 01:24:36Z.
Evidence: [sanitized JSON receipt](2026-11-09-voice-acceptance.json).

| Measurement | Observed result |
| --- | --- |
| Transport and input | WebSocket; synthetic PCM; manual turn commitment |
| Input audio | 5.435 seconds at 24 kHz |
| Realtime model / voice | `gpt-realtime-2.1` / `marin` |
| Delegation | Actual LangGraph `gpt-6-astra` execution of `workspace_status` |
| Graph token usage | 1,089 input / 30 output tokens |
| Returned audio | 268,800 PCM bytes; 5.6 seconds; non-silent |
| Output RMS | 0.070712 |
| Input and output transcripts | Both present; contents are not recorded here |
| Browser WebRTC / physical device | `not_run` by this probe |

The first provider attempt failed with `audio_before_graph_result`, blocking
that Core deployment while the previous Core remained live. The correction
keeps delegation output text-only while accepting microphone audio input, then
explicitly requests audio narration after the verified graph result. The gate
still rejects premature audio; its acceptance condition was not relaxed.

## Final deployed browser acceptance: input device unavailable

Secure reauthentication succeeded, resolving the earlier sign-in blocker. The
signed-in workspace displayed Ready and enabled Start voice. Clicking it first
showed Connecting / “Checking your voice connection…”, then Voice needs
attention / “No microphone was found. Connect or enable a microphone and try
again.” Start voice returned; Mute and End call were removed.

The controller awaits `getUserMedia` before creating the peer connection,
gathering SDP, or posting `/api/voice/offer`. Its missing-microphone error therefore
establishes that this attempt stopped before offer negotiation. No Realtime
session-created, WebRTC connection, or audio playback was observed.

| Browser check | Observed result |
| --- | --- |
| Secure sign-in and corrected live workspace | Passed |
| Start voice | Attempted; blocked by missing microphone in the cloud browser |
| Failure recovery | Start voice restored; Mute and End call removed |
| SDP offer negotiation | Not started |
| WebRTC connection, incoming audio, speaker playback | Not run |
| Mute, interruption, End call during an active call | Not run |
| Physical microphone/speaker audibility | Not run |

Afterward, a typed request asked whether Core, Temporal, and Computer were ready.
The actual live UI logged “Using workspace status”, then “workspace status
completed”, and reported all three components Ready. This authenticated
LangGraph/tool run used 1,087 input / 38 output tokens. It establishes the live
chat and tool path; it does not establish browser voice delivery.

Evidence: [reviewed browser screenshot](2026-11-09-voice-browser.jpg), captured
at **2026-11-09T02:23:05.838Z**. The image contains the generic app/status
interaction and no authentication values.

Next action: use a browser with an available microphone, exercise Start voice,
confirm the spoken greeting and a spoken request through the shared LangGraph
conversation, then test Mute, interruption, End call, and resource cleanup.
James's phone microphone/speaker remain untested. The passed WebSocket provider
probe and simulated UI checks do not establish physical audibility or the
remaining browser WebRTC/media checks.
