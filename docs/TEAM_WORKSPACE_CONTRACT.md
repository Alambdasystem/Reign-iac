# Shared REIGN workspace implementation contract

Status: candidate implementation; release and live deployment evidence are
recorded separately in `docs/deployments/`.

James requested employee access, a shared workspace with saved team sessions,
group chat/calls and REIGN as a visible speaking participant. Camera and screen
content must reach REIGN only through an explicit sharing action. Legacy owner
chat, saved accounts and browser sessions remain private.

## Scope and ownership

The first workspace has the stable internal ID `rain` and display name
`Workspace`. REIGN, pronounced "Rain", is the AI participant. Display copy must
not change the `rain` record bindings, `team:rain:{room_id}` browser scopes,
encrypted associations or existing saved titles. Core owns durable users,
membership, rooms, message events and document versions in an encrypted SQLite
database on the existing private vault disk. Stable user IDs are distinct from
usernames and individual browser connection IDs. Owner and employee access use
the existing AX cookie and CSRF boundary; the Avatar Lab-only role remains valid.

Every `/api/team` request is checked against current membership in Core. AX sends
a short-lived signed actor assertion in addition to the existing Core service
authorization. Browser-provided actor IDs or role fields never authorize access.
New team routes must not call legacy global conversation/history/account APIs.
Separate resource bindings are required before shared browser execution is enabled.

## HTTP contract

JSON responses use `Cache-Control: no-store`. Invalid or extra identity fields
are rejected. All mutations require the existing AX Origin and CSRF checks.
Core-only login and identity operations require service authorization.

| Endpoint | Request / response |
| --- | --- |
| `GET /api/team` | `{workspace:{id,name,durable}, me:{id,username,display_name,role}, members:[], rooms:[], capabilities:{...}}` |
| `POST /api/team/members` | Owner: `{username,display_name}`; create generated temporary password, return `{member,temporary_password}` once. No email is sent. |
| `PATCH /api/team/members/{user_id}` | Owner: `{active:false}`; revoke workspace membership. Owner cannot revoke itself. |
| `POST /api/team/rooms` | `{title}`; return `{room:{id,title,created_at,updated_at}}`. |
| `GET /api/team/rooms/{room_id}` | `{room,events,documents,presence,last_seq,assistant}` with the most recent bounded event page. |
| `GET /api/team/rooms/{room_id}/events?after={seq}` | Ordered `{events,last_seq,presence,assistant}`; support bounded `wait` up to 20 seconds. Recheck membership before returning after a wait. |
| `POST /api/team/rooms/{room_id}/messages` | `{client_message_id,text,ask_reign:false,speak:false,attachment_ids:[]}`; optional `input_source:"voice"` requires an exact matching, caller- and room-bound `transcription_id`. Omitted source defaults to text. Persist before acknowledgement; idempotency prevents duplicate turns. Return `{event,run_id}`. |
| `POST /api/team/rooms/{room_id}/presence` | `{connection_id,in_call:false,microphone:false,camera:false,screen:false}`; heartbeat with a 30-second lease. Response `{presence}`. |
| `DELETE /api/team/rooms/{room_id}/presence/{connection_id}` | Release this user's connection, media presence and control lease only. |
| `POST /api/team/rooms/{room_id}/documents` | `{title,content,client_document_id}`; create Markdown document. |
| `PUT /api/team/rooms/{room_id}/documents/{document_id}` | `{title,content,expected_version}`; reject stale edits with 409. |
| `GET /api/team/rooms/{room_id}/documents/{document_id}` | `{document:{id,title,content,version,updated_at}}`; enforce room binding. |
| `GET /api/team/rooms/{room_id}/voice` | Room-authorized `{configured,model,transport:"webrtc",max_duration_seconds:3600,...}`. |
| `POST /api/team/rooms/{room_id}/voice/offer` | `{sdp,connection_id,client_session_id}`; return `{sdp,type,model,max_duration_seconds,voice_session_id}`. Both session IDs are the same caller-generated UUID. |
| `DELETE /api/team/rooms/{room_id}/voice/sessions/{session_id}` | No body; idempotently retire this caller's pending or active session, close its provider call and cancel only its unfinished room request. |
| `POST /api/team/rooms/{room_id}/voice/sessions/{session_id}/turns` | `{client_message_id,text}`; delegate a spoken request through the authorized room assistant and return `{event,run_id}`. Server supplies voice origin and session binding. |

