"""Local probe validation. Scripted events are not live audio acceptance."""
import base64
import contextlib
import io
import json
import os
import unittest
from array import array
from unittest.mock import AsyncMock, patch

from reign.voice import DEFAULT_MODEL
from reign.voice_acceptance import (EventReader, PCMStats, PinnedConnect, ProbeFailure,
                                    collect_audio, collect_delegate, load_fixture, main,
                                    new_receipt, run_acceptance, websocket_session, websocket_uri)

PCM = array("h", [2000, -2000] * 12000).tobytes()
MESSAGE = "Check whether all workspace services are ready, please."
CALL = {"type": "function_call", "name": "delegate_to_reign", "call_id": "test-call",
        "arguments": json.dumps({"message": MESSAGE})}


class FakeSocket:
    def __init__(self, events):
        self.events, self.sent, self.received = list(events), [], []
        self.closed = False

    async def recv(self):
        if not self.events:
            raise AssertionError("Probe read beyond scripted events")
        event = self.events.pop(0)
        self.received.append(event)
        return json.dumps(event)

    async def send(self, raw):
        event = json.loads(raw)
        if event["type"] == "response.create" and event.get("response", {}).get("tool_choice") == "none":
            if not any(item.get("type") == "response.done" and item["response"]["id"] == "delegate"
                       for item in self.received):
                raise AssertionError("Narration began before delegate response.done")
        self.sent.append(event)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


def delegate_events():
    return [{"type": "response.created", "response": {"id": "delegate"}},
            {**CALL, "type": "response.function_call_arguments.done"},
            {"type": "response.output_item.done", "item": CALL},
            {"type": "response.done", "response": {"id": "delegate", "status": "completed", "output": [CALL]}}]


def audio_events(pcm=PCM):
    return [{"type": "response.created", "response": {"id": "narration"}},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(pcm).decode()},
            {"type": "response.output_audio_transcript.done", "transcript": "The services are ready."},
            {"type": "response.done", "response": {"id": "narration", "status": "completed", "output": []}}]


