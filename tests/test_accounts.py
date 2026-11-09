import base64
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from reign.core import app
from reign.credential_transport import context, open_credentials
from reign.vault import Vault


ORIGIN = "https://alambda-forge.onrender.com"
PASSWORD = "disposable-account-password-do-not-echo"
TOKEN = "core-account-test-only-" + "x" * 32
WORKER_TOKEN = "computer-account-test-only-" + "y" * 32


class AccountBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = Vault(self.directory.name, b"v" * 32)
        self.env = patch.dict(os.environ, {"CORE_TOKEN": TOKEN, "COMPUTER_TOKEN": WORKER_TOKEN,
            "CREDENTIAL_CHANNEL_KEY": base64.b64encode(b"c" * 32).decode(),
            "ALLOWED_HOSTS": "alambda-forge.onrender.com"})
        self.env.start()
        self.store = patch("reign.accounts.get_vault", return_value=self.vault)
        self.store.start()
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://core.test")
        self.headers = {"Authorization": "Bearer " + TOKEN}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.stop()
        self.env.stop()
        self.directory.cleanup()

    async def save(self):
        result = await self.client.post("/api/accounts", headers=self.headers,
            json={"label": "Fixture login", "origin": ORIGIN, "username": "fixture-user", "password": PASSWORD})
        self.assertEqual(result.status_code, 201)
        self.assertNotIn(PASSWORD, result.text)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        return result.json()["account"]

    async def test_private_auth_and_no_secret_getter(self):
        account = await self.save()
        for method, path in (("GET", "/api/accounts"), ("POST", "/api/accounts"),
                             ("DELETE", "/api/accounts/" + account["id"]),
                             ("POST", "/api/accounts/" + account["id"] + "/use")):
            self.assertEqual((await self.client.request(method, path, json={})).status_code, 401)
        self.assertEqual((await self.client.get("/api/accounts/" + account["id"], headers=self.headers)).status_code, 405)
        self.assertEqual((await self.client.post("/api/vault/grants/redeem", headers=self.headers, json={})).status_code, 401)

    async def test_metadata_only_and_changed_duplicate_is_not_an_overwrite(self):
        account = await self.save()
        listed = await self.client.get("/api/accounts", headers=self.headers)
        self.assertEqual(listed.json()["accounts"], [account])
        self.assertNotIn("password", json.dumps(listed.json()["accounts"]).lower())
        result = await self.client.post("/api/accounts", headers=self.headers,
            json={"label": "Fixture login", "origin": ORIGIN, "username": "different", "password": "replacement-secret"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["code"], "account_exists")
        self.assertEqual(self.vault.list_accounts()[0]["username"], "fixture-user")

    async def test_validation_never_echoes_credentials_or_owner_injection(self):
        valid = {"label": "Fixture", "origin": ORIGIN, "username": "user", "password": PASSWORD}
        for body in ({**valid, "owner_id": "someone-else"}, {**valid, "password": {"nested": PASSWORD}},
                     {**valid, "password": PASSWORD * 400}, {**valid, "username": [PASSWORD]}):
            result = await self.client.post("/api/accounts", headers=self.headers, json=body)
            self.assertEqual(result.status_code, 422)
            self.assertEqual(result.json()["code"], "invalid_account")
            self.assertNotIn(PASSWORD, result.text)
        self.assertEqual(self.vault.list_accounts(), [])

    async def test_saved_login_use_joins_existing_graph_without_credentials(self):
        account = await self.save()
        sid, tid = str(uuid.uuid4()), str(uuid.uuid4())
        agent = Mock()
        agent.submit.return_value = {"thread_id": tid, "session_id": sid, "status": "running"}
        with patch.object(app.state, "agent", agent, create=True):
            result = await self.client.post("/api/accounts/" + account["id"] + "/use", headers=self.headers,
                json={"session_id": sid, "thread_id": tid})
        self.assertEqual(result.json()["status"], "running")
        prompt, actual_tid, actual_sid = agent.submit.call_args.args
        self.assertIn(account["id"], prompt)
        self.assertNotIn(PASSWORD, prompt)
        self.assertEqual((actual_tid, actual_sid), (tid, sid))

    async def test_worker_redeems_sealed_single_use_bound_grant(self):
        account = await self.save()
        sid, observation = str(uuid.uuid4()), str(uuid.uuid4())
        grant = self.vault.issue_grant("admin", account["id"], sid, observation, 3, ORIGIN)
        binding = context(grant["grant_id"], sid, observation, 3, ORIGIN)
        headers = {"Authorization": "Bearer " + WORKER_TOKEN}
        wrong = await self.client.post("/api/vault/grants/redeem", headers=headers,
            json={**binding, "epoch": 4})
        self.assertEqual(wrong.status_code, 403)
        result = await self.client.post("/api/vault/grants/redeem", headers=headers, json=binding)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(PASSWORD, result.text)
        self.assertEqual(open_credentials(result.json(), binding), {"username": "fixture-user", "password": PASSWORD})
        used = await self.client.post("/api/vault/grants/redeem", headers=headers, json=binding)
        self.assertEqual(used.status_code, 403)
        self.assertNotIn(PASSWORD, used.text)

    async def test_unconfigured_vault_reports_unavailable_without_saving(self):
        with patch("reign.accounts.get_vault", return_value=Vault(None, None, require_mount=True)):
            result = await self.client.get("/api/accounts", headers=self.headers)
            self.assertEqual(result.json(), {"vault": {"ready": False, "durable": False, "code": "vault_not_ready"}, "accounts": []})
            result = await self.client.post("/api/accounts", headers=self.headers,
                json={"label": "Fixture", "origin": ORIGIN, "username": "user", "password": PASSWORD})
            self.assertEqual(result.status_code, 503)
            self.assertNotIn(PASSWORD, result.text)

    async def test_secret_target_rejected_before_temporal_history(self):
        sid = str(uuid.uuid4())
        worker = AsyncMock()
        worker.request.return_value = Response(403, json={"code": "secret_field", "detail": PASSWORD})
        with patch.object(app.state, "http", worker, create=True), patch("reign.core.Client.connect", new_callable=AsyncMock) as connect:
            result = await self.client.post(f"/api/sessions/{sid}/action", headers=self.headers,
                json={"epoch": 0, "observation_id": str(uuid.uuid4()), "action": "fill", "ref": "e1", "text": PASSWORD})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["code"], "secret_field")
        self.assertNotIn(PASSWORD, result.text)
        self.assertNotIn("text", worker.request.call_args.kwargs["json"])
        connect.assert_not_awaited()
