"""Security posture endpoint/console are authenticated and never misstate enforcement."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from reign.ax import app as ax_app
from reign.core import app as core_app


SETTINGS = {"CORE_TOKEN":"c"*40, "AX_SESSION_KEY":"s"*40,
            "AX_ADMIN_PASSWORD":"p"*40, "AX_PUBLIC_ORIGIN":"https://ax.example.com"}


class CoreStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_private_and_does_not_claim_protection(self):
        core_app.state.agent = SimpleNamespace(archive=object())
        try:
            with patch.dict(os.environ, SETTINGS):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=core_app),base_url="http://core.test"
                ) as client:
                    self.assertEqual((await client.get("/api/security/status")).status_code, 401)
                    data=(await client.get("/api/security/status",headers={
                        "Authorization":"Bearer "+SETTINGS["CORE_TOKEN"]
                    })).json()
                    self.assertEqual(data["policy_enforcement"],"not_active")
                    self.assertEqual(data["phi_pii_egress_protection"],"not_active")
                    self.assertEqual(data["conversation_storage"],"encrypted_finished_turn_archive")
                    self.assertEqual(data["langgraph_checkpoints"],"process_local")
                    self.assertEqual(data["identity"],"single_admin")
                    self.assertNotIn("token",str(data).lower())
        finally:
            del core_app.state.agent

    async def test_status_never_infers_encryption_from_env_only(self):
        core_app.state.agent = SimpleNamespace(archive=None)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=core_app),base_url="http://core.test"
            ) as client, patch.dict(os.environ, SETTINGS):
                data=(await client.get("/api/security/status",headers={
                    "Authorization":"Bearer "+SETTINGS["CORE_TOKEN"]
                })).json()
                self.assertEqual(data["conversation_storage"],"no_verified_archive")
        finally:
            del core_app.state.agent


class AXStatusTests(unittest.TestCase):
    def test_auth_gate_status_and_navigation(self):
        with patch.dict(os.environ,SETTINGS):
            with TestClient(ax_app,base_url="https://ax.example.com") as client:
                result=client.get("/guardrails",follow_redirects=False)
                self.assertEqual(result.status_code,303)
                self.assertEqual(result.headers["location"],"/")
                self.assertEqual(client.get("/api/security/status").status_code,401)
                self.assertEqual(client.post("/login",json={"password":"p"*40}).status_code,200)
                page=client.get("/guardrails")
                self.assertEqual(page.status_code,200)
                self.assertIn("Guardrails & Privacy",page.text)
                self.assertIn("not operational yet",page.text)
                self.assertNotIn(SETTINGS["CORE_TOKEN"],page.text)
                self.assertNotIn(SETTINGS["AX_SESSION_KEY"],page.text)