class ProbeHelperTests(unittest.TestCase):
    def test_pcm_statistics_detect_silence_short_output_and_odd_chunks(self):
        stats = PCMStats()
        stats.add(PCM[:3])
        stats.add(PCM[3:])
        result = stats.require_speech()
        self.assertEqual(result["bytes"], 48000)
        self.assertEqual(result["duration_seconds"], 1.0)
        self.assertAlmostEqual(result["rms_normalized"], 2000 / 32768, places=6)
        for data in (b"\x00" * 48000, PCM[:1000]):
            stats = PCMStats()
            stats.add(data)
            with self.assertRaises(ProbeFailure):
                stats.require_speech()
        stats = PCMStats()
        stats.add(b"\x01")
        with self.assertRaises(ProbeFailure):
            stats.result()

    def test_fixture_is_real_bounded_non_silent_pcm(self):
        pcm, metadata = load_fixture()
        self.assertGreater(len(pcm), 48000)
        self.assertEqual(metadata["sample_rate"], 24000)
        self.assertTrue(0.25 < metadata["duration_seconds"] <= 15)
        self.assertEqual(len(metadata["sha256"]), 64)

    def test_websocket_config_uses_same_voice_tool_and_only_transport_overrides(self):
        settings = {"model": DEFAULT_MODEL, "transcription": "configured-transcriber"}
        session = websocket_session(settings)
        self.assertNotIn("model", session)
        self.assertEqual(session["type"], "realtime")
        self.assertEqual(session["output_modalities"], ["text"])
        self.assertEqual(session["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(session["audio"]["output"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertIsNone(session["audio"]["input"]["turn_detection"])
        self.assertEqual(session["audio"]["input"]["transcription"]["model"], "configured-transcriber")
        self.assertEqual(session["audio"]["output"]["voice"], "marin")
        self.assertEqual(session["tool_choice"], {"type": "function", "name": "delegate_to_reign"})
        self.assertEqual([tool["name"] for tool in session["tools"]], ["delegate_to_reign"])

    def test_provider_uri_and_handshake_never_fall_back_or_redirect(self):
        self.assertEqual(websocket_uri("https://configured.test/custom/v1/", "my-model"),
                         "wss://configured.test/custom/v1/realtime?model=my-model")
        self.assertEqual(websocket_uri("https://api.openai.com/v1", DEFAULT_MODEL),
                         "wss://api.openai.com/v1/realtime?model=" + DEFAULT_MODEL)
        for base in ("http://wrong.test/v1", "https://user:secret@example.test/v1", "https://example.test?secret=x"):
            with self.assertRaises(ProbeFailure):
                websocket_uri(base, DEFAULT_MODEL)
        error = RuntimeError("private handshake message")
        self.assertIs(PinnedConnect.process_redirect(None, error), error)

    def test_cli_failure_emits_one_sanitized_receipt(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("REIGN_VOICE_ACCEPTANCE "))
        receipt = json.loads(lines[0].split(" ", 1)[1])
        self.assertEqual(receipt["error_code"], "voice_not_configured")
        self.assertEqual(receipt["stages"]["configuration"], "failed")
        self.assertEqual(receipt["physical_device"], "not_run")
        self.assertEqual(receipt["webrtc_browser"], "not_run")


class ProbeEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_delegate_is_deduplicated_and_waits_for_response_done(self):
        # Text preambles aren't spoken or treated as a completed graph answer.
        socket = FakeSocket([{"type": "response.output_text.delta", "delta": "Checking that request."},
                             *delegate_events()])
        call_id, message = await collect_delegate(EventReader(socket))
        self.assertEqual((call_id, message), ("test-call", MESSAGE))
        self.assertEqual(socket.received[-1]["type"], "response.done")
        self.assertEqual(socket.events, [])

    async def test_audio_before_graph_result_still_fails_the_gate(self):
        socket = FakeSocket([{"type": "response.output_audio.delta", "delta": base64.b64encode(PCM).decode()},
                             *delegate_events()])
        with self.assertRaises(ProbeFailure) as caught:
            await collect_delegate(EventReader(socket))
        self.assertEqual(caught.exception.code, "audio_before_graph_result")

    async def test_failed_or_multiple_calls_cannot_be_executed(self):
        for events in ([{"type": "response.done", "response": {"status": "failed", "output": [CALL]}}],
                       [{"type": "response.done", "response": {"status": "completed", "output": [CALL, {**CALL, "call_id": "another"}]}}]):
            with self.assertRaises(ProbeFailure):
                await collect_delegate(EventReader(FakeSocket(events)))

    async def test_audio_requires_real_bytes_and_accepts_late_input_transcription(self):
        socket = FakeSocket([*audio_events(), {"type": "conversation.item.input_audio_transcription.completed",
                                               "transcript": MESSAGE}])
        receipt = new_receipt()
        stats = await collect_audio(EventReader(socket), receipt)
        self.assertGreater(stats.require_speech()["rms_normalized"], 0.002)
        self.assertTrue(receipt["input_transcript_present"])
        self.assertTrue(receipt["output_audio"]["transcript_present"])
        self.assertNotIn(MESSAGE, json.dumps(receipt))
        reader = EventReader(FakeSocket(audio_events(b"\x00" * 48000)))
        reader.input_transcript_present = True
        stats = await collect_audio(reader, new_receipt())
        with self.assertRaises(ProbeFailure):
            stats.require_speech()

    async def test_provider_error_only_exposes_allowlisted_metadata(self):
        socket = FakeSocket([{"type": "error", "error": {"code": "invalid_api_key",
                             "param": "private-secret", "message": "credential PRIVATE-KEY"}}])
        with self.assertRaises(ProbeFailure) as caught:
            await EventReader(socket).next()
        self.assertEqual(caught.exception.safe, {"provider_error_code": "invalid_api_key"})
        self.assertNotIn("PRIVATE", str(caught.exception))

    async def test_scripted_full_probe_uses_actual_delegate_once_and_labels_limits(self):
        socket = FakeSocket([{"type": "session.created"}, {"type": "session.updated"},
                             {"type": "input_audio_buffer.committed"},
                             {"type": "conversation.item.input_audio_transcription.completed", "transcript": MESSAGE},
                             *delegate_events(), *audio_events()])
        graph = AsyncMock(return_value={"status": "completed", "answer": "All services report ready.",
                                       "orchestration": "langgraph", "tools": ["workspace_status"]})
        receipt = new_receipt()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "local-fake-key", "OPENAI_BASE_URL": "https://voice.test/v1",
                                     "REALTIME_MODEL": DEFAULT_MODEL, "REALTIME_TRANSCRIPTION_MODEL": "gpt-4o-mini-transcribe"}), \
             patch("reign.voice_acceptance.PinnedConnect", return_value=socket) as connector, \
             patch("reign.voice_acceptance.run_graph", graph):
            await run_acceptance(receipt)
        graph.assert_awaited_once_with(MESSAGE, receipt)
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(all(value == "passed" for value in receipt["stages"].values()))
        self.assertEqual(receipt["transport"], "websocket")
        self.assertEqual(receipt["input"], "synthetic_pcm_manual_commit")
        self.assertEqual(receipt["physical_device"], "not_run")
        self.assertEqual(receipt["webrtc_browser"], "not_run")
        self.assertTrue(socket.closed)
        self.assertIsNone(connector.call_args.kwargs["proxy"])
        sent_types = [event["type"] for event in socket.sent]
        self.assertGreater(sent_types.count("input_audio_buffer.append"), 1)
        self.assertEqual(sent_types.count("response.create"), 2)
        responses = [event["response"] for event in socket.sent if event["type"] == "response.create"]
        self.assertEqual(responses[0]["output_modalities"], ["text"])
        self.assertEqual(responses[1]["output_modalities"], ["audio"])
        self.assertEqual(responses[1]["tool_choice"], "none")
        outputs = [event for event in socket.sent if event["type"] == "conversation.item.create"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["item"]["call_id"], "test-call")
        self.assertNotIn("local-fake-key", json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
