"""Verified expression plans; provider responses are simulated, not audible acceptance."""
import asyncio
import json
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from reign.speech import PrivateSpeech


SETTINGS = {"OPENAI_API_KEY": "synthetic-expression-provider-key", "OPENAI_BASE_URL": "https://speech.example.test/v1",
            "OPENAI_MODEL": "configured-expression-model", "REALTIME_MODEL": "configured-realtime-model"}
THREAD = str(uuid.uuid4())
RUN = str(uuid.uuid4())


def response(segments, **overrides):
    return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": json.dumps({"segments": segments})}]}], **overrides})


class Runtime:
    def __init__(self, text="The draft is saved. Please review it."):
        self.value = {"thread_id": THREAD, "run_id": RUN, "status": "completed", "answer": text, "error": None}
        self.deleted = False

    def get(self, thread_id):
        if self.deleted or thread_id != THREAD:
            raise HTTPException(404, "Conversation expired")
        return SimpleNamespace(snapshot=lambda: dict(self.value))


class ExpressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, SETTINGS); env.start(); self.addCleanup(env.stop)
        self.runtime = Runtime()
        self.segments = [{"text": "The draft is saved.", "expression": "warm"},
                         {"text": "Please review it.", "expression": "thoughtful"}]
        self.http = SimpleNamespace(post=AsyncMock(return_value=response(self.segments)))
        self.speech = PrivateSpeech(self.http)

    async def test_same_sentence_model_labels_only_verified_text_and_cache_is_run_bound(self):
        self.runtime.value["answer"] = "The draft is saved.\n\nPlease review it."
        result = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(result["speech_status"], "ready")
        self.assertEqual(result["segments"], self.segments)
        self.assertEqual(result["text"], "The draft is saved. Please review it.")
        args = self.http.post.await_args
        self.assertEqual(args.args[0], SETTINGS["OPENAI_BASE_URL"] + "/responses")
        self.assertFalse(args.kwargs["follow_redirects"])
        payload = args.kwargs["json"]
        self.assertEqual(payload["model"], SETTINGS["OPENAI_MODEL"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertEqual(payload["input"][0]["content"][0]["text"], result["text"])
        self.assertNotIn(SETTINGS["OPENAI_API_KEY"], json.dumps(result))
        result["segments"][0]["text"] = "A caller cannot alter the cached plan."
        cached = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(cached["segments"], self.segments)
        self.http.post.assert_awaited_once()
        self.runtime.value["run_id"] = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as raised:
            await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(raised.exception.status_code, 409)
        self.runtime.deleted = True
        with self.assertRaises(HTTPException) as raised:
            await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_word_number_omission_reordering_and_invalid_mood_never_become_speech_cues(self):
        cases = [
            ("Use a part at 1.2 volts.", [{"text": "Use apart at 1.2 volts.", "expression": "warm"}]),
            ("Use a part at 1.2 volts.", [{"text": "Use a part at 12 volts.", "expression": "warm"}]),
            ("Do not send it.", [{"text": "Send it.", "expression": "warm"}]),
            ("First. Second.", [{"text": "Second.", "expression": "warm"}, {"text": "First.", "expression": "neutral"}]),
            ("Ready.", [{"text": "Ready.", "expression": "excited"}]),
            ("Ready.", [{"text": "Ready.", "expression": "warm", "command": "run tools"}]),
        ]
        for text, segments in cases:
            with self.subTest(text=text, segments=segments):
                self.http.post.return_value = response(segments)
                result = await PrivateSpeech(self.http).plan(Runtime(text), THREAD, RUN)
                self.assertEqual(result["speech_status"], "unavailable")
                self.assertEqual(result["segments"], [])
                self.assertEqual(result["text"], "")

    async def test_provider_tool_refusal_redirect_and_timeout_leave_neutral_voice_available(self):
        for provider in (
            httpx.Response(307, headers={"location": "https://untrusted.test/"}, text="private-provider-body"),
            httpx.Response(401, text="private-provider-body"),
            response(self.segments, status="incomplete"),
            response(self.segments, output=[{"type": "function_call", "name": "workspace_open"}]),
            response(self.segments, output=[{"type": "message", "role": "assistant", "content": [{"type": "refusal"}]}]),
        ):
            with self.subTest(provider=provider.status_code):
                self.http.post.return_value = provider
                result = await PrivateSpeech(self.http).plan(self.runtime, THREAD, RUN)
                self.assertEqual(result["speech_status"], "unavailable")
                self.assertNotIn("private-provider-body", json.dumps(result))
        self.http.post.side_effect = httpx.ReadTimeout("private-provider-body")
        result = await PrivateSpeech(self.http).plan(self.runtime, THREAD, RUN)
        self.assertEqual(result["speech_status"], "unavailable")
        self.assertNotIn("private-provider-body", json.dumps(result))
        # A real blocked coroutine must be cancelled by the outer deadline.
        async def blocked(text): await asyncio.Event().wait()
        self.speech.annotate = blocked
        with patch("reign.speech.ANNOTATION_TIMEOUT", .01):
            result = await asyncio.wait_for(self.speech.plan(self.runtime, THREAD, RUN), 1)
        self.assertEqual(result["speech_status"], "unavailable")
        self.assertEqual(self.speech.pending, set())

    async def test_new_run_during_annotation_cannot_receive_old_expressions(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def annotate(text):
            started.set(); await release.wait(); return self.segments
        self.speech.annotate = annotate
        task = asyncio.create_task(self.speech.plan(self.runtime, THREAD, RUN))
        await started.wait()
        self.runtime.value.update(run_id=str(uuid.uuid4()), status="running", answer="")
        release.set()
        with self.assertRaises(HTTPException) as raised: await task
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.speech.pending, set())
        self.assertEqual(len(self.speech.cache), 0)

    async def test_pending_requests_are_bounded_and_cancellation_does_not_leave_a_lock(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def annotate(text):
            started.set(); await release.wait(); return self.segments
        self.speech.annotate = annotate
        task = asyncio.create_task(self.speech.plan(self.runtime, THREAD, RUN))
        await started.wait()
        duplicate = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(duplicate["speech_status"], "unavailable")
        with patch("reign.speech.MAX_PENDING", 1):
            other = Runtime(); other.value["run_id"] = str(uuid.uuid4())
            crowded = await self.speech.plan(other, THREAD, other.value["run_id"])
            self.assertEqual(crowded["speech_status"], "unavailable")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.speech.pending, set())
        release.set()
        result = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(result["speech_status"], "ready")

    async def test_cache_expiry_and_capacity_do_not_grow_with_conversation_history(self):
        now = [100.]
        speech = PrivateSpeech(self.http, clock=lambda: now[0])
        with patch("reign.speech.MAX_CACHED_PLANS", 2):
            for _ in range(3):
                self.runtime.value["run_id"] = str(uuid.uuid4())
                await speech.plan(self.runtime, THREAD, self.runtime.value["run_id"])
            self.assertEqual(len(speech.cache), 2)
        count = self.http.post.await_count
        now[0] += 301
        await speech.plan(self.runtime, THREAD, self.runtime.value["run_id"])
        self.assertEqual(self.http.post.await_count, count + 1)
        self.assertEqual(len(speech.cache), 1)

    async def test_long_answers_and_nonterminal_runs_do_not_invoke_another_model(self):
        result = await self.speech.plan(Runtime("a" * 1601), THREAD, RUN)
        self.assertEqual(result["speech_status"], "unavailable")
        for state in ("running", "idle", "cancelled"):
            self.runtime.value["status"] = state
            with self.assertRaises(HTTPException): await self.speech.plan(self.runtime, THREAD, RUN)
        self.http.post.assert_not_awaited()

    async def test_failed_and_paused_runs_never_annotate_a_previous_success(self):
        self.runtime.value.update(status="failed", answer="The old action succeeded.", error="The task failed.")
        self.http.post.return_value = response([{"text": "The task failed.", "expression": "concerned"}])
        failed = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(failed["text"], "The task failed.")
        self.assertNotIn("succeeded", json.dumps(self.http.post.await_args.kwargs["json"]["input"]))
        self.runtime.value.update(status="paused", error=None)
        paused_text = "The task is paused while you control the browser. Return control, then press Resume."
        self.http.post.return_value = response([{"text": paused_text, "expression": "neutral"}])
        paused = await self.speech.plan(self.runtime, THREAD, RUN)
        self.assertEqual(paused["text"], paused_text)
        self.assertNotIn("succeeded", json.dumps(self.http.post.await_args.kwargs["json"]["input"]))


class SpeechHTTPTests(unittest.TestCase):
    def test_owner_ax_core_round_trip_and_request_boundary(self):
        from reign import ax
        from reign.core import app as core_app
        settings = {**SETTINGS, "CORE_TOKEN": "c" * 40, "AX_SESSION_KEY": "s" * 40,
                    "AX_ADMIN_PASSWORD": "p" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com",
                    "CORE_URL": "https://core.example.com"}
        runtime = Runtime("The draft is ready.")
        plan_segments = [{"text": "The draft is ready.", "expression": "warm"}]
        path = f"/api/chat/{THREAD}/speech"
        with patch.dict(os.environ, settings):
            with TestClient(core_app) as core, TestClient(ax.app, base_url="https://ax.example.com") as client:
                async def forward(method, url, **kwargs):
                    self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + "c" * 40)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core_app)) as inner:
                        return await inner.request(method, url, headers=kwargs["headers"], json=kwargs["json"])
                core_app.state.private_speech = PrivateSpeech(SimpleNamespace(post=AsyncMock(return_value=response(plan_segments))))
                with patch.object(core_app.state.agent, "get", side_effect=runtime.get), \
                     patch.object(ax.app.state.http, "request", side_effect=forward) as proxy:
                    self.assertEqual(core.post(path, json={"run_id": RUN}).status_code, 401)
                    self.assertEqual(client.post(path, json={"run_id": RUN}).status_code, 401)
                    client.post("/login", json={"password": "p" * 40})
                    self.assertEqual(client.post(path, json={"run_id": RUN}).status_code, 403)
                    headers = {"Origin": settings["AX_PUBLIC_ORIGIN"], "X-CSRF-Token": client.cookies.get("ax_csrf")}
                    self.assertEqual(client.post(path, headers={**headers, "Origin": "https://evil.test"}, json={"run_id": RUN}).status_code, 403)
                    for body in ({"run_id": RUN, "answer": "Invented client answer"}, {"run_id": "bad"}, {}):
                        self.assertEqual(client.post(path, headers=headers, json=body).status_code, 422)
                    self.assertEqual(client.post(path, headers=headers, content=b"x" * 257).status_code, 413)
                    proxy.assert_not_awaited()
                    result = client.post(path, headers=headers, json={"run_id": RUN})
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.headers["cache-control"], "no-store")
                    self.assertEqual(result.json()["segments"], plan_segments)
                    self.assertEqual(proxy.await_args.kwargs["json"], {"run_id": RUN})
                    stale = client.post(path, headers=headers, json={"run_id": str(uuid.uuid4())})
                    self.assertEqual(stale.status_code, 409)

    def test_owner_revocation_during_annotation_prevents_private_response_delivery(self):
        from reign import ax
        settings = {"CORE_TOKEN": "c" * 40, "AX_SESSION_KEY": "s" * 40,
                    "AX_ADMIN_PASSWORD": "p" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com"}
        with patch.dict(os.environ, settings), TestClient(ax.app, base_url=settings["AX_PUBLIC_ORIGIN"]) as client:
            client.post("/login", json={"password": "p" * 40})
            async def revoke_before_return(*args, **kwargs):
                for session in list(ax.app.state.auth.sessions.values()): ax.app.state.auth.revoke(session)
                return httpx.Response(200, json={"text": "private-answer-must-not-return", "speech_status": "ready"})
            with patch.object(ax, "core", side_effect=revoke_before_return):
                result = client.post(f"/api/chat/{THREAD}/speech", json={"run_id": RUN}, headers={
                    "Origin": settings["AX_PUBLIC_ORIGIN"], "X-CSRF-Token": client.cookies.get("ax_csrf")})
            self.assertEqual(result.status_code, 401)
            self.assertNotIn("private-answer-must-not-return", result.text)


if __name__ == "__main__":
    unittest.main()
