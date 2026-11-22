# REIGN live voice

## Private workspace flow

UltraView keeps its existing design and adds Start voice, microphone mute,
End call, speaker-unlock recovery, and live captions. A call uses browser WebRTC
to the configured OpenAI-compatible Realtime provider. Core brokers the SDP
exchange; the permanent provider key never reaches AX or the browser.

The transport follows Workmates' existing WebRTC integration. The REIGN voice
session exposes exactly one tool, `delegate_to_reign`, and requires that named
tool for every normal user turn. The browser sends its request through the same
LangGraph conversation used by typed messages. There is no separate voice agent
allowed to navigate the computer or perform workspace actions.

Delegation responses use `output_modalities: ["text"]`, with microphone audio
still enabled as input. Named tool choice alone does not prevent a model from
emitting an audio preamble: the first live release probe observed that behavior
and blocked Core promotion. Keeping delegation in text output mode prevents
speech before the graph supplies a verified result. Text preambles are not
presented as a graph answer.

Only a completed, failed, or paused graph result can be returned as the tool
result. A following `response.create` explicitly enables
`output_modalities: ["audio"]` and overrides tool choice to `none` for that
single narration response. Subsequent user turns again use text output and
require delegation. The startup connection check follows the same phase rules.
Spoken narration is model-generated; the on-screen LangGraph result remains the
authoritative record of actual tool actions.

After a graph run finishes, the private interface can request sentence expression
cues through owner-only `POST /api/chat/{thread_id}/speech` with exactly
`{"run_id":"<current graph run UUID>"}`. AX requires its current owner session,
same-origin CSRF and a second session check after the model call. Core requires
its service bearer token and resolves the current graph result itself; the
client cannot supply answer text, a model name or an expression. Nonterminal,
deleted and superseded runs cannot obtain a plan, including from the cache.

The annotation uses the same configured Responses model and sentence schema as
team replies: up to four segments of 400 characters, each labeled `neutral`,
`warm`, `thoughtful` or `concerned`. Its tools are disabled. The server requires
complete, ordered agreement with the verified answer after whitespace-only
normalization, preserving words, numbers and punctuation. Failed or paused runs
use their current failure/status text rather than an earlier successful answer.
Successful responses contain `thread_id`, `run_id`, `status`, `text`, `segments`
and `speech_status: "ready"`. Overlong answers or unavailable, invalid or slow
annotations return `speech_status: "unavailable"`, empty text and no segments;
the existing verified voice narration remains available with a neutral face.

Expression preparation has a twelve-second model deadline, at most two pending
requests and 64 process-local plans retained for five minutes. It starts alongside
spoken-answer preparation; it is no longer an extra model round trip that blocks
the first audio. The client segments the exact verified answer into bounded
sentences and starts neutral narration immediately. A returned model annotation
may label a sentence only when its entire exact text and position fall within
one annotated span and that sentence has not started playing. This preserves
model cues when the annotation groups adjacent sentences with the same tone.
A sentence crossing annotation spans stays neutral. A late annotation cannot
change an expression mid-sentence. The client rejects late
plans after interruption, call end or replacement of the spoken answer. Long
answers outside the four-by-400-character expression bound retain the existing
neutral verified-result narration.

This preparation is separate from executing the graph and never retries an
action. Private narration binds each segment to its own verified audio response;
expression changes follow playback, not the arrival of model text. The segment
schema has no phoneme or word timestamps. Output-energy mouth motion is not a
claim of phoneme-accurate lip synchronization.

Actual microphone turns send `input_source: "voice"` with the graph request, so
the graph can preserve their spoken origin even though delegated words use a
text representation internally. Typed requests default to `"text"`; the generated
startup connection check is also text, not a claim of user speech. Active voice
runs poll graph completion every 250 ms rather than the typed interface's 750 ms.

Typed and spoken requests share thread ID, browser session, process-local
checkpoints, trace events, and usage. Requests are serialized to protect the
graph. The existing twelve-turn conversation limit is exposed explicitly so
the UI can start a new conversation without retrying an action ambiguously.
Human takeover still pauses browser actions until control is returned and the
user resumes the run.

## Continuous shared Workspace voice

In `/team`, **Talk with REIGN → Microphone on** starts a persistent WebRTC
microphone connection to the Realtime model. Audio streams while the microphone
track is enabled, across pauses and successive turns. Server VAD identifies
speech boundaries and interruptions; it does not end or reacquire the input
track after each sentence. **Mute mic** disables that track while preserving
the connection, and **Microphone off** stops capture and closes the owned call.
This live path has no 20-second recording or utterance timer.

