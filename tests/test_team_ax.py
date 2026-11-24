"""Exercise real AX sessions against a disposable private team directory."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx

from reign import ax, ax_auth
from reign.team_api import TeamRuntime, install
from reign.team_identity import ACTOR_HEADER, OWNER_ID, verify_actor
from reign.team_store import TeamStore, owner_identity

ORIGIN = "https://ax.example.com"
TOKEN = "synthetic-core-token-for-team-boundary-tests-123456"
OWNER_PASSWORD = "synthetic-owner-password-team-boundary-tests-123456"
AVATAR_PASSWORD = "synthetic-avatar-password-team-boundary-tests"


class TeamAXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.avatar_hash = ax_auth.hash_password(AVATAR_PASSWORD)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-team-ax-"); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "AX_TEAM_ENABLED": "1", "AX_SESSION_KEY": "synthetic-session-key-for-team-boundary-tests-123456",
            "AX_ADMIN_PASSWORD": OWNER_PASSWORD, "AX_PUBLIC_ORIGIN": ORIGIN,
            "CORE_TOKEN": TOKEN, "CORE_URL": "https://core.example.com",
            "AX_AVATAR_USERS_JSON": json.dumps({"mayur": {"display_name": "Mayur", "password_hash": self.avatar_hash}}),
        }); self.env.start(); self.addCleanup(self.env.stop)
        self.store = TeamStore(self.temp.name, bytes(range(32))); self.store.initialize()
        self.owner = owner_identity()
        created = self.store.create_member(self.owner, "fixture-employee", "Fixture Employee")
        self.member, self.password = created["member"], created["temporary_password"]
        self.runtime = TeamRuntime(self.store)
        self.core_app = FastAPI(); self.core_app.state.team = self.runtime
        def service_auth(request):
            if request.headers.get("authorization") != "Bearer " + TOKEN: raise HTTPException(401, "Unauthorized")
        install(self.core_app, service_auth)
        ax.openstl.pending.clear(); ax.openstl.grants.clear(); ax.openstl.starts.clear()
        self.client = TestClient(ax.app, base_url=ORIGIN); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.forwarded = []
        self.upstream = None
        async def forward(method, url, **kwargs):
            self.forwarded.append((method, url, kwargs))
            if self.upstream is not None: return self.upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.core_app), base_url="https://core.example.com") as client:
                return await client.request(method, url, **kwargs)
        self.transport = patch.object(ax.app.state.http, "request", new=forward)
        self.transport.start(); self.addCleanup(self.transport.stop)

    def login(self, username="fixture-employee", password=None):
        result = self.client.post("/login", json={"username": username, "password": self.password if password is None else password})
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def csrf(self):
        return {"Origin": ORIGIN, "X-CSRF-Token": self.client.cookies.get("ax_csrf", "")}

    def cookies(self):
        return self.client.cookies.get("ax_session"), self.client.cookies.get("ax_csrf")

    def restore(self, value):
        self.client.cookies.clear()
        for name, cookie in zip(("ax_session", "ax_csrf"), value):
            self.client.cookies.set(name, cookie, domain="ax.example.com", path="/")

    def test_provider_catalog_requires_workspace_identity_and_exposes_no_tools(self):
        path = "/api/connections/catalog"
        self.assertEqual(self.client.get(path).status_code, 401)

        self.login("mayur", AVATAR_PASSWORD)
        self.assertEqual(self.client.get(path).status_code, 403)

        self.login("owner", OWNER_PASSWORD)
        owner = self.client.get(path)
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.headers["cache-control"], "no-store")
        providers = owner.json()["providers"]
        self.assertEqual([entry["id"] for entry in providers], ["gmail", "outlook", "aws"])
        for entry in providers:
            self.assertEqual(set(entry), {"id", "name", "description", "availability", "tools"})
            self.assertEqual(entry["availability"], "unavailable")
            self.assertEqual(entry["tools"], [])
        self.assertEqual(self.client.post(path, headers=self.csrf()).status_code, 405)

        self.login()
        self.forwarded.clear()
        employee = self.client.get(path)
        self.assertEqual(employee.json(), owner.json())
        self.assertEqual([urlsplit(url).path for _, url, _ in self.forwarded],
                         ["/internal/team/identity"])
        self.assertEqual(self.client.get("/api/accounts").status_code, 403)
        self.assertEqual(self.client.post(path, headers=self.csrf()).status_code, 403)

        self.store.disable_member(self.owner, self.member["id"])
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_only_the_initiating_owner_session_can_complete_unread_mail_consent(self):
        from urllib.parse import parse_qs
        from reign.mail_routes import install as install_mail_routes

        def service_auth(request: Request):
            if request.headers.get("authorization") != "Bearer " + TOKEN:
                raise HTTPException(401, "Unauthorized")

        install_mail_routes(self.core_app, service_auth)
        mail = AsyncMock()
        mail.begin.side_effect = lambda provider, state, verifier: {
            "url": "https://oauth.example.test/consent?state=" + state, "revision": 3,
        }
        mail.connect.return_value = {"status": "connected", "email": "owner@example.test"}
        self.core_app.state.mail = mail
        self.core_app.state.mail_mcp = AsyncMock()
        self.core_app.state.mail_mcp.invoke.return_value = {"sampled_unread": 0, "messages": []}
        path = "/api/connections/mail"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        self.assertEqual(self.client.post(path + "/gmail/begin", headers=self.csrf()).status_code, 403)
        config = {"client_id": "fixture-oauth-client", "client_secret": "fixture-secret",
                  "mailbox": "owner@example.test", "tenant": ""}
        self.assertEqual(self.client.put(path + "/gmail/settings", json=config,
                                        headers=self.csrf()).status_code, 403)
        self.login("owner", OWNER_PASSWORD)
        first = self.cookies()
        self.assertEqual(self.client.post(path + "/gmail/begin").status_code, 403)
        mail.configure.return_value = {"status": "available", "redirect": ORIGIN + "/connections/mail/gmail/callback"}
        dest = path + "/gmail/settings"
        self.assertEqual(self.client.put(dest, json=config).status_code, 403)
        self.assertEqual(self.client.put(dest, json={**config, "origin": "https://other.test"},
                                        headers=self.csrf()).status_code, 422)
        saved = self.client.put(dest, json=config, headers=self.csrf())
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(config["client_secret"], saved.text)
        mail.configure.assert_awaited_once_with("gmail", {**config, "origin": ORIGIN})
        for provider in ("gmail", "outlook"):
            start = self.client.post(f"{path}/{provider}/begin", headers=self.csrf())
            self.assertEqual(start.status_code, 200, start.text)
            state = parse_qs(urlsplit(start.json()["url"]).query)["state"][0]
            callback = f"/connections/mail/{provider}/callback"
            params = {"state": state, "code": "disposable-code"}
            if provider == "gmail":
                result = self.client.get(callback, params=params, follow_redirects=False)
            else:
                result = self.client.post(callback, data=params, follow_redirects=False)
            self.assertEqual(result.headers["location"], "/#reign-mail=" + state)
            self.assertEqual(result.headers["referrer-policy"], "no-referrer")
            self.login("owner", OWNER_PASSWORD)
            self.assertEqual(self.client.post(path + "/complete", data={"state": state},
                                              headers=self.csrf()).status_code, 409)
            self.restore(first)
            completed = self.client.post(path + "/complete", data={"state": state}, headers=self.csrf())
            self.assertEqual(completed.json()["status"], "connected")
            self.assertEqual(self.client.post(path + "/complete", data={"state": state},
                                              headers=self.csrf()).status_code, 409)
            sample = self.client.post(f"{path}/{provider}/preview", headers=self.csrf())
            self.assertEqual(sample.json()["sampled_unread"], 0)
            self.core_app.state.mail_mcp.invoke.assert_awaited_with("reign_mail_unread",
                {"provider": provider, "limit": 5}, mailbox_owner=True)
        self.assertEqual(mail.connect.await_count, 2)

    def test_skills_use_member_identity_csrf_and_conflict_checked_encrypted_storage(self):
        path = "/api/team/skills"
        payload = {"revision": 0, "pinned": ["action_checklist"],
                   "items": [{"label": "Review the private briefing", "done": False}]}
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login("mayur", AVATAR_PASSWORD)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.login()
        self.assertEqual(self.client.put(path, json=payload).status_code, 403)
        response = self.client.put(path, headers=self.csrf(), json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.put(path, headers=self.csrf(), json=payload).status_code, 409)
        self.assertEqual(self.client.get(path).json()["items"], payload["items"])
        self.assertNotIn(b"Review the private briefing",
                         (Path(self.store.directory) / "reign-team.sqlite3").read_bytes())
        self.login("owner", OWNER_PASSWORD)
        self.assertEqual(self.client.get(path).json()["items"], [])
        self.assertEqual(self.client.put(path, headers=self.csrf(),
                         json={**payload, "user_id": self.member["id"]}).status_code, 422)
        self.store.disable_member(self.owner, self.member["id"])
        self.assertEqual(len(self.client.get(path).json()["skills"]), 3)

    def test_employee_login_profile_and_protected_page_keep_local_owner_and_avatar_accounts(self):
        self.login(" FIXTURE-EMPLOYEE ")
        profile = self.client.get("/api/me").json()
        self.assertEqual((profile["username"], profile["role"], profile["landing_path"]), ("fixture-employee", "employee", "/team"))
        self.assertFalse(profile["capabilities"]["managed_browser"])
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/team")
        page = self.client.get("/team")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        for path in ("/assets/team.html", "/assets/%74eam.html"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/avatar-lab").status_code, 200)
        before = len(self.forwarded)
        self.login("owner", OWNER_PASSWORD)
        self.assertEqual(self.client.get("/api/me").json()["role"], "owner")
        self.login("mayur", AVATAR_PASSWORD)
        self.assertEqual(self.client.get("/api/me").json()["role"], "avatar")
        self.assertEqual(len(self.forwarded), before, "local accounts must never try the employee directory")
        self.assertEqual(self.client.get("/team").status_code, 403)
        self.assertEqual(self.client.get("/api/team").status_code, 403)

    def test_owner_can_grant_named_console_workspaces_without_vault_or_admin_access(self):
        self.login("owner", OWNER_PASSWORD)
        target = self.member["id"]
        result = self.client.patch(f"/api/team/members/{target}/console-access", headers=self.csrf(), json={"enabled": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["member"]["console_access"])
        self.login()
        profile = self.client.get("/api/me").json()
        self.assertEqual(profile["landing_path"], "/")
        self.assertTrue(profile["capabilities"]["ai_chat"])
        self.assertFalse(profile["capabilities"]["managed_browser"])
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 200)
        self.assertEqual(self.client.get("/genesis").status_code, 200)
        self.assertEqual(self.client.get("/guardrails").status_code, 200)
        self.assertEqual(self.client.get("/nodes").status_code, 200)
        for forbidden in ("/api/accounts", "/api/sessions/active", "/api/nodes/enroll", "/api/unknown-owner-route"):
            self.assertEqual(self.client.get(forbidden, follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/team/members/{target}/console-access",
                         headers=self.csrf(), json={"enabled": False}).status_code, 403)
        self.login("owner", OWNER_PASSWORD)
        result = self.client.patch(f"/api/team/members/{target}/console-access", headers=self.csrf(), json={"enabled": False})
        self.assertEqual(result.status_code, 200)
        self.login()
        self.assertEqual(self.client.get("/genesis", follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/team")

    def test_employee_denied_every_legacy_api_before_any_core_forwarding(self):
        self.login(); before = len(self.forwarded)
        with patch.object(ax, "core", new_callable=AsyncMock) as legacy, patch.object(ax.openstl, "core", new_callable=AsyncMock) as external:
            for route in ax.app.routes:
                path = getattr(route, "path", "")
                if (not path.startswith("/api/") or path.startswith("/api/team/")
                        or path in {"/api/me", "/api/team", "/api/connections/catalog"}):
                    continue
                path = re.sub(r"\{[^}]+\}", "123e4567-e89b-42d3-a456-426614174000", path)
                for method in route.methods:
                    with self.subTest(path=path, method=method):
                        result = self.client.request(method, path, headers={**self.csrf(), "Authorization": "Bearer " + TOKEN}, json={}, follow_redirects=False)
                        self.assertEqual(result.status_code, 403)
            for path in ("/api", "/api/unknown-future-owner-route", "/api/me/", "/genesis", "/guardrails", "/nodes", "/connect/openstl"):
                self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 403)
            legacy.assert_not_awaited(); external.assert_not_awaited()
        self.assertTrue(all(url.endswith(("/internal/team/identity", "/internal/team/console-access"))
                            for _, url, _ in self.forwarded[before:]),
                        "ungranted employees must never forward legacy requests to Core")

    def test_team_csrf_and_server_signed_actor_ignore_browser_credentials(self):
        self.login(); before = len(self.forwarded)
        for headers in ({}, {"Origin": ORIGIN}, {**self.csrf(), "Origin": "https://evil.example"}):
            self.assertEqual(self.client.post("/api/team/rooms", headers=headers, json={"title": "Must not exist"}).status_code, 403)
        self.assertEqual(len(self.forwarded), before)
        result = self.client.post("/api/team/rooms", headers={**self.csrf(), ACTOR_HEADER: "forged-owner", "Authorization": "Bearer browser-supplied"}, json={"title": "Authorized employee room"})
        self.assertEqual(result.status_code, 201)
        _, url, sent = self.forwarded[-1]
        self.assertEqual(urlsplit(url).path, "/api/team/rooms")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer " + TOKEN)
        claims = verify_actor(sent["headers"][ACTOR_HEADER])
        self.assertEqual((claims["sub"], claims["role"], claims["rev"]), (self.member["id"], "employee", 1))
        self.assertNotIn("Cookie", sent["headers"]); self.assertNotIn("X-CSRF-Token", sent["headers"])
        self.assertFalse(sent["follow_redirects"])
        self.assertEqual(self.client.get("/api/team?role=owner").status_code, 422)
        self.assertEqual(self.client.post("/api/team/members", headers=self.csrf(), json={"username": "new-user", "display_name": "New"}).status_code, 403)
        self.login("owner", OWNER_PASSWORD)
        self.assertEqual(self.client.get("/api/team").status_code, 200)
        self.assertEqual(verify_actor(self.forwarded[-1][2]["headers"][ACTOR_HEADER])["sub"], OWNER_ID)
        self.assertEqual(self.client.post("/api/team/members", headers=self.csrf(), json={"username": "mayur", "display_name": "Collision"}).status_code, 409)

    def test_revocation_blocks_existing_employee_sessions_and_cookie_replay(self):
        self.login(); old = self.cookies()
        self.store.disable_member(self.owner, self.member["id"])
        self.assertEqual(self.client.get("/api/team").status_code, 401)
        self.restore(old)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/team", follow_redirects=False).status_code, 303)
        wrong = self.client.post("/login", json={"username": "fixture-employee", "password": self.password})
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn(self.password, wrong.text)

    def test_logout_requires_session_csrf_and_never_revokes_owner_resources(self):
        self.login(); old = self.cookies()
        self.login("owner", OWNER_PASSWORD); owner = self.cookies()
        self.restore((old[0], owner[1]))
        self.assertEqual(self.client.post("/logout", headers=self.csrf()).status_code, 403)
        self.restore(old)
        with patch.object(ax.openstl, "reap", new_callable=AsyncMock) as reap:
            self.assertEqual(self.client.post("/logout", headers=self.csrf()).status_code, 200)
            reap.assert_not_awaited()
        self.restore(old)
        self.assertEqual(self.client.get("/api/team").status_code, 401)
        self.restore(owner)
        self.assertEqual(self.client.get("/api/me").json()["role"], "owner")

    def test_core_redirects_and_unapproved_response_types_are_not_forwarded(self):
        self.login()
        for result in (httpx.Response(302, json={"credential": "private-fixture"}, headers={"Location": "https://evil.example"}),
                       httpx.Response(200, text="private-fixture", headers={"Content-Type": "text/html"}),
                       httpx.Response(500, json={"secret": "private-fixture"})):
            self.upstream = result
            response = self.client.get("/api/team")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private-fixture", response.text); self.assertNotIn("location", response.headers)
        self.upstream = httpx.Response(200, content=b"synthetic-jpeg", headers={"Content-Type": "image/jpeg",
            "X-Session-ID": "123e4567-e89b-42d3-a456-426614174000", "X-Frame-Sequence": "10",
            "X-Frame-Timestamp": "1000.5", "Set-Cookie": "secret=private-fixture", "X-Private": "private-fixture"})
        self.assertEqual(self.client.get("/api/team").status_code, 503)
        response = self.client.get("/api/team/rooms/123e4567-e89b-42d3-a456-426614174000/browser/frame")
        self.assertEqual(response.status_code, 200); self.assertEqual(response.content, b"synthetic-jpeg")
        self.assertEqual(response.headers["x-frame-sequence"], "10")
        self.assertNotIn("set-cookie", response.headers); self.assertNotIn("x-private", response.headers)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_employee_directory_fails_closed_without_changing_local_accounts(self):
        with patch.dict(os.environ, {"AX_TEAM_ENABLED": "0"}):
            response = self.client.post("/login", json={"username": "fixture-employee", "password": self.password})
            self.assertEqual(response.status_code, 401); self.assertEqual(self.forwarded, [])
            self.login("owner", OWNER_PASSWORD)
        self.upstream = httpx.Response(200, json={"identity": {"id": self.member["id"], "username": "fixture-employee",
            "display_name": "Fixture", "role": "owner", "credential_revision": 1}})
        response = self.client.post("/login", json={"username": "fixture-employee", "password": self.password})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.password, response.text)


if __name__ == "__main__": unittest.main()
