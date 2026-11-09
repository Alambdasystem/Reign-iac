"""Voice broker and authenticated HTTP contracts; no real provider/audio claims."""
import asyncio
import json
import os
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from reign.voice import (DEFAULT_MODEL, MAX_OFFERS_PER_MINUTE, MAX_REQUEST_BYTES,
                         MAX_SDP_BYTES, VoiceBroker, VoiceError, capabilities)

SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
KEY = "unit-test-provider-key-never-live"
SETTINGS = {"OPENAI_API_KEY": KEY, "OPENAI_BASE_URL": "https://voice.example.test/v1",
            "REALTIME_MODEL": DEFAULT_MODEL, "REALTIME_TRANSCRIPTION_MODEL": "gpt-4o-mini-transcribe"}


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    def test_capabilities_are_safe_and_unconfigured_without_key(self):
        with patch.dict(os.environ, {**SETTINGS, "OPENAI_API_KEY": ""}):
            result = capabilities()
            self.assertFalse(result["configured"])
            self.assertEqual(result["provider"], "openai_realtime")
            self.assertEqual(result["model"], DEFAULT_MODEL)
            self.assertEqual(result["max_duration_seconds"], 900)
            self.assertNotIn("key", result)
            self.assertNotIn("base", result)
        for base in ("", "http://insecure.test/v1", "https://user:pass@example.test/v1",
                     "https://example.test/v1?key=private", "https://example.test/v1#secret"):
            with self.subTest(base=base), patch.dict(os.environ, {**SETTINGS, "OPENAI_BASE_URL": base}):
                self.assertFalse(capabilities()["configured"])

    async def test_real_multipart_negotiation_pins_provider_and_delegates_to_langgraph(self):
        requests = []

        async def provider(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://voice.example.test/v1/realtime/calls")
            self.assertEqual(request.headers["authorization"], "Bearer " + KEY)
            raw = await request.aread()
            mime = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + raw)
            fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                      for part in mime.iter_parts()}
            self.assertEqual(set(fields), {"sdp", "session"})
            self.assertEqual(fields["sdp"].decode(), SDP)
            session = json.loads(fields["session"])
            self.assertNotIn(KEY, fields["session"].decode())
            self.assertEqual(session["type"], "realtime")
            self.assertEqual(session["model"], "configured-realtime-model")
            self.assertEqual(session["output_modalities"], ["text"])
            self.assertEqual(session["audio"]["output"]["voice"], "marin")
            self.assertEqual(session["audio"]["input"]["transcription"]["model"], "configured-transcriber")
            self.assertTrue(session["audio"]["input"]["turn_detection"]["interrupt_response"])
            self.assertEqual(session["max_output_tokens"], 1200)
            self.assertEqual(len(session["tools"]), 1)
            self.assertEqual(session["tools"][0]["name"], "delegate_to_reign")
            self.assertEqual(session["tools"][0]["parameters"]["required"], ["message"])
            self.assertFalse(session["tools"][0]["parameters"]["additionalProperties"])
            self.assertEqual(session["tool_choice"], {"type": "function", "name": "delegate_to_reign"})
            self.assertIn("EVERY user request", session["instructions"])
            return httpx.Response(201, text=SDP, headers={"location": "/v1/realtime/calls/private-id"})

        with patch.dict(os.environ, {**SETTINGS, "REALTIME_MODEL": "configured-realtime-model",
                                    "REALTIME_TRANSCRIPTION_MODEL": "configured-transcriber"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
                result = await VoiceBroker(http).offer(SDP)
        self.assertEqual(result, {"sdp": SDP, "type": "answer", "model": "configured-realtime-model",
                                  "max_duration_seconds": 900})
        self.assertEqual(len(requests), 1)
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn("private-id", json.dumps(result))

    async def test_upstream_errors_are_sanitized_without_redirect_or_provider_fallback(self):
        for status, expected, code in ((401, 503, "voice_provider_auth"),
                                      (403, 503, "voice_provider_access"),
                                      (429, 429, "voice_provider_rate_limit"),
                                      (400, 503, "voice_provider_setup"),
                                      (404, 503, "voice_provider_setup"),
                                      (500, 502, "voice_provider_unavailable"),
                                      (307, 502, "voice_provider_unavailable")):
            calls = []

            def provider(request):
                calls.append(str(request.url))
                return httpx.Response(status, text="secret-provider-body " + KEY,
                                      headers={"location": "https://other-provider.test/steal"})

            with self.subTest(status=status), patch.dict(os.environ, SETTINGS):
                async with httpx.AsyncClient(transport=httpx.MockTransport(provider), follow_redirects=True) as http:
                    broker = VoiceBroker(http)
                    with self.assertRaises(VoiceError) as caught:
                        await broker.offer(SDP)
                    self.assertEqual(caught.exception.status_code, expected)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn(KEY, str(caught.exception))
                    self.assertNotIn("secret-provider-body", str(caught.exception))
                    self.assertEqual(broker.pending, 0)
                self.assertEqual(calls, ["https://voice.example.test/v1/realtime/calls"])

    async def test_timeouts_and_network_errors_do_not_leak_details(self):
        for error, code in ((httpx.ReadTimeout, "voice_provider_timeout"),
                            (httpx.ConnectError, "voice_provider_unavailable")):
            def provider(request):
                raise error(KEY, request=request)
            with self.subTest(error=error), patch.dict(os.environ, SETTINGS):
                async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
                    with self.assertRaises(VoiceError) as caught:
                        await VoiceBroker(http).offer(SDP)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn(KEY, str(caught.exception))

    async def test_invalid_and_oversized_answers_fail_closed(self):
        for answer in ("not SDP " + KEY, SDP + "x" * MAX_SDP_BYTES, b"\xff\xfe"):
            with self.subTest(size=len(answer)), patch.dict(os.environ, SETTINGS):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda request: httpx.Response(201, content=answer))) as http:
                    with self.assertRaises(VoiceError) as caught:
                        await VoiceBroker(http).offer(SDP)
                    self.assertEqual(caught.exception.code, "voice_invalid_answer")
                    self.assertNotIn(KEY, str(caught.exception))

    async def test_no_key_or_invalid_configuration_never_calls_provider(self):
        for overrides, code in (({"OPENAI_API_KEY": ""}, "voice_not_configured"),
                                ({"OPENAI_BASE_URL": ""}, "voice_invalid_configuration")):
            provider = AsyncMock()
            with patch.dict(os.environ, {**SETTINGS, **overrides}):
                with self.assertRaises(VoiceError) as caught:
                    await VoiceBroker(provider).offer(SDP)
                self.assertEqual(caught.exception.code, code)
                provider.stream.assert_not_called()

    async def test_pending_offers_and_start_rate_are_bounded(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def provider(request):
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set()
            await release.wait()
            return httpx.Response(201, text=SDP)

        with patch.dict(os.environ, SETTINGS):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
                broker = VoiceBroker(http)
                first = asyncio.create_task(broker.offer(SDP))
                second = asyncio.create_task(broker.offer(SDP))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    with self.assertRaises(VoiceError) as caught:
                        await broker.offer(SDP)
                    self.assertEqual(caught.exception.code, "voice_busy")
                finally:
                    release.set()
                    await asyncio.gather(first, second)
                self.assertEqual(broker.pending, 0)
                for _ in range(MAX_OFFERS_PER_MINUTE - 2):
                    await broker.offer(SDP)
                with self.assertRaises(VoiceError) as caught:
                    await broker.offer(SDP)
                self.assertEqual(caught.exception.code, "voice_busy")
                self.assertEqual(calls, MAX_OFFERS_PER_MINUTE)


class VoiceHTTPTests(unittest.TestCase):
    def test_core_auth_configuration_and_bounded_offers(self):
        from reign.core import app
        headers = {"Authorization": "Bearer " + "c" * 40}
        with patch.dict(os.environ, {**SETTINGS, "CORE_TOKEN": "c" * 40, "OPENAI_API_KEY": ""}):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/voice").status_code, 401)
                self.assertEqual(client.post("/api/voice/offer", json={"sdp": SDP}).status_code, 401)
                status = client.get("/api/voice", headers=headers)
                self.assertEqual(status.status_code, 200)
                self.assertFalse(status.json()["configured"])
                self.assertEqual(status.headers["cache-control"], "no-store")
                response = client.post("/api/voice/offer", headers=headers, json={"sdp": SDP})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "voice_not_configured")
                for payload in ({"sdp": "not SDP"}, {"sdp": SDP, "model": "injected"},
                                {"sdp": SDP + "\ud800"}, {"sdp": SDP + "x" * MAX_SDP_BYTES}):
                    response = client.post("/api/voice/offer", headers=headers,
                                           content=json.dumps(payload),
                                           )
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn(SDP, response.text)
                response = client.post("/api/voice/offer", headers=headers,
                                       content=iter([b"x" * (MAX_REQUEST_BYTES // 2)] * 3))
                self.assertEqual(response.status_code, 413)

    def test_authenticated_ax_core_voice_round_trip(self):
        from reign.ax import app as ax_app
        from reign.core import app as core_app
        settings = {**SETTINGS, "CORE_TOKEN": "c" * 40, "AX_SESSION_KEY": "s" * 40,
                    "AX_ADMIN_PASSWORD": "p" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com",
                    "CORE_URL": "https://core.example.com"}
        answer = {"sdp": SDP, "type": "answer", "model": DEFAULT_MODEL, "max_duration_seconds": 900}
        with patch.dict(os.environ, settings):
            with TestClient(core_app), TestClient(ax_app, base_url="https://ax.example.com") as ax:
                async def forward(method, url, **kwargs):
                    self.assertNotIn(KEY, str(kwargs))
                    self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + "c" * 40)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core_app)) as inner:
                        return await inner.request(method, url, headers=kwargs["headers"], json=kwargs["json"])

                with patch.object(ax_app.state.http, "request", side_effect=forward), \
                     patch.object(core_app.state.voice, "offer", new_callable=AsyncMock, return_value=answer) as broker:
                    self.assertEqual(ax.post("/login", json={"password": "p" * 40}).status_code, 200)
                    status = ax.get("/api/voice")
                    self.assertTrue(status.json()["configured"])
                    self.assertNotIn(KEY, status.text)
                    response = ax.post("/api/voice/offer", json={"sdp": SDP}, headers={
                        "Origin": "https://ax.example.com", "X-CSRF-Token": ax.cookies.get("ax_csrf")})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), answer)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    broker.assert_awaited_once_with(SDP)


if __name__ == "__main__":
    unittest.main()