The shared adapter reuses the private voice controller with
`connectionCheck: false`, so it listens immediately without submitting a
generated greeting. Each provider request still requires `delegate_to_reign`.
The adapter routes that request to the authenticated room assistant, preserving
its member, room, viewer connection, and stable message ID. Private owner-chat
routes are not used. The room's saved answer and expression segments return
through the existing room event stream before verified Realtime narration
begins. Text passed to the room model is the representation of a spoken request;
it does not itself prove waveform contents or grant additional tool authority.

The same native output clock and output-only meter synchronize REIGN's face.
Real provider speech-start clears current narration and calls
`onUserSpeechStart()` so the room can also stop separate cached-clip playback.
The microphone remains connected during this interruption. The initiating
caller suppresses duplicate cached playback for its own voice session; other
room viewers may receive independently verified sentence clips as they become
available. Later requests remain serialized with the room's actual work.

Room endpoints are `GET /api/team/rooms/{room_id}/voice`, `POST .../voice/offer`,
`POST .../voice/sessions/{session_id}/turns`, and
`DELETE .../voice/sessions/{session_id}`. Core validates the actor and room before
negotiation and again after the provider response. Its provider call ID stays
inside Core; the browser receives a separate room session ID. The broker
accepts only the configured provider's exact call resource from `Location` and
constructs hangup requests from the validated opaque ID. The broker transfers
that ID to the owning room service as soon as response headers arrive, before
waiting for the SDP body. The owner retains capacity and retries failed cleanup.
If no owner accepted the allocation, the broker closes it or preserves the ID in
internal exception metadata for recovery. Neither path exposes it in public
error bodies. A confirmed provider close releases its reservation only after the
room's cancellation and durable run settlement also succeed; failed settlement
remains retryable without repeating a confirmed hangup.

One active shared call per member and four total calls are allowed. Presence
expiry, room leave, revocation, explicit Off and shutdown initiate closure of the owned
provider call. An Off request can retire a session before its in-flight offer
finishes; a late provider result is closed rather than reviving capture. Missing
or uncertain task receipts require an explicit retry with the same message ID.

