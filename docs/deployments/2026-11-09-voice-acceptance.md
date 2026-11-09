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

## Final deployed browser acceptance: blocked

The browser session expired before the final UI test against the corrected
Core. A secure sign-in submission reached the site, which visibly returned
“That password wasn’t accepted. Try again.” No authentication value is included
in this receipt. Final deployed WebRTC connection, microphone capture, and
speaker playback have not been verified. James's phone hardware was not tested.

Next action: complete secure user sign-in, then exercise Start voice, confirm
the spoken greeting and a request through the shared LangGraph conversation,
test Mute and End call, and record actual playback and resource cleanup. Test
James's phone microphone/speaker separately. The passed WebSocket provider
probe and simulated UI checks do not establish either of those outcomes.
