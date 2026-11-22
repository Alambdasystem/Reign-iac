"""Actual bounded media/runtime paths with deliberately delayed synthetic speech."""
import asyncio
import io
import json
import tempfile
import unittest
import uuid
import wave
from unittest.mock import AsyncMock, patch

import httpx

from reign.team_api import TeamRuntime
from reign.team_assistant import TeamAssistant
from reign.team_media import MAX_AUDIO_BYTES, TeamMedia, TeamMediaError
from reign.team_store import TeamError, TeamStore, owner_identity


def clip():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(bytes(12000))
    return output.getvalue()


class SpokenReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="reign-voice-receipt-")
        self.addCleanup(self.directory.cleanup)
        self.now = 1000.0
        self.media = TeamMedia(clock=lambda: self.now)
        self.store = TeamStore(self.directory.name, bytes(range(32)))
        self.runtime = TeamRuntime(self.store)
        await self.runtime.start()
        self.actor = owner_identity()
        self.room = self.store.create_room(self.actor, "Synthetic spoken room")["room"]["id"]
        self.other = self.store.create_room(self.actor, "Other room")["room"]["id"]
        self.seen = []
        async def assistant(context, actor, text, attachments, speak):
            self.seen.append(context)
            return {"text": "I received your spoken request."}
        self.runtime.assistant = assistant
        self.runtime.transcription_validator = self.media.validate_transcription

    async def asyncTearDown(self):
        await self.runtime.shutdown()
        await self.media.aclose()

    def message(self, receipt, **changes):
        return {"client_message_id": str(uuid.uuid4()), "text": "Can you hear me?",
            "input_source": "voice", "transcription_id": receipt, "ask_reign": True, **changes}

    async def drain(self):
        tasks = [value["task"] for value in self.runtime.active.values()]
        if tasks: await asyncio.gather(*tasks)

    async def test_real_receipt_preserves_spoken_origin_in_run_archive_and_retry(self):
        key = self.media.save_transcription(self.actor, self.room, "Can you hear me?")
        request = self.message(key)
        accepted = await self.runtime.message(self.actor, self.room, request)
        await self.drain()
        self.assertEqual(self.seen[0]["input_source"], "voice")
        event = accepted["event"]
        self.assertEqual(event["data"]["input_source"], "voice")
        context = self.store.room_context(self.actor, self.room)
        self.assertEqual(context["messages"][0]["input_source"], "voice")
        # A lost acceptance response can be retried after the ephemeral receipt
        # expires without running the room's actions a second time.
        self.now += 301
        retry = await self.runtime.message(self.actor, self.room, request)
        self.assertEqual(retry["run_id"], accepted["run_id"])
        self.assertEqual(retry["event"]["id"], event["id"])
        self.assertEqual(len(self.seen), 1)
        typed = {"client_message_id": str(uuid.uuid4()), "text": "Now a typed message.", "ask_reign": True}
        await self.runtime.message(self.actor, self.room, typed)
        await self.drain()
        self.assertEqual(self.seen[-1]["input_source"], "text")

    async def test_forged_cross_room_cross_actor_edited_or_expired_source_never_runs(self):
        key = self.media.save_transcription(self.actor, self.room, "Can you hear me?")
        foreign = self.media.save_transcription({"id": str(uuid.uuid4())}, self.room, "Can you hear me?")
        for room, payload in (
            (self.room, self.message(str(uuid.uuid4()))),
            (self.other, self.message(key)),
            (self.room, self.message(foreign)),
            (self.room, self.message(key, text="These words were edited.")),
        ):
            with self.assertRaises(TeamMediaError): await self.runtime.message(self.actor, room, payload)
        self.now += 301
        with self.assertRaises(TeamMediaError): await self.runtime.message(self.actor, self.room, self.message(key))
        for payload in (self.message(key, input_source="microphone_admin"), self.message(key, input_source="text"),
                        self.message(key, transcription_id=None)):
            with self.assertRaises(TeamError): await self.runtime.message(self.actor, self.room, payload)
        self.assertFalse(self.seen)
        self.assertFalse(self.store.room_context(self.actor, self.room)["messages"])


class DelayedNarrator:
    def __init__(self):
        self.first_gate = asyncio.Event(); self.first_gate.set()
        self.next_gate = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.fail = False

    async def generate(self, segments, *, on_clip=None):
        self.started.set()
        try:
            await self.first_gate.wait()
            first = clip(); on_clip(0, first)
            await self.next_gate.wait()
            if self.fail: raise RuntimeError("private-provider-error-not-for-client")
            second = clip(); on_clip(1, second)
            return [first, second]
        finally:
            self.closed.set()


class ProgressiveSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.media = TeamMedia()
        self.narrator = DelayedNarrator()
        self.http = AsyncMock()
        self.segments = [{"text": "I heard your question.", "expression": "warm"},
                         {"text": "Let's work on it together.", "expression": "thoughtful"}]
        answer = {"segments": self.segments, "documents": []}
        self.http.post.return_value = httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]})
        self.assistant = TeamAssistant(self.http, self.media, self.narrator)
        self.actor = {"id": str(uuid.uuid4()), "display_name": "Synthetic speaker"}
        self.room = str(uuid.uuid4())
        self.context = {"room": {"id": self.room, "title": "Synthetic room"}, "messages": [],
                        "documents": [], "input_source": "voice"}
        self.configuration = patch("reign.team_assistant.configuration", return_value={
            "base": "https://provider.example/v1", "key": "synthetic-never-live", "model": "synthetic-model"})
        self.configuration.start(); self.addCleanup(self.configuration.stop)

    async def asyncTearDown(self):
        await self.media.aclose()

    async def reply(self):
        return await asyncio.wait_for(self.assistant(self.context, self.actor, "Can you hear me?", [], True), 1)

    async def test_first_verified_sentence_is_available_while_later_audio_is_blocked(self):
        answer = await self.reply()
        self.assertEqual(answer["speech_status"], "streaming")
        self.assertFalse(self.narrator.next_gate.is_set())
        first, second = [part["audio_id"] for part in answer["segments"]]
        self.assertEqual(await self.media.audio_bytes(first, self.room), clip())
        waiting = asyncio.create_task(self.media.audio_bytes(second, self.room))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        self.assertEqual(self.media.allocated_bytes(), len(clip()) + MAX_AUDIO_BYTES)
        payload = self.http.post.await_args.kwargs["json"]
        data = json.loads(payload["input"][0]["content"][0]["text"])
        self.assertEqual(data["input_source"], "voice")
        self.assertIn("do not call it typed text", payload["instructions"])
        self.narrator.next_gate.set()
        self.assertEqual(await asyncio.wait_for(waiting, 1), clip())
        await self.narrator.closed.wait()
        self.assertEqual(self.media.allocated_bytes(), 2 * len(clip()))
        self.assertEqual([part["expression"] for part in answer["segments"]], ["warm", "thoughtful"])

    async def test_later_failure_is_explicit_and_preserves_only_verified_audio(self):
        answer = await self.reply()
        first, second = [part["audio_id"] for part in answer["segments"]]
        waiter = asyncio.create_task(self.media.audio_bytes(second, self.room))
        self.narrator.fail = True; self.narrator.next_gate.set()
        with self.assertRaises(TeamMediaError): await asyncio.wait_for(waiter, 1)
        self.assertEqual(await self.media.audio_bytes(first, self.room), clip())
        self.assertEqual(self.media.allocated_bytes(), len(clip()))
        self.assertNotIn("private-provider", json.dumps(answer))

    async def test_cancel_before_first_clip_releases_producer_and_all_reservations(self):
        self.narrator.first_gate.clear()
        task = asyncio.create_task(self.assistant(self.context, self.actor, "Hello", [], True))
        await self.narrator.started.wait()
        self.assertEqual(self.media.allocated_bytes(), 2 * MAX_AUDIO_BYTES)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(self.narrator.closed.is_set())
        self.assertEqual(self.media.items, {})
        self.assertFalse(self.media.speech_tasks)

    async def test_shutdown_unblocks_waiters_cancels_provider_and_frees_memory(self):
        answer = await self.reply()
        waiter = asyncio.create_task(self.media.audio_bytes(answer["segments"][1]["audio_id"], self.room))
        await asyncio.sleep(0)
        await self.media.aclose()
        with self.assertRaises(TeamMediaError): await asyncio.wait_for(waiter, 1)
        self.assertTrue(self.narrator.closed.is_set())
        self.assertFalse(self.media.speech_tasks)
        self.assertEqual(self.media.items, {})

    async def test_audio_wait_remains_room_bound(self):
        answer = await self.reply()
        with self.assertRaises(TeamMediaError):
            await self.media.audio_bytes(answer["segments"][1]["audio_id"], str(uuid.uuid4()))

    async def test_close_before_producer_starts_cannot_leave_first_clip_waiter_hanging(self):
        task = asyncio.create_task(self.assistant.prepare_speech(self.room, self.segments))
        asyncio.get_running_loop().call_soon(self.media.close)
        with self.assertRaises(TeamError): await asyncio.wait_for(task, 1)
        self.assertEqual(self.media.items, {})
        self.assertFalse(self.media.speech_tasks)

    async def test_expired_pending_clip_releases_existing_waiter_immediately(self):
        clock = [1000.0]
        cache = TeamMedia(clock=lambda: clock[0])
        key = cache.reserve_audio(self.room, 1)[0]
        waiter = asyncio.create_task(cache.audio_bytes(key, self.room))
        await asyncio.sleep(0)
        clock[0] += 601
        cache.prune()
        with self.assertRaises(TeamMediaError): await asyncio.wait_for(waiter, 1)
        await cache.aclose()


if __name__ == "__main__": unittest.main()
