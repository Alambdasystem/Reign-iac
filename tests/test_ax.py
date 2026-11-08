import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from reign.ax import app

SETTINGS = {"AX_SESSION_KEY": "s" * 40, "AX_ADMIN_PASSWORD": "p" * 40,
            "CORE_TOKEN": "c" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com"}

class AXTests(unittest.TestCase):
    def test_health_and_auth(self):
        with patch.dict(os.environ, SETTINGS):
            with TestClient(app) as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.get("/api/sessions/123").status_code, 401)
                self.assertEqual(client.post("/login", json={"password": "bad"}).status_code, 401)
                self.assertEqual(client.post("/login", json={"password": "p" * 40}).status_code, 200)
                self.assertEqual(client.post("/api/sessions", json={}).status_code, 403)
                self.assertEqual(client.post("/api/sessions", headers={"Origin": "https://evil.example",
                    "x-csrf-token": client.cookies.get("ax_csrf")}, json={}).status_code, 403)
                self.assertEqual(client.post("/logout", headers={"Origin": "https://ax.example.com",
                    "x-csrf-token": client.cookies.get("ax_csrf")}).status_code, 200)

if __name__ == "__main__":
    unittest.main()
