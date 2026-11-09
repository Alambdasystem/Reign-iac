"""One-shot provider/graph/audio release gate, not WebRTC or device acceptance.

Run only with the authorized Core runtime environment. Sends the checked-in
synthetic PCM fixture to the configured provider, executes the actual delegated
request through LangGraph with read-only status tools, and measures returned
audio in memory. The single receipt contains no credentials or transcript text.
"""
import asyncio
import base64
import hashlib
import json
import logging
import math
import sys
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from reign.voice import VoiceError, configuration, model_name, session_config

SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 60
MAX_EVENTS = 2000
MAX_EVENT_BYTES = 1024 * 1024
PROBE_TIMEOUT_SECONDS = 180
STAGES = ("configuration", "fixture", "connection", "session", "audio_input",
          "delegation", "langgraph", "narration", "audio_validation")
SAFE_PROVIDER_CODES = {"invalid_api_key", "model_not_found", "unsupported_model",
                       "insufficient_quota", "rate_limit_exceeded", "invalid_request_error",
                       "server_error", "session_expired", "invalid_value", "unknown_parameter"}
SAFE_PROVIDER_PARAMS = {"model", "session", "session.type", "session.model", "session.tool_choice",
                        "session.tools", "session.audio.input.transcription.model",
                        "session.audio.input.format", "session.audio.output.format",
                        "session.audio.input.turn_detection", "response.tool_choice",
                        "response.output_modalities"}


class ProbeFailure(Exception):
    def __init__(self, code, safe=None):
        self.code, self.safe = code, safe or {}
        super().__init__(code)


class PinnedConnect(connect):
    """Never forward the provider credential to a redirected endpoint."""
    def process_redirect(self, exc):
        return exc


def websocket_uri(base, model):
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ProbeFailure("invalid_provider_base")
    return urlunsplit(("wss", parsed.netloc, parsed.path.rstrip("/") + "/realtime",
                       urlencode({"model": model}), ""))


def websocket_session(settings):
    session = session_config(settings)
    # Model is selected in the WebSocket URL and cannot be changed by update.
    session.pop("model", None)
    session["audio"]["input"]["format"] = {"type": "audio/pcm", "rate": SAMPLE_RATE}
    session["audio"]["output"]["format"] = {"type": "audio/pcm", "rate": SAMPLE_RATE}
    session["audio"]["input"]["turn_detection"] = None
    return session


class PCMStats:
    """Stream energy statistics without retaining or writing generated audio."""
    def __init__(self):
        self.byte_count = 0
        self.sample_count = 0
        self.squares = 0
        self.carry = b""

    def add(self, data):
        if self.byte_count + len(data) > MAX_AUDIO_BYTES:
            raise ProbeFailure("output_audio_limit")
        self.byte_count += len(data)
        data = self.carry + data
        self.carry = data[-1:] if len(data) % 2 else b""
        samples = array("h")
        samples.frombytes(data[:len(data) - len(self.carry)])
        if sys.byteorder != "little":
            samples.byteswap()
        self.sample_count += len(samples)
        self.squares += sum(value * value for value in samples)

    def result(self):
        if self.carry:
            raise ProbeFailure("invalid_pcm_alignment")
        return {"bytes": self.byte_count,
                "duration_seconds": round(self.sample_count / SAMPLE_RATE, 4),
                "rms_normalized": round(math.sqrt(self.squares / self.sample_count) / 32768, 6)
                if self.sample_count else 0.0}

    def require_speech(self):
        result = self.result()
        if result["duration_seconds"] <= 0.25 or result["rms_normalized"] < 0.002:
            raise ProbeFailure("output_audio_silent_or_short")
        return result


def load_fixture(path=None):
    path = path or Path(__file__).with_name("fixtures") / "voice-status.wav"
    if path.stat().st_size > 1024 * 1024:
        raise ProbeFailure("fixture_too_large")
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ProbeFailure("invalid_fixture_format")
        if not 0.25 < source.getnframes() / SAMPLE_RATE <= 15:
            raise ProbeFailure("invalid_fixture_duration")
        pcm = source.readframes(source.getnframes())
    stats = PCMStats()
    stats.add(pcm)
    energy = stats.require_speech()
    return pcm, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "duration_seconds": energy["duration_seconds"], "sample_rate": SAMPLE_RATE}


def safe_provider_error(error):
    safe = {}
    if isinstance(error, dict):
        code, param = error.get("code"), error.get("param")
        if isinstance(code, str) and code in SAFE_PROVIDER_CODES:
            safe["provider_error_code"] = code
        if isinstance(param, str) and param in SAFE_PROVIDER_PARAMS:
            safe["provider_error_param"] = param
    return safe


