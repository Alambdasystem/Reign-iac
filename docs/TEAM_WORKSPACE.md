# Rain shared workspace

Rain adds one shared team workspace at `/team`. The existing owner sign-in and
private workspace remain available. Employee accounts land in Rain and cannot
use the owner's private account vault, history, agent, or browser endpoints.
An existing Avatar Lab-only login remains restricted to that role.

## Using Rain

The owner can create an employee login from **Employee access**. Its generated
password is shown once so the owner can give it directly to the intended person.
The application does not send invitations. Revoking a login invalidates its
membership, presence, signaling, media access, and shared-browser control.

Choose a saved room or create one. Room messages and Markdown documents are
shared with current workspace members. **Ask REIGN** requests one assistant turn
in the room; **Talk to REIGN** records up to twenty seconds from the caller's own
microphone, then transcribes and submits that turn when the caller stops.
**Dictate** only puts the transcript in the composer for review. Cancelling,
changing rooms, or signing out discards an unfinished recording.

**Join call** joins a small peer-to-peer call. Microphone, camera and screen
sharing are separate controls. Participants can see shared video; sending an
image to REIGN requires a separate local preview and confirmation. Only that
caller's selected camera or screen still is uploaded for a model turn. There is
no continuous room recording or automatic capture of another participant's
video. The first call or Listen gesture enables browser audio playback.

REIGN replies with text, short spoken segments, and a face in the room. Each
segment has a neutral, warm, thoughtful, or concerned expression. The portrait
player blends pre-rendered neural frames and drives mouth opening from the
spoken audio. It does not perform phoneme alignment or live neural inference.
Audio generation, transcription, image understanding, and assistant work run
through Core; the team page does not download an ONNX model. The compressed
portrait atlas is approximately 320 KB and decodes to approximately 12 MiB.
The separate Avatar Lab remains a device-local neural experiment.

Documents use explicit saves and version checks. If somebody else has saved a
newer version, the editor preserves the local draft and offers recovery instead
of silently overwriting it. Documents can be downloaded as Markdown.

The shared browser is a room-owned managed browser, currently restricted to the
existing Git High host allowlist. Members can view it and explicitly take or
return control. The browser lease belongs to one live room connection and has a
versioned control epoch; stale actions are rejected. REIGN can use the same
room-scoped browser tools during an assistant turn. Browser credentials, the
owner's private browser, arbitrary remote desktops, and device enrollment are
outside this feature.

## Storage and limits

Core stores encrypted SQLite room records on the existing private vault disk.
The team encryption key is derived with a distinct purpose from the existing
vault key. No shared database permission or additional disk is required.
Rooms, text events, document versions, and employee verifiers survive a process
restart. Presence, WebRTC signals, uploaded images, and generated audio are
ephemeral; call recordings and browser profiles are not archived.

The pilot permits 32 member records, 100 rooms, 20,000 events and 100 documents
per room, 100 versions per document, and a 96 MiB team storage budget. Calls permit four
human connections per room. They use a public STUN service and have no TURN
relay configured, so some restrictive networks cannot establish a call. There
are no guarantees of four physical-device calls from local or CI acceptance.

Assistant work is limited to one active turn per room and two workspace turns
at once, with bounded daily and per-minute request budgets. Media uploads,
image decoding, recordings, audio responses, browser operations, and caches all
have independent size, lifetime, or concurrency limits. Provider failure leaves
text/documents available when possible and reports speech as unavailable.

## Deployment

Enable Core with `REIGN_TEAM_MODE=disk` and AX with `AX_TEAM_ENABLED=1` only after
the exact candidate images pass the repository's four CI checks and gated
release. Reuse the existing Blueprint with auto-sync off and immutable images.
Do not add services, increase plans, or enable remote adapters for this rollout.

Promote Computer first. Dedicated team workflows, activities, HTTP paths and a
protocol acknowledgement prevent new Core from treating an old Computer as a
scoped team worker. Wait for the new Computer deployment and old worker shutdown
before enabling Core/AX. Then promote the tested Core and AX images with the
two feature flags. Preserve Temporal and all unrelated resources.

Core's mountless pre-deploy command must disable disk-backed features while
running the release gates:

```sh
env REIGN_HISTORY_MODE=disabled GENESIS_DRAFT_MODE=disabled REIGN_NODES_MODE=disabled REIGN_TEAM_MODE=disabled python -m reign.release_acceptance
```

The wrapper first runs the existing private voice/graph acceptance, then the
read-only Computer isolation-protocol probes, followed by a synthetic team
image/expression/document/spoken-audio probe. The protocol gate checks a
nonexistent browser ID with matching and incorrect scopes without creating or
changing a session. Failure of any gate blocks the new Core deployment.
Production startup still requires the existing private disk; disabling disk
features applies only to pre-deploy verification.

CI exercises two isolated browser participants with synthetic microphone/camera
sources, actual WebRTC media, saved-room restart behavior, employee revocation,
and actual managed-browser transport. Real provider pre-deploy checks and live
authenticated Render checks are separate evidence. Physical-device call quality
and the owner's observed human takeover remain human acceptance items.
