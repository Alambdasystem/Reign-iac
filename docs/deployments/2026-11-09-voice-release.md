# REIGN live voice release provenance — 2026-11-09

Implementation: [PR #10](https://github.com/Alambdasystem/Reign-iac/pull/10),
merged source `acc2fa84969083aca544cd0f60371cab2b13425f`.

- [Required validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/42542304805): all four checks passed.
- [Tested image release](https://github.com/Alambdasystem/Reign-iac/actions/runs/42542568808): passed.
- [Release artifact](https://github.com/Alambdasystem/Reign-iac/actions/runs/42542568808/artifacts/13642311265): immutable images, provenance, and initial/restart browser evidence.
- Source checks: 77 Python tests, 35 voice controller tests, and six disposable PostgreSQL integration tests passed.
- Real four-service acceptance passed before and after restart. The separately labeled voice UI simulation passed seven integration checks, including shared graph ownership, cancellation, microphone denial, and expired sign-in recovery.

## Blueprint promotion

The existing `REIGN pilot` Blueprint remains the only infrastructure owner,
with `autoSync=false`, four existing resources, and unchanged instance plans.
Only Core and AX image pins change. Computer and Temporal retain their previous
live image pins. Workmates, Git High, Moby Alpha, and the shared database are
outside this promotion.

| Service | Image selected for promotion |
| --- | --- |
| Core | `ghcr.io/alambdasystem/reign-core@sha256:9a771a6bf2c415f70d715dc385baa6fd5fdcd2db9442e24e9dd2ea12302cdab6` |
| AX | `ghcr.io/alambdasystem/reign-ax@sha256:d3fa8117b740a26f705b88bbfb55d8c26ec39d73533ee20188f475e1157b4241` |

Core gains `preDeployCommand: python -m reign.voice_acceptance`. The command
uses its existing server-only provider configuration, synthetic speech, and
real read-only LangGraph workspace status. A nonzero exit blocks the Core
deployment. Its sanitized `REIGN_VOICE_ACCEPTANCE` log receipt identifies the
actual provider, graph execution, and returned PCM duration/energy.

## Evidence boundaries

CI does not have a live provider credential. Its voice media/provider/graph
events are simulated and explicitly labeled. The pre-deploy probe uses real
provider speech input and output over WebSocket with manual turn commitment;
it does not prove browser WebRTC, automatic voice activity detection, or
physical microphone/speaker behavior. Deployment health and the live provider
receipt must be checked after Blueprint application, followed by live
UltraView controls. Human device acceptance remains separate.