class EventReader:
    def __init__(self, socket):
        self.socket, self.count = socket, 0
        self.input_transcript_present = False

    async def next(self):
        raw = await asyncio.wait_for(self.socket.recv(), 45)
        self.count += 1
        if self.count > MAX_EVENTS or len(raw) > MAX_EVENT_BYTES:
            raise ProbeFailure("provider_event_limit")
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ProbeFailure("invalid_provider_event") from None
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ProbeFailure("invalid_provider_event")
        if event["type"] == "error":
            raise ProbeFailure("provider_event_error", safe_provider_error(event.get("error")))
        if event["type"] == "conversation.item.input_audio_transcription.failed":
            raise ProbeFailure("input_transcription_failed", safe_provider_error(event.get("error")))
        if event["type"] == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript")
            self.input_transcript_present = isinstance(transcript, str) and bool(transcript.strip())
        return event

    async def until(self, event_type):
        while True:
            event = await self.next()
            if event["type"] == event_type:
                return event


def completed_response(event, failure_code):
    response = event.get("response")
    if not isinstance(response, dict) or response.get("status") != "completed":
        error = (response or {}).get("status_details", {}) if isinstance(response, dict) else {}
        raise ProbeFailure(failure_code, safe_provider_error(error.get("error") if isinstance(error, dict) else None))
    return response


async def collect_delegate(reader):
    calls = {}

    def record(item):
        if item.get("name") != "delegate_to_reign":
            raise ProbeFailure("unexpected_delegate_tool")
        call_id, arguments = item.get("call_id"), item.get("arguments")
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 256 or not isinstance(arguments, str) or len(arguments) > 20000:
            raise ProbeFailure("invalid_delegate_call")
        try:
            parsed = json.loads(arguments)
        except (ValueError, UnicodeError):
            raise ProbeFailure("invalid_delegate_arguments") from None
        if (not isinstance(parsed, dict) or set(parsed) != {"message"}
                or not isinstance(parsed["message"], str) or not 1 <= len(parsed["message"].strip()) <= 4000):
            raise ProbeFailure("invalid_delegate_arguments")
        if call_id in calls and calls[call_id] != parsed["message"]:
            raise ProbeFailure("inconsistent_delegate_call")
        calls[call_id] = parsed["message"]

    while True:
        event = await reader.next()
        if event["type"] == "response.function_call_arguments.done":
            record(event)
        elif event["type"] == "response.output_item.done":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "function_call":
                record(item)
        elif event["type"] == "response.output_audio.delta":
            raise ProbeFailure("audio_before_graph_result")
        elif event["type"] == "response.done":
            response = completed_response(event, "delegate_response_failed")
            for item in response.get("output", []):
                if isinstance(item, dict) and item.get("type") == "function_call":
                    record(item)
            # Wait for response.done before any narration response is submitted.
            if len(calls) != 1:
                raise ProbeFailure("missing_or_multiple_delegate_calls")
            return next(iter(calls.items()))


async def collect_audio(reader, receipt):
    stats, transcript_present, response_finished = PCMStats(), False, False
    while not (response_finished and reader.input_transcript_present):
        event = await reader.next()
        if event["type"] == "response.function_call_arguments.done":
            raise ProbeFailure("unexpected_second_delegation")
        if event["type"] == "response.output_audio.delta":
            try:
                delta = base64.b64decode(event.get("delta", ""), validate=True)
            except (ValueError, TypeError):
                raise ProbeFailure("invalid_audio_delta") from None
            stats.add(delta)
        elif event["type"] == "response.output_audio_transcript.done":
            transcript = event.get("transcript")
            transcript_present = isinstance(transcript, str) and bool(transcript.strip())
        elif event["type"] == "response.done":
            response = completed_response(event, "narration_response_failed")
            for item in response.get("output", []):
                if isinstance(item, dict) and item.get("type") == "function_call":
                    raise ProbeFailure("unexpected_second_delegation")
            response_finished = True
            if not transcript_present:
                raise ProbeFailure("output_transcript_missing")
    receipt["input_transcript_present"] = reader.input_transcript_present
    receipt["output_audio"] = {**stats.result(), "transcript_present": transcript_present}
    return stats


class StatusOnlyTools:
    def __init__(self):
        from reign.agent import CoreTools
        self.core, self.denied = CoreTools(), 0

    async def status(self):
        return await self.core.status()

    async def session(self, sid):
        self.denied += 1
        raise HTTPException(403, "Acceptance permits workspace status only")

    async def open(self, url):
        self.denied += 1
        raise HTTPException(403, "Acceptance permits workspace status only")

    async def navigate(self, sid, url, epoch):
        self.denied += 1
        raise HTTPException(403, "Acceptance permits workspace status only")


