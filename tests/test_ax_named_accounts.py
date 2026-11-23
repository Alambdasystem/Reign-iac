"""Authorization boundaries for named Avatar Lab accounts and revocable sessions."""

import asyncio
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from reign import ax, ax_auth
from reign.openstl import Grant


ORIGIN = "https://ax.example.com"
PASSWORD = "synthetic-avatar-password-for-tests"
OWNER_PASSWORD = "synthetic-owner-password-for-tests-only-1234"
SETTINGS = {"AX_SESSION_KEY": "synthetic-session-signing-key-for-tests-1234",
            "AX_ADMIN_PASSWORD": OWNER_PASSWORD, "AX_PUBLIC_ORIGIN": ORIGIN,
            "CORE_TOKEN": "synthetic-core-token-for-tests-only-1234",
            "CORE_URL": "https://core.example.com"}
ROOT = Path(__file__).resolve().parents[1]


def account_config(verifier, **extra):
    return json.dumps({"mayur": {"display_name": "Mayur", "password_hash": verifier, **extra}})


class NamedAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = ax_auth.hash_password(PASSWORD)
        cls.rotated_verifier = ax_auth.hash_password("different-synthetic-avatar-password")

    def setUp(self):
        self.env = patch.dict(os.environ, {**SETTINGS, "AX_AVATAR_USERS_JSON": account_config(self.verifier)})
        self.env.start()
        self.addCleanup(self.env.stop)
        ax.openstl.pending.clear()
        ax.openstl.grants.clear()
        ax.openstl.starts.clear()
        self.client = TestClient(ax.app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(ax.openstl.pending.clear)
        self.addCleanup(ax.openstl.grants.clear)

    def login(self, username="mayur", password=PASSWORD):
        response = self.client.post("/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def cookies(self):
        return self.client.cookies.get("ax_session"), self.client.cookies.get("ax_csrf")

    def restore(self, cookies):
        self.client.cookies.clear()
        for key, value in zip(("ax_session", "ax_csrf"), cookies):
            self.client.cookies.set(key, value, domain="ax.example.com", path="/")

    def csrf(self):
        return {"Origin": ORIGIN, "X-CSRF-Token": self.client.cookies.get("ax_csrf", "")}

    def test_named_profile_landing_and_existing_owner_aliases(self):
        for username in (None, "", "owner", "ADMIN"):
            with self.subTest(username=username):
                body = {"password": OWNER_PASSWORD}
                if username is not None:
                    body["username"] = username
                response = self.client.post("/login", json=body)
                self.assertEqual(response.status_code, 200)
                profile = self.client.get("/api/me").json()
                self.assertEqual(profile["username"], "admin")
                self.assertEqual(profile["role"], "owner")
                self.assertEqual(profile["landing_path"], "/")
                self.assertTrue(profile["capabilities"]["ai_chat"])
                self.assertTrue(profile["capabilities"]["voice"])
                self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 200)
        response = self.login(" MaYuR ")
        profile = self.client.get("/api/me")
        self.assertEqual(profile.headers["cache-control"], "no-store")
        expected = {"authenticated": True, "username": "mayur", "display_name": "Mayur",
                    "role": "avatar", "landing_path": "/avatar-lab",
                    "capabilities": {"avatar_lab": True, "managed_browser": False,
                                     "external_computer": False, "voice": False, "ai_chat": False}}
        self.assertEqual(profile.json(), expected)
        self.assertEqual(response.json(), {"status": "authenticated", **expected})
        redirect = self.client.get("/", follow_redirects=False)
        self.assertEqual((redirect.status_code, redirect.headers["location"]), (303, "/avatar-lab"))
        self.assertEqual(self.client.get("/avatar-lab").status_code, 200)
        session, csrf = self.cookies()
        payload = ax.signer().loads(session)
        self.assertEqual(set(payload), {"v", "boot", "nonce", "username", "role"})
        self.assertEqual(payload["v"], 2)
        self.assertNotIn(self.verifier, response.text + str(payload))
        self.assertNotIn(PASSWORD, response.text + str(payload))
        headers = response.headers.get_list("set-cookie")
        self.assertTrue(all("Secure" in value and "SameSite=strict" in value for value in headers))
        self.assertTrue(any(value.startswith("ax_session=") and "HttpOnly" in value for value in headers))
        self.assertTrue(any(value.startswith("ax_csrf=") and "HttpOnly" not in value for value in headers))
        self.assertRegex(csrf, r"^[A-Za-z0-9_-]{43}$")

    def test_every_api_route_and_unknown_api_deny_avatar_before_forwarding(self):
        self.login()
        checks = set()
        for route in ax.app.routes:
            if getattr(route, "path", "").startswith("/api/") and route.path != "/api/me":
                path = re.sub(r"\{[^}]+\}", "123e4567-e89b-42d3-a456-426614174000", route.path)
                checks.update((method, path) for method in route.methods)
        checks.update({("POST", "/api/future-owner-route"), ("GET", "/api"),
                       ("OPTIONS", "/api/openstl/connect"), ("GET", "/api/me/")})
        self.assertGreater(len(checks), 45)
        with patch.object(ax, "core", new_callable=AsyncMock) as core, \
                patch.object(ax.openstl, "core", new_callable=AsyncMock) as bridge, \
                patch.object(ax.app.state.http, "request", new_callable=AsyncMock) as transport:
            for method, path in sorted(checks):
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, json={"password": "private-test-value"},
                        headers={**self.csrf(), "Authorization": "Bearer " + SETTINGS["CORE_TOKEN"],
                                 "X-Node-Token": "synthetic-node-token"}, follow_redirects=False)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertNotIn("private-test-value", response.text)
            core.assert_not_awaited()
            bridge.assert_not_awaited()
            transport.assert_not_awaited()
        for path in ("/genesis", "/guardrails", "/nodes", "/connect/openstl"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 403)

    def test_linkedin_publish_requires_the_same_owner_sessions_exact_preview(self):
        preview_path = "/api/linkedin/preview"
        publish_path = "/api/linkedin/publish"
        author = "urn:li:person:test-author"
        self.login("owner", OWNER_PASSWORD)
        owner_cookies = self.cookies()

        async def fake_core(method, path, data):
            if path == "/internal/linkedin/preview":
                return httpx.Response(200, json={"text": data["text"].strip(), "author": author,
                                                 "status": "preview", "ready": True})
            self.assertEqual(path, "/internal/linkedin/publish")
            self.assertEqual(data, {"text": "Draft for review", "author": author})
            return httpx.Response(200, json={"status": "published", "post_id": "urn:li:share:42"})

        with patch.object(ax, "core", side_effect=fake_core) as relay:
            draft = {"text": " Draft for review "}
            self.assertEqual(self.client.post(preview_path, json=draft).status_code, 403)
            response = self.client.post(preview_path, headers=self.csrf(), json=draft)
            self.assertEqual(response.status_code, 200, response.text)
            receipt = {"receipt": response.json()["receipt"]}
            self.assertEqual(response.json()["text"], "Draft for review")
            self.assertEqual(self.client.post(publish_path, headers=self.csrf(),
                                              json={**receipt, "text": "Different"}).status_code, 409)

            self.login()
            self.assertEqual(self.client.post(publish_path, headers=self.csrf(), json=receipt).status_code, 403)
            self.login("owner", OWNER_PASSWORD)
            self.assertEqual(self.client.post(publish_path, headers=self.csrf(), json=receipt).status_code, 409)

            self.restore(owner_cookies)
            response = self.client.post(publish_path, headers=self.csrf(), json=receipt)
            self.assertEqual(response.json()["post_id"], "urn:li:share:42")
            self.assertEqual(self.client.post(publish_path, headers=self.csrf(), json=receipt).status_code, 409)
            self.assertEqual(relay.await_count, 2)

    def test_owner_api_access_still_requires_csrf(self):
        self.login("owner", OWNER_PASSWORD)
        with patch.object(ax, "core", new_callable=AsyncMock) as core:
            core.return_value = httpx.Response(200, json={"ready": True})
            self.assertEqual(self.client.get("/api/status").status_code, 200)
            self.assertEqual(self.client.post("/api/sessions", json={}).status_code, 403)
            self.assertEqual(self.client.post("/api/sessions", headers=self.csrf(), json={}).status_code, 200)
            self.assertEqual(core.await_count, 2)

    def test_wrong_credentials_and_bad_bodies_never_echo_secrets(self):
        cases = [{"username": "mayur", "password": "wrong-synthetic-password"},
                 {"username": "unknown", "password": PASSWORD},
                 {"username": "mayur", "password": {"secret": PASSWORD}},
                 {"username": [PASSWORD], "password": PASSWORD},
                 {"username": "mayur", "password": PASSWORD, "role": "owner"},
                 {"password": PASSWORD}, {"password": "\ud800"},
                 {"password": "x" * 1025}, {"password": "has\x00nul"}, [], None]
        for case in cases:
            with self.subTest(case_type=type(case).__name__):
                response = self.client.post("/login", content=json.dumps(case))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "Invalid credentials"})
                self.assertNotIn(PASSWORD, response.text)
                self.assertEqual(ax.app.state.auth.pending_logins, 0)
        oversized = self.client.post("/login", content=b"x" * 4097)
        self.assertEqual(oversized.status_code, 401)
        wrong_origin = self.client.post("/login", headers={"Origin": "https://evil.example"},
                                        json={"username": "mayur", "password": PASSWORD})
        self.assertEqual(wrong_origin.status_code, 403)

    def test_csrf_is_bound_to_each_signed_session_and_role(self):
        self.login()
        first = self.cookies()
        self.login()
        second = self.cookies()
        self.login("owner", OWNER_PASSWORD)
        owner = self.cookies()
        for selected, other in ((first, second), (first, owner), (owner, first)):
            with self.subTest(owner_cookie=selected == owner):
                self.restore((selected[0], other[1]))
                response = self.client.post("/logout", headers=self.csrf())
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.restore(first)
        self.assertEqual(self.client.post("/logout", headers=self.csrf()).status_code, 200)
        for active in (second, owner):
            self.restore(active)
            self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_logout_requires_origin_and_live_csrf_then_blocks_cookie_replay(self):
        self.login()
        saved = self.cookies()
        for headers in ({}, {"Origin": ORIGIN}, {**self.csrf(), "Origin": "https://evil.example"}):
            self.assertEqual(self.client.post("/logout", headers=headers).status_code, 403)
            self.assertEqual(self.client.get("/api/me").status_code, 200)
        response = self.client.post("/logout", headers=self.csrf())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "logged_out", "authenticated": False})
        self.assertTrue(all("Max-Age=0" in value for value in response.headers.get_list("set-cookie")))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.restore(saved)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.post("/logout", headers={"Origin": ORIGIN}).status_code, 200)
        self.assertEqual(self.client.post("/logout", headers={"Origin": ORIGIN}).status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 403)

    def test_expiry_restart_legacy_cookies_and_forged_role_cannot_authorize(self):
        self.login()
        saved = self.cookies()
        payload = ax.signer().loads(saved[0])
        session = ax.app.state.auth.sessions[payload["nonce"]]
        ax.app.state.auth.sessions[payload["nonce"]] = replace(session, expires=time.monotonic() - 1)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.post("/logout", headers={"Origin": ORIGIN}).status_code, 200)
        self.login()
        saved = self.cookies()
        payload = ax.signer().loads(saved[0])
        altered = {**payload, "role": "owner", "username": "admin"}
        self.restore((ax.signer().dumps(altered), saved[1]))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.restore(saved)
        ax.app.state.auth = ax_auth.AuthState()
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.restore((ax.signer().dumps("admin"), saved[1]))
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.login("owner", OWNER_PASSWORD)

    def test_removal_and_password_rotation_revoke_named_sessions(self):
        self.login()
        saved = self.cookies()
        for config in ("{}", account_config(self.rotated_verifier)):
            with patch.dict(os.environ, {"AX_AVATAR_USERS_JSON": config}):
                self.restore(saved)
                self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_verifier_rotation_during_kdf_cannot_authorize_the_old_password(self):
        original = ax_auth.hashlib.pbkdf2_hmac
        def rotate(*args, **kwargs):
            result = original(*args, **kwargs)
            os.environ["AX_AVATAR_USERS_JSON"] = account_config(self.rotated_verifier)
            return result
        with patch.object(ax_auth.hashlib, "pbkdf2_hmac", side_effect=rotate):
            response = self.client.post("/login", json={"username": "mayur", "password": PASSWORD})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(ax.app.state.auth.sessions)

    def test_avatar_logout_does_not_revoke_owner_openstl_grants(self):
        self.login()
        grant = Grant("https://open-stl-3a6l.onrender.com", time.monotonic() + 60)
        pending = {"expires": time.monotonic() + 60, "approved": False}
        ax.openstl.grants["owner-grant"] = grant
        ax.openstl.pending["owner-pair"] = pending
        with patch.object(ax.openstl, "reap", new_callable=AsyncMock) as reap:
            self.assertEqual(self.client.post("/logout", headers=self.csrf()).status_code, 200)
            reap.assert_not_awaited()
        self.assertFalse(grant.revoked)
        self.assertEqual(ax.openstl.pending["owner-pair"], pending)
        self.assertIs(ax.openstl.grants["owner-grant"], grant)

    def test_owner_logout_survives_failed_or_slow_cleanup(self):
        async def slow():
            await asyncio.Event().wait()
        for behavior in (RuntimeError("synthetic-cleanup-private-error"), slow):
            with self.subTest(behavior_type=type(behavior).__name__):
                self.login("owner", OWNER_PASSWORD)
                saved = self.cookies()
                grant = Grant("https://open-stl-3a6l.onrender.com", time.monotonic() + 60)
                ax.openstl.grants["owner-grant"] = grant
                ax.openstl.pending["owner-pair"] = {"expires": time.monotonic() + 60}
                with patch.object(ax.openstl, "reap", new_callable=AsyncMock, side_effect=behavior), \
                        patch.object(ax, "LOGOUT_CLEANUP_TIMEOUT", 0.02):
                    response = self.client.post("/logout", headers=self.csrf())
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("private-error", response.text)
                self.assertTrue(grant.revoked)
                self.assertFalse(ax.openstl.pending)
                self.assertIsNone(self.client.cookies.get("ax_session"))
                self.restore(saved)
                self.assertEqual(self.client.get("/api/me").status_code, 401)
                ax.openstl.grants.clear()

    def test_configuration_rejects_role_escalation_and_unsafe_verifiers(self):
        invalid = [account_config(self.verifier, role="owner"),
                   account_config(self.verifier.replace("600000", "600000000")),
                   account_config("plaintext-synthetic-secret"), "[]", "not-json",
                   json.dumps({"admin": {"display_name": "Admin", "password_hash": self.verifier}})]
        for config in invalid:
            with self.subTest(config_length=len(config)), patch.dict(os.environ, {"AX_AVATAR_USERS_JSON": config}):
                with patch.object(ax_auth.hashlib, "pbkdf2_hmac") as kdf:
                    response = self.client.post("/login", json={"username": "mayur", "password": PASSWORD})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {"detail": "Authentication unavailable"})
                    kdf.assert_not_called()
                # A broken guest configuration does not lock out the owner.
                self.login("owner", OWNER_PASSWORD)

    def test_login_rate_limits_are_bounded_before_password_work(self):
        with patch.object(ax_auth, "LOGIN_PER_CLIENT", 2), \
                patch.object(ax_auth, "authenticate", side_effect=ax_auth.InvalidCredentials) as authenticate:
            for _ in range(2):
                self.assertEqual(self.client.post("/login", json={"password": "bad"}).status_code, 401)
            response = self.client.post("/login", json={"password": "bad"})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "60")
            self.assertEqual(authenticate.call_count, 2)
            self.assertEqual(ax.app.state.auth.pending_logins, 0)


class LoginConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, SETTINGS)
        self.env.start()
        self.addCleanup(self.env.stop)
        ax.app.state.auth = ax_auth.AuthState()

    def request(self, gate=None, entered=None):
        async def receive():
            if entered:
                entered.set()
            if gate:
                await gate.wait()
            return {"type": "http.request", "body": json.dumps({"password": OWNER_PASSWORD}).encode(),
                    "more_body": False}
        return Request({"type": "http", "app": ax.app, "headers": [],
                        "client": ("synthetic-client", 1234)}, receive)

    async def test_slow_bodies_reserve_slots_before_a_third_login(self):
        gate = asyncio.Event()
        entered = [asyncio.Event(), asyncio.Event()]
        tasks = [asyncio.create_task(ax.login(self.request(gate, event))) for event in entered]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 2)
            self.assertEqual(ax.app.state.auth.pending_logins, 2)
            with self.assertRaises(HTTPException) as error:
                await ax.login(self.request())
            self.assertEqual(error.exception.status_code, 429)
        finally:
            gate.set()
            results = await asyncio.gather(*tasks)
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(ax.app.state.auth.pending_logins, 0)

    async def test_stalled_body_times_out_and_releases_slot(self):
        with patch.object(ax, "LOGIN_BODY_TIMEOUT", 0.02):
            with self.assertRaises(HTTPException) as error:
                await ax.login(self.request(asyncio.Event()))
        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(ax.app.state.auth.pending_logins, 0)

    async def test_cancelled_request_holds_slot_until_native_password_work_finishes(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        def password_work(username, password):
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=3)
            return ax_auth.identity_for("admin", "owner")
        with patch.object(ax_auth, "authenticate", side_effect=password_work):
            task = asyncio.create_task(ax.login(self.request()))
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(ax.app.state.auth.pending_logins, 1)
            finally:
                release.set()
                async with asyncio.timeout(2):
                    while ax.app.state.auth.pending_logins:
                        await asyncio.sleep(0.01)
        self.assertFalse(ax.app.state.auth.sessions)