Room events have `{seq,id,type,user_id,display_name,created_at,data}`. Types are
`message`, `assistant_started`, `assistant_message`, `assistant_error`, and
`document_saved`. Message data includes `text`, `client_message_id`, and the
verified `input_source` (`text` or `voice`); assistant
messages can include `segments:[{text,expression,audio_id?}]`, `audio_id`, document
IDs and `speech_status` (`ready`, `streaming`, `unavailable`, `not_requested`).
`streaming` means one or more reserved shared clips may still be generating;
every clip is verified before its audio endpoint returns it. For a live voice
session, all clips may be pending when the text event is published so shared
generation cannot delay the initiating caller. This cache status does not
describe microphone input. Live assistant events also carry their server-supplied
`voice_session_id`; the initiating browser matches both that ID and `run_id`
before narrating the answer and suppresses its own cached duplicate.
Expressions are bounded choices (`neutral`, `warm`, `thoughtful`, `concerned`).
The assistant context is reconstructed only from that room's authorized history.
One assistant run is active per room, with a bounded global model-call budget.
Failed/interrupted runs are recorded honestly and never silently replay tools.

The browser bounds its room long-poll wait to five seconds. The server can wake
a wait on a visible presence change, but a leave between successive requests
can occur before the next request's baseline snapshot. The shorter client wait
keeps that case from retaining old microphone/camera flags for a twenty-second
idle poll. This is a bounded refresh mechanism, not strict response ordering.

## Calling and multimodal boundaries

Joining a room does not turn on media. `Join call`, camera, screen share and
`Share with REIGN` are separate explicit controls. Leaving a call releases local
tracks and peer connections without deleting the saved room.

For an initial small-room call, the transport uses bounded peer connections and
authenticated room-specific signaling. It exposes whether a relay is configured;
direct peer connections alone do not establish reliability on every network.
No paid provider, new Render plan or new service is provisioned by this contract.

| Endpoint | Request / response |
| --- | --- |
| `GET /api/team/call-config` | `{ice_servers:[{urls:"stun:stun.cloudflare.com:3478"}],relay_configured:false,max_participants:4}`. The isolated CI fixture disables public STUN. |
| `POST /api/team/rooms/{room_id}/signals` | `{connection_id,to,kind,payload}`; only an active same-room connection owned by the caller can send. `kind` is `offer`, `answer` or `ice`. |
| `GET /api/team/rooms/{room_id}/signals?connection_id={id}&after={seq}` | `{signals:[{seq,from,kind,payload}],last_seq}` for that caller-owned connection only. Signaling is bounded, ephemeral and excluded from room archives. |

Camera/screen snapshots need a room-scoped upload receipt before they can enter
an assistant message. Source, participant, capture time and expiry accompany each
receipt. Sharing a video with humans does not grant image-input permission to
REIGN. User media never starts automatically after restoring a saved session.

`POST /api/team/rooms/{room_id}/attachments` accepts a JPEG/PNG data URL (at most
512 KiB), `source`, the caller's current connection ID, and an ISO capture timestamp.
Sources `camera` and `screen` require the caller's active call and enabled source.
Source `camera_preview` requires the caller's active, same-room viewer connection
with `in_call: false`; it neither creates a call nor changes media-sharing flags.
Both paths recheck membership and the owned connection after decoding. The server decodes
and strips metadata, scales to at most 960×540, and returns a room/user-bound
receipt that expires after five minutes. Only explicitly shared stills enter the
model input. Human video sharing does not automatically enable AI vision.

The direct Camera control requests video only from a user gesture and displays a
local muted preview. Describe captures and shares one bounded JPEG, then submits
an explicit image-description question with speech enabled. The ordinary typed
draft and its attachments are retained. A confirmed send closes the preview and
stops its owned camera; an uncertain send keeps one stable message ID for the
visible Retry action. No repeated frame capture or automatic resubmission runs.
Stop, close, room changes, logout and page exit stop owned tracks, including
tracks that arrive after a late permission result. Description uses actual image
bytes through the existing vision model. The submitter's account identity does
not establish who is pictured, and presence flags do not establish image content.

`POST /api/team/rooms/{room_id}/transcriptions` accepts a bounded recording made
by an explicit Dictate action and returns `{text,transcription_id}`.
The receipt expires after five minutes, is limited to the requesting actor and
room, and authorizes only the exact returned words as a spoken input. It does
not authorize a tool or change room permissions. The room prompt and saved event
retain the voice source, so rendering a transcript does not imply typed input.
Edited drafts use the ordinary text source. An uncertain send retries the same
message ID and receipt; no automatic rerecording or resubmission occurs.