async def run_graph(message, receipt):
    from reign.core import app, lifespan
    async with lifespan(app):
        runtime = app.state.agent
        adapter = StatusOnlyTools()
        runtime.adapter = adapter
        initial = runtime.submit(message)
        thread = runtime.get(initial["thread_id"])
        await asyncio.wait_for(asyncio.shield(thread.task), 120)
        result = thread.snapshot()
        tools = [event.get("tool") for event in result["events"] if event.get("kind") == "tool_started"]
        known_tools = {"workspace_status", "browser_status", "open_browser", "navigate_browser"}
        receipt["graph"] = {"model": model_name("OPENAI_MODEL", "gpt-6-astra"),
                            "tools": [tool for tool in tools if tool in known_tools],
                            "usage": {key: max(0, int(result["usage"].get(key, 0)))
                                      for key in ("input_tokens", "output_tokens")}}
        success = any(event.get("kind") == "tool_completed" and event.get("tool") == "workspace_status"
                      and event.get("ok") is True for event in result["events"])
        if (result["status"] != "completed" or not success or adapter.denied
                or any(tool != "workspace_status" for tool in tools)):
            raise ProbeFailure("graph_status_tool_failed")
        answer = result.get("answer")
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 16000:
            raise ProbeFailure("graph_answer_missing_or_oversized")
        return {"status": "completed", "answer": answer, "orchestration": "langgraph",
                "tools": ["workspace_status"]}


def new_receipt():
    return {"verification": "reign_voice_provider", "status": "failed", "stage": "configuration",
            "stages": {stage: "not_run" for stage in STAGES}, "transport": "websocket",
            "input": "synthetic_pcm_manual_commit", "physical_device": "not_run",
            "webrtc_browser": "not_run", "input_transcript_present": False,
            "started_at": datetime.now(timezone.utc).isoformat()}


def stage(receipt, name):
    receipt["stage"] = name


def passed(receipt):
    receipt["stages"][receipt["stage"]] = "passed"


async def run_acceptance(receipt):
    async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
        settings = configuration()
        receipt["provider"] = {"model": settings["model"], "voice": "marin"}
        passed(receipt)
        stage(receipt, "fixture")
        pcm, receipt["fixture"] = load_fixture()
        passed(receipt)
        stage(receipt, "connection")
        async with PinnedConnect(websocket_uri(settings["base"], settings["model"]),
                                 additional_headers={"Authorization": "Bearer " + settings["key"]},
                                 open_timeout=15, close_timeout=3, max_size=MAX_EVENT_BYTES,
                                 max_queue=16, proxy=None) as socket:
            reader = EventReader(socket)
            await reader.until("session.created")
            passed(receipt)
            stage(receipt, "session")
            session = websocket_session(settings)
            await socket.send(json.dumps({"type": "session.update", "session": session}))
            await reader.until("session.updated")
            passed(receipt)
            stage(receipt, "audio_input")
            for offset in range(0, len(pcm), 32768):
                await socket.send(json.dumps({"type": "input_audio_buffer.append",
                                             "audio": base64.b64encode(pcm[offset:offset + 32768]).decode()}))
            await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await reader.until("input_audio_buffer.committed")
            passed(receipt)
            stage(receipt, "delegation")
            await socket.send(json.dumps({"type": "response.create", "response": {
                "tool_choice": {"type": "function", "name": "delegate_to_reign"}}}))
            call_id, message = await collect_delegate(reader)
            receipt["input_transcript_present"] = reader.input_transcript_present
            passed(receipt)
            stage(receipt, "langgraph")
            result = await run_graph(message, receipt)
            passed(receipt)
            stage(receipt, "narration")
            await socket.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call_id, "output": json.dumps(result)}}))
            await socket.send(json.dumps({"type": "response.create", "response": {
                "output_modalities": ["audio"], "tool_choice": "none"}}))
            stats = await collect_audio(reader, receipt)
            passed(receipt)
            stage(receipt, "audio_validation")
            stats.require_speech()
            passed(receipt)
            receipt["event_count"] = reader.count
        receipt["status"], receipt["stage"] = "passed", "complete"


def main():
    # This short-lived gate emits one sanitized JSON receipt, never SDK debug logs.
    previous_logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    receipt = new_receipt()
    try:
        asyncio.run(run_acceptance(receipt))
    except ProbeFailure as error:
        receipt.update(error.safe)
        receipt["error_code"] = error.code
    except VoiceError as error:
        receipt["error_code"] = error.code
    except InvalidStatus as error:
        status = error.response.status_code
        receipt["error_code"] = {401: "voice_provider_auth", 403: "voice_provider_access",
                                 429: "voice_provider_rate_limit"}.get(status, "provider_handshake_failed")
    except (TimeoutError, asyncio.TimeoutError):
        receipt["error_code"] = "probe_timeout"
    except ConnectionClosed:
        receipt["error_code"] = "provider_connection_closed"
    except Exception:
        receipt["error_code"] = "probe_internal_error"
    if receipt["status"] != "passed":
        receipt["stages"][receipt["stage"]] = "failed"
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    print("REIGN_VOICE_ACCEPTANCE " + json.dumps(receipt, separators=(",", ":")), flush=True)
    logging.disable(previous_logging_level)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