class PasswordProvisioningTests(unittest.TestCase):
    def test_salted_verifiers_and_private_cli_output(self):
        first, second = ax_auth.hash_password(PASSWORD), ax_auth.hash_password(PASSWORD)
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^pbkdf2_sha256\$600000\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}$")
        with patch.dict(os.environ, {"AX_AVATAR_USERS_JSON": account_config(first)}):
            self.assertEqual(ax_auth.authenticate("mayur", PASSWORD).role, "avatar")
        with tempfile.TemporaryDirectory(prefix="ax-auth-fixture-") as temp:
            output = Path(temp) / "verifier"
            command = [sys.executable, str(ROOT / "scripts/hash_ax_password.py"), "--stdin", "--output", str(output)]
            result = subprocess.run(command, input=PASSWORD + "\n", text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
            self.assertNotIn("pbkdf2_sha256", result.stdout + result.stderr)
            original = output.read_text()
            again = subprocess.run(command, input=PASSWORD, text=True, capture_output=True, check=False)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(output.read_text(), original)
            for bad in ("x" * 1027, "has\x00null" + "x" * 20):
                invalid = subprocess.run(command[:-1] + [str(Path(temp) / "invalid")],
                                         input=bad, text=True, capture_output=True, check=False)
                self.assertNotEqual(invalid.returncode, 0)
                self.assertNotIn("Traceback", invalid.stderr)
                self.assertFalse((Path(temp) / "invalid").exists())
        denied = subprocess.run([sys.executable, str(ROOT / "scripts/hash_ax_password.py"), "--stdin",
                                 "--output", str(ROOT / "test-must-not-create-verifier")],
                                input=PASSWORD, text=True, capture_output=True, check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertFalse((ROOT / "test-must-not-create-verifier").exists())


if __name__ == "__main__":
    unittest.main()
