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
| `POST /api/team/rooms/{room_id}/messages` | `{client_message_id,text,ask_reign:false,speak:false,attachment_ids:[]}`; persist before acknowledgement. Idempotency prevents duplicate turns. Return `{event,run_id}`. |
| `POST /api/team/rooms/{room_id}/presence` | `{connection_id,in_call:false,microphone:false,camera:false,screen:false}`; heartbeat with a 30-second lease. Response `{presence}`. |
| `DELETE /api/team/rooms/{room_id}/presence/{connection_id}` | Release this user's connection, media presence and control lease only. |
| `POST /api/team/rooms/{room_id}/documents` | `{title,content,client_document_id}`; create Markdown document. |
| `PUT /api/team/rooms/{room_id}/documents/{document_id}` | `{title,content,expected_version}`; reject stale edits with 409. |
| `GET /api/team/rooms/{room_id}/documents/{document_id}` | `{document:{id,title,content,version,updated_at}}`; enforce room binding. |

Room events have `{seq,id,type,user_id,display_name,created_at,data}`. Types are
`message`, `assistant_started`, `assistant_message`, `assistant_error`, and
`document_saved`. Message data includes `text` and `client_message_id`; assistant
messages can include `segments:[{text,expression,audio_id?}]`, `audio_id`, document
IDs and `speech_status` (`ready`, `unavailable`, `not_requested`).
Expressions are bounded choices (`neutral`, `warm`, `thoughtful`, `concerned`).
The assistant context is reconstructed only from that room's authorized history.
One assistant run is active per room, with a bounded global model-call budget.
Failed/interrupted runs are recorded honestly and never silently replay tools.

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
by an explicit Dictate or Talk to REIGN action. Dictate puts text into the composer
for review; Talk to REIGN submits that one requested turn after Stop. There is no
continuous listening to the group. Camera/screen content and recordings are not
written to the durable conversation archive.

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
requested Markdown documents. The same configured Realtime provider narrates
each returned sentence. Narration has no tools, is checked against the requested
sentence's transcript, and is stored only in a bounded ten-minute audio cache.
Audio failure preserves the successful text/document result with an explicit
unavailable status. This is bounded turn-based speech, not streaming duplex
conversation or phoneme prediction. The browser samples the decoded PCM envelope
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
