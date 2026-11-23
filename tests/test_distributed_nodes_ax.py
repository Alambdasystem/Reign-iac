"""AX node console authentication and no secret exposure."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from reign.ax import app

SETTINGS = {
    "CORE_TOKEN": "c" * 40,
    "AX_SESSION_KEY": "s" * 40,
    "AX_ADMIN_PASSWORD": "p" * 40,
    "AX_PUBLIC_ORIGIN": "https://ax.example.com",
}


class NodeAXTests(unittest.TestCase):
    def test_node_manager_page_login_and_csrf(self):
        with patch.dict(os.environ, SETTINGS):
            with TestClient(app, base_url="https://ax.example.com") as client:
                result = client.get("/nodes", follow_redirects=False)
                self.assertEqual(result.status_code, 303)
                self.assertEqual(result.headers["location"], "/")
                self.assertEqual(client.get("/api/nodes").status_code, 401)
                self.assertEqual(client.post("/api/nodes/enroll", json={}).status_code, 401)
                self.assertEqual(client.post("/login", json={"password": "p" * 40}).status_code, 200)
                page = client.get("/nodes")
                self.assertEqual(page.status_code, 200)
                self.assertIn("Distributed Node Manager", page.text)
                self.assertIn("dispatch disabled", page.text.lower())
                self.assertNotIn(SETTINGS["CORE_TOKEN"], page.text)
                self.assertEqual(client.post("/api/nodes/enroll", json={}).status_code, 403)
                self.assertEqual(client.post("/api/nodes/enroll", json={},
                    headers={"Origin": "https://evil.example",
                             "X-CSRF-Token": client.cookies.get("ax_csrf")}).status_code, 403)