Graceful shutdown is bounded and best effort. Render gives this disk-backed Core
a 30-second shutdown window and does not allow extending it. The Core image sets
`UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=2`, honored by both its normal and diagnostic
Uvicorn CLI startup, so in-flight HTTP requests cannot consume that whole window
before lifespan cleanup begins. TeamVoice then uses one 18-second deadline:
it retires calls immediately, starts known provider hangups alongside room
cancellation, reserves time to close pending allocations, and permits at most one
hangup retry within the remaining budget. Successful closes are not repeated.
The optional Tailscale supervisor shares a 25-second child-process grace. These
are process-shutdown budgets, not microphone or utterance time limits. A failed
hangup or a request that ends before a validated allocation ID arrives remains
unconfirmed; shutdown never claims that it proved the remote call ended. The
Tailscale feature flag and separate activation rollout remain unchanged. See
[Render's graceful shutdown rules](https://render.com/docs/deploys#graceful-shutdown).

Shared sessions use the provider's documented **60-minute maximum**. The
controller honors the server capability up to 3600 seconds and reports the
actual expired duration; reconnecting requires another Microphone on action.
The existing private workspace default remains 900 seconds. These are whole
connection limits, not per-utterance recording windows. See the official
[Realtime session lifecycle](https://developers.openai.com/api/docs/guides/realtime-conversations)
and [WebRTC hangup API](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/hangup).

## Configuration and endpoints

No new service or permanent credential is needed. Core already inherits the
Workmates `OPENAI_API_KEY` through the existing Render service reference.

| Setting | Default / meaning |
| --- | --- |
| `OPENAI_API_KEY` | Existing server-only credential; never logged or returned |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1`; a configured key stays paired with this host |
| `REALTIME_MODEL` | `gpt-realtime-2.1`, matching Workmates' transport |
| `REALTIME_TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe`, configurable for provider compatibility |
| Output voice | `marin` |
| Server VAD | 450 ms silence, 300 ms pre-roll; interrupts output on new speech |
| Private call duration | 900 seconds, enforced by the browser lifecycle |
| Shared call duration | Up to 3600 seconds; explicit reconnect after expiry |
| Offer limits | 64 KiB SDP; 96 KiB JSON body; two pending offers; six starts per minute |

`GET /api/me` advertises implementation support. Authenticated `GET /api/voice`
returns `configured`, `provider`, `model`, `voice`, `transport`,
`orchestration`, and `max_duration_seconds`. `configured` is a configuration
check, not evidence that the account has model access or that a call connected.

Authenticated `POST /api/voice/offer` accepts exactly `{"sdp":"..."}`. AX
requires its HttpOnly session plus same-origin CSRF token; Core requires its
existing service bearer token. Success returns an SDP answer and non-secret
model/duration metadata. Provider requests use bounded timeouts with no
redirects, retries, or fallback to a different credential destination.

Errors use a closed vocabulary of safe `code` and `detail` fields. Credential,
model access, rate-limit, timeout, invalid-offer, and unavailable-service errors
have distinct recovery messages. Raw SDP, credentials, and provider error
bodies are never included in diagnostic logs.

The compatibility transcription default follows Workmates. Current provider
documentation schedules that model family for retirement in February 2027;
validate a replacement through `REALTIME_TRANSCRIPTION_MODEL` before then.
This implementation does not use the deprecated `/audio/speech` endpoint.

## Media lifecycle

Start voice requests microphone access and gathers the SDP offer. Input uses
echo cancellation, noise suppression, and automatic gain control. The incoming
audio track is attached to an inline audio element. If the browser blocks
playback, Enable speaker retries from a fresh user gesture.

The native audio element is the only audible sink. Its playback recovery starts
directly from that gesture and never waits for the separate metering context to
resume. If a verified sentence streamed while native playback was blocked, the
same gesture can request that sentence again; it does not rerun the graph or any
workspace action. Waiting/stalled playback and a frozen media clock close the
mouth instead of allowing the independent analyser to imply audible output.

Private sentence cues begin with verified output energy and use native media
time. The next segment waits for the matching provider response to finish and
its audio buffer to stop, then for the observed tail to drain. Without a working
meter, the conservative fallback requires native player advancement and reports
that weaker evidence. A mismatched completed sentence transcript stops further
narration; this check cannot retroactively validate audio already streamed.

Mute disables the actual microphone track. End call stops tracks, closes the
peer/data channel, aborts negotiation, clears timers, and cancels only the
unfinished graph work owned by that call. Completed external actions are not
reversed. Interruption stops spoken playback; an already-running workspace
action must not be silently replayed. Later requests queue within a small bound.

Call generation IDs and provider call IDs fence stale callbacks and duplicate
function events. Provider response generation and speaker playback are tracked
separately. Incoming audio bytes, changing audio energy, and player advancement
are diagnostics; none independently proves a physical speaker was audible.

## Verification levels

1. Python tests exercise actual AX-to-Core forwarding with in-process transports,
   provider request shape, authorization/CSRF, safe errors, and bounds. Provider
   responses in these tests are simulated.
2. Node tests exercise the real voice state machine with explicitly simulated
   microphone, WebRTC, provider, and graph events. They cover lifecycle and race
   conditions; they are not provider or hardware acceptance.
3. The four-container browser smoke retains real Core/Temporal/Computer checks
   and verifies honest voice capability behavior without a live provider key.
4. `python -m reign.voice_acceptance` is a separate paid-provider release probe.
   It sends a synthetic spoken fixture through the configured Realtime model,
   requires actual LangGraph `workspace_status` execution against live services,
   and verifies non-silent returned PCM audio. It runs only in an authorized
   runtime containing the existing key and emits a sanitized receipt. Its
   WebSocket/manual-commit transport is explicitly distinct from browser WebRTC.
   Audio emitted before the graph result fails the gate; that check is retained
   even though delegation now explicitly disables audio output.
   The existing `python -m reign.team_acceptance` release gate also annotates a
   fixed synthetic two-sentence answer with the private expression model. It
   requires exact text coverage and the bounded expression schema within twelve
   seconds, returning only a sanitized boolean receipt. Failure blocks that
   gate; no user conversation or archive is read or written by this fixture.
5. Live UltraView acceptance must verify WebRTC connection, greeting/audio
   delivery, shared text/voice conversation, mute, interruption, and cleanup.
   Android microphone/speaker acceptance is a separate physical-device check.

Never label simulated events, SDP success, `response.done`, or health checks as
successful end-to-end audible voice. Record passed, failed, and not-run stages.

## References

- Workmates API revision `15b268a0502bc983a8ad835d8707e7d7148163e9`, `solo.py`
- Workmates Core revision `985691442d298f5a6d88669a5c753e44cf06173f`,
  `static/solo.js`, `static/call_lifecycle.js`, `static/call_audio.js`
- [Realtime tools](https://developers.openai.com/api/docs/guides/realtime-mcp)
- [Realtime client events](https://developers.openai.com/api/reference/resources/realtime/client-events)
- [Create a Realtime call](https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/create)