Dictate puts text into the composer for review and remains manually completed,
with its existing recording byte/time bounds. It never automatically submits
the draft, and is unavailable while the live microphone is connected. Cancel,
room change, logout and page exit release its owned tracks and abort
transcription; late permission/results cannot submit a turn. The ordinary
composer sends text origin, including reviewed or edited dictation drafts.

**Microphone on** uses the existing Realtime WebRTC controller with room-specific
negotiation and delegation. It never calls MediaRecorder or the recording
transcription endpoint. Raw microphone audio continues on the same connection
across utterances. Provider server VAD has a 450 ms silence window and supports
interruptions; it is a turn boundary, not a timer that closes the microphone.
The shared adapter disables the private controller's startup greeting, so an
explicit spoken request is required before room work or narration begins.

Before negotiation, the adapter renews the exact current viewer connection,
then uses a new client-generated session UUID. That connection has the existing
ten-second heartbeat and thirty-second presence lease. No human-call membership
or fabricated microphone/camera presence flag is required. Core validates the
member, room, credential revision, live viewer and session on each new spoken
request. The browser cannot supply live session IDs to the ordinary messages
endpoint. Voice-origin metadata records the live model's delegated request;
it is not independent verification of raw audio, an exact transcription receipt,
or permission to use tools outside the room. Provider call IDs and credentials
remain server-side. At most four live calls, one per member, are admitted.

**Mute mic** disables the owned audio track while retaining the connection;
**Microphone off** immediately stops the track, peer connection and local output,
then DELETEs the pending or active lease. The client-known session ID lets Off
retire an offer before its provider answer arrives. A late answer repeats only
idempotent cleanup. A restart waits for previous cleanup, and another Off cancels
that waiting restart. Room changes, logout, page exit, membership revocation,
viewer expiry and server shutdown also close the associated call. Expired calls
return `team_voice_closed` with HTTP 410 and require an explicit reconnect.
If Off precedes a provider's answer, the old call keeps its capacity reservation
until cleanup finishes. An attempted restart during that interval returns
`team_voice_closing` with HTTP 409, leaves the microphone off and asks the user
to try **Microphone on** again shortly; no timed reconnect or request replay runs.
The maximum one-hour session lifetime does not limit individual utterances.
Media never reopens automatically on refresh, room restore or connection loss.

Each delegated request receives one stable `client_message_id`. If submission
is uncertain, the browser exposes an explicit retry of that exact ID and body;
it does not replay tools automatically. Durable duplicate lookup precedes the
ephemeral lease check so a request accepted before Off can still be confirmed.
Ending one voice session cancels only that session's unfinished request and
does not cancel another member's work or a separate typed turn. The portrait's
native-output ownership ends with the call. Its retired IDs remain in a bounded
client set so a delayed cached answer cannot play a second time after Off.

Live voice captures only the caller's microphone. The human group call has
separate media ownership; live voice does not stop peer tracks, join a call,
enable a camera or capture other participants. Camera/screen content and raw
recordings are not written to the durable conversation archive.

