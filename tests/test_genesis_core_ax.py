"""Authenticated Genesis API and AX console regression tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from reign.core import app as core_app
from reign.ax import app as ax_app
from reign.genesis_pilot import GenesisPilot

SETTINGS = {"CORE_TOKEN": "c" * 40, "AX_SESSION_KEY": "s" * 40,
            "AX_ADMIN_PASSWORD": "p" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com"}


class GenesisHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_core_api_requires_token_and_rejects_unsafe_capabilities(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, SETTINGS):
            core_app.state.genesis = GenesisPilot(str(Path(root) / "drafts.sqlite"))
            transport = httpx.ASGITransport(app=core_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://core.test") as client:
                self.assertEqual((await client.get("/api/genesis/status")).status_code, 401)
                headers={"Authorization": "Bearer " + SETTINGS["CORE_TOKEN"]}
                status=await client.get("/api/genesis/status", headers=headers)
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["mode"], "draft_only")
                data={"agent_id": "research", "name": "Research",
                      "capabilities": ["workspace_status"]}
                preview=await client.post("/api/genesis/preview", json=data, headers=headers)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual((await client.get("/api/genesis/agents",headers=headers)).json()["agents"], [])
                created=await client.post("/api/genesis/agents",json=data,headers=headers)
                self.assertEqual(created.status_code,200)
                self.assertEqual(created.json()["state"],"draft")
                self.assertFalse(created.json()["provisioned"])
                listing=await client.get("/api/genesis/agents",headers=headers)
                self.assertEqual(len(listing.json()["agents"]),1)
                denied=await client.post("/api/genesis/agents",headers=headers,
                    json={"agent_id":"bad-agent","name":"Bad","capabilities":["browser_click"]})
                self.assertEqual(denied.status_code,422)
                injected=await client.post("/api/genesis/agents",headers=headers,
                    json={**data,"owner_id":"external-owner"})
                self.assertEqual(injected.status_code,422)
            del core_app.state.genesis


class GenesisAXTests(unittest.TestCase):
    def test_genesis_console_requires_login_and_csrf(self):
        with patch.dict(os.environ, SETTINGS):
            with TestClient(ax_app, base_url="https://ax.example.com") as client:
                unauth = client.get("/genesis", follow_redirects=False)
                self.assertEqual(unauth.status_code, 303)
                self.assertEqual(unauth.headers["location"], "/")
                self.assertEqual(client.get("/genesis").status_code, 200)
                self.assertIn("loginForm", client.get("/genesis").text)
                self.assertEqual(client.get("/api/genesis/status").status_code,401)
                self.assertEqual(client.post("/api/genesis/agents",json={}).status_code,401)
                self.assertEqual(client.post("/login",json={"password":"p"*40}).status_code,200)
                response=client.get("/genesis")
                self.assertEqual(response.status_code,200)
                self.assertIn("Guardrail Manager",response.text)
                self.assertNotIn(SETTINGS["CORE_TOKEN"],response.text)
                self.assertEqual(client.post("/api/genesis/agents",json={}).status_code,403)
                self.assertEqual(client.post("/api/genesis/agents",
                    headers={"Origin":"https://evil.test",
                             "X-CSRF-Token":client.cookies.get("ax_csrf")},
                    json={}).status_code,403)
