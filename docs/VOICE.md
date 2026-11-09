# REIGN live voice

## Application flow

UltraView keeps its existing design and adds Start voice, microphone mute,
End call, speaker-unlock recovery, and live captions. A call uses browser WebRTC
to the configured OpenAI-compatible Realtime provider. Core brokers the SDP
exchange; the permanent provider key never reaches AX or the browser.

The transport follows Workmates' existing WebRTC integration. The REIGN voice
session exposes exactly one tool, `delegate_to_reign`, and requires that named
tool for every normal user turn. The browser sends its request through the same
LangGraph conversation used by typed messages. There is no separate voice agent
allowed to navigate the computer or perform workspace actions.

Only a completed, failed, or paused graph result can be returned as the tool
result. A following `response.create` overrides tool choice to `none` for that
single narration response. Subsequent user turns again require delegation.
Spoken narration is model-generated; the on-screen LangGraph result remains the
authoritative record of actual tool actions.

Typed and spoken requests share thread ID, browser session, process-local
checkpoints, trace events, and usage. Requests are serialized to protect the
graph. The existing twelve-turn conversation limit is exposed explicitly so
the UI can start a new conversation without retrying an action ambiguously.
Human takeover still pauses browser actions until control is returned and the
user resumes the run.

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
| Call duration | Fifteen minutes, enforced by the browser lifecycle |
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