Public STUN helps discover direct network routes. No TURN relay or SFU is
provisioned. Four human call connections are supported; restricted networks can
still prevent calls. See [Cloudflare's public STUN documentation](https://developers.cloudflare.com/realtime/turn/faq/).

REIGN audio has a single authoritative room response. Each human plays that
response with expressions synchronized to their local audio output. Different
devices are not assumed to have identical network or output latency. Audio
playback must not wait on slow
portrait inference. A fixed precomputed neural portrait can keep model weights
out of the team page; this must be labeled separately from live neural rendering
and from phoneme-accurate lip sync.

The candidate team portrait is a 48-frame, 256-pixel neural atlas generated
offline from the bundled fictional sample using the same checksum-pinned model
as Avatar Lab. The checked-in manifest records model/source/atlas hashes. The
compressed atlas is about 320 KB; its decoded RGBA image is about 12 MiB. Neither
ONNX weights nor an inference worker are loaded by the team page. Inference is
performed when building the atlas, not by the Render CPU at runtime.

Core calls the existing configured OpenAI Responses model for authorized room
text, deliberately shared image bytes, structured sentence expressions and
requested Markdown documents. For the initiating live caller, the exact
authorized answer and its model expressions are passed back to the existing
Realtime connection for narration. The browser accepts expression plans only
when their normalized text exactly covers that answer and both run and session
IDs match the outstanding request. Native output level and media time drive
the same prepared portrait; microphone energy never drives REIGN's mouth.
Incoming user speech stops the current narration before a later reply can
take its place. No synthetic greeting or direct unrequested model answer is
permitted. Streaming narration is audible before its final transcript can be
checked; a mismatch stops remaining output and is reported, rather than
represented as fully verified audio heard before validation.

For other viewers and ordinary typed replies, the same configured Realtime
provider generates sentence clips with no tools. These clips are checked
against the requested sentence's transcript before delivery and stored only
in a bounded ten-minute audio cache. Live-session clip production starts in
the background after reserving IDs and does not block delivery of the room
answer to its initiating caller.
The server reserves worst-case cache space before speech generation, limits
concurrent speech producers, and exposes the first clip only after its complete
transcript matches the sentence. Later clips are generated on the same bounded
provider connection. A protected room audio request can wait up to 42 seconds
for its reserved clip and rechecks membership before returning the bytes. A
failed pending clip cannot appear as successful empty audio. Failure before the
first clip preserves text/documents with `unavailable`; failure later preserves
earlier verified clips and makes the remaining audio request fail honestly.

The browser prefetches and decodes at most one future clip while the current
clip plays. A pending clip is shown as buffering, and its expression never
advances until actual playback starts. Later-clip failure resets the face and
offers an explicit retry of only unheard segments; it does not rerun room tools
or automatically replay prior sentences. Cancellation aborts pending audio
requests and fences late decoding. This shared clip fallback remains bounded
sentence playback; continuous input and native streaming output use the
separate live WebRTC connection. Neither path predicts phonemes. For clips,
the browser samples the decoded PCM envelope
at its audio output position and advances sentence expressions on the same
clock. Where available, Web Audio output timestamps account for device latency;
the fallback is bounded by the context's reported time and latency. A suspended
context freezes the cues and closes the mouth. No timer may silently advance to
the next sentence while output is stopped. Room changes and interruptions stop
and disconnect the current source immediately. Portrait loading never delays
speech or starts neural inference.

Typed Ask REIGN defaults to requesting speech when that capability is available,
and calls audio resume directly from the submit gesture before network work.
Checking Voice reply is another explicit audio-enabling gesture. An explicit
unchecked choice survives room switching; archived replies never autoplay.
Playback failures retain the written answer and expose the audio retry control.

## Shared browser and private owner isolation

Each room has its own encrypted durable browser binding. Computer sessions have
an immutable server-derived scope (`owner` or `team:rain:<room UUID>`). Legacy
owner discovery, frame, control, action, credential and close routes cannot attach
to a team session, even when its UUID is known. Team requests never discover the
owner's browser or saved accounts. A room can reuse the pilot's single browser
slot only while it is available; no additional Computer is provisioned.

The room browser panel opens only after a user or authorized room tool requests
it. Taking control creates a lease bound to the viewer's live room connection,
user and ownership epoch. A different participant cannot inject input or return
that lease. Human takeover cancels the room's running assistant; returning
control does not replay it. Membership and ownership are rechecked on actions.
Browser metadata survives restart; an expired Chromium process is reported as
expired and is never represented as a recovered live browser.

The existing exact HTTPS host allowlist remains authoritative. This pilot's
configured destination is Git High. No remote employee desktop, Tailscale device
or Windows/GPU worker is enrolled by this change. Those need an actual target and
supported connection configuration before they can be enabled.

## Rollout and operations

The source change does not activate the feature by itself. The reviewed digest
promotion enables `REIGN_TEAM_MODE=disk` on Core and `AX_TEAM_ENABLED=1` on AX.
The existing private Core vault disk and a domain-separated derivative of its
existing encryption key protect team records; there is no new disk, secret or
compute plan. Invalid disk/key configuration fails startup closed.

The owner adds employees through the shared workspace and receives a generated
password once. No invitation or email is sent automatically. Existing avatar-only
usernames are reserved, so creating an employee cannot silently change their
role or replace their credential. Employees land in `/team`; legacy owner APIs
remain inaccessible. Every team API rechecks current membership, and disabling a
member invalidates its next request and stops active room work/media signaling.

Saved sessions consist of room discussions, assistant replies, document versions
and browser bindings. Live media, camera stills, audio clips and browser process
state have separate lifetimes. Current limits bound disk usage, message/model
budgets, concurrent model calls, presence, signaling, media decoding and caches.

Core's promotion includes the existing live voice/graph probe plus a separate
synthetic team-provider probe. The latter must demonstrate actual image input,
a structured expression, a document draft and non-silent matching narration.
It makes no archive writes and does not substitute for a human device call.

## Acceptance

Required checks cover actor spoofing, expired assertion, disabled membership,
cross-room/workspace document and event denial, idempotency, restart persistence,
stale document edits, bounded presence/signaling, media cleanup and member logout
without owner cleanup. Saved discussion/document continuity is separate from
browser process and live media continuity. Real two-participant media testing
must be reported separately from simulated transport tests.
