import os
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
from reign.ax import app

SETTINGS = {"AX_SESSION_KEY": "s" * 40, "AX_ADMIN_PASSWORD": "p" * 40,
            "CORE_TOKEN": "c" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com"}

class AXTests(unittest.TestCase):
    def test_health_and_auth(self):
        with patch.dict(os.environ, SETTINGS):
            with TestClient(app, base_url="https://ax.example.com") as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.get("/").status_code, 200)
                self.assertIn("Take control", client.get("/").text)
                self.assertEqual(client.get("/api/sessions/123").status_code, 401)
                self.assertEqual(client.get("/api/me").status_code, 401)
                self.assertEqual(client.get("/api/status").status_code, 401)
                self.assertEqual(client.get("/api/agent").status_code, 401)
                self.assertEqual(client.get("/api/voice").status_code, 401)
                self.assertEqual(client.post("/api/voice/offer", json={"sdp": "private"}).status_code, 401)
                self.assertEqual(client.post("/api/chat", json={"message": "hi"}).status_code, 401)
                self.assertEqual(client.post("/login", json={"password": "bad"}).status_code, 401)
                self.assertEqual(client.post("/login", json={"password": "p" * 40}).status_code, 200)
                profile = client.get("/api/me")
                self.assertEqual(profile.headers["cache-control"], "no-store")
                self.assertTrue(profile.json()["capabilities"]["voice"])
                self.assertNotIn(SETTINGS["CORE_TOKEN"], profile.text)
                self.assertEqual(client.get("/assets/live.js").status_code, 200)
                self.assertEqual(client.get("/assets/ultraview.css").status_code, 200)
                self.assertEqual(client.get("/api/sessions/not-a-uuid").status_code, 422)
                # Authenticated requests are validated before forwarding to Core.
                self.assertEqual(client.post("/api/chat", json={"message": "hi"}).status_code, 403)
                self.assertEqual(client.post("/api/voice/offer", json={"sdp": "private"}).status_code, 403)
                self.assertEqual(client.post("/api/voice/offer", headers={"Origin": "https://evil.example",
                    "x-csrf-token": client.cookies.get("ax_csrf")}, json={"sdp": "private"}).status_code, 403)
                self.assertEqual(client.get("/api/chat/not-a-uuid").status_code, 422)
                self.assertEqual(client.post("/api/sessions", json={}).status_code, 403)
                self.assertEqual(client.post("/api/sessions", headers={"Origin": "https://evil.example",
                    "x-csrf-token": client.cookies.get("ax_csrf")}, json={}).status_code, 403)
                self.assertEqual(client.post("/logout", headers={"Origin": "https://ax.example.com",
                    "x-csrf-token": client.cookies.get("ax_csrf")}).status_code, 200)

    def test_voice_proxy_preserves_safe_codes_but_never_core_error_bodies(self):
        with patch.dict(os.environ, {**SETTINGS, "CORE_URL": "https://core.example.com"}):
            with TestClient(app, base_url="https://ax.example.com") as client:
                client.post("/login", json={"password": "p" * 40})
                for code, expected in (("voice_provider_auth", "voice_provider_auth"),
                                       ("private-secret", "voice_core_unavailable"),
                                       ([], "voice_core_unavailable")):
                    with patch.object(app.state.http, "request", new_callable=AsyncMock) as proxy:
                        proxy.return_value = httpx.Response(503, json={"detail": "raw-private-secret", "code": code})
                        response = client.get("/api/voice")
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.json()["code"], expected)
                        self.assertNotIn("private-secret", response.text)

if __name__ == "__main__":
    unittest.main()
