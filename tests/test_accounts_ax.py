import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from reign.ax import app


SETTINGS = {"AX_SESSION_KEY": "s" * 40, "AX_ADMIN_PASSWORD": "p" * 40,
            "CORE_TOKEN": "c" * 40, "CORE_URL": "https://core.example.com",
            "AX_PUBLIC_ORIGIN": "https://ax.example.com",
            "ALLOWED_HOSTS": "alambda-forge.onrender.com"}
ACCOUNT_ID = "db893d55-d90b-48e5-b6f1-7dce7c6595f5"
SESSION_ID = "7f42e167-3e82-40c8-8880-51dab943ce03"
THREAD_ID = "af16a2fe-12df-4244-b271-6dd3b47cba16"
PASSWORD = "  synthetic-test-password  "
METADATA = {"id": ACCOUNT_ID, "label": "Git High test",
            "origin": "https://alambda-forge.onrender.com", "username": "fixture-user",
            "created_at": "2026-11-09T04:00:00Z", "updated_at": "2026-11-09T04:00:00Z"}


@contextmanager
def client_for(handler, *, signed_in=True):
    with patch.dict(os.environ, SETTINGS):
        with TestClient(app, base_url="https://ax.example.com") as client:
            # Follow redirects at client level to prove the credential proxy
            # explicitly pins each account request to its configured Core.
            proxy = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            with patch.object(app.state, "http", proxy):
                try:
                    if signed_in:
                        assert client.post("/login", json={"password": SETTINGS["AX_ADMIN_PASSWORD"]}).status_code == 200
                    yield client
                finally:
                    client.portal.call(proxy.aclose)


def csrf(client):
    return {"Origin": SETTINGS["AX_PUBLIC_ORIGIN"], "X-CSRF-Token": client.cookies.get("ax_csrf", "")}


class AccountAXTests(unittest.TestCase):
    def test_account_surfaces_require_login_and_csrf_before_forwarding(self):
        requests = []
        def upstream(request):
            requests.append(request)
            return httpx.Response(500)
        with client_for(upstream, signed_in=False) as client:
            for method, path in (("GET", "/api/accounts"), ("POST", "/api/accounts"),
                                 ("DELETE", f"/api/accounts/{ACCOUNT_ID}"),
                                 ("POST", f"/api/accounts/{ACCOUNT_ID}/use")):
                response = client.request(method, path, json={"password": PASSWORD})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn(PASSWORD, response.text)
            client.post("/login", json={"password": SETTINGS["AX_ADMIN_PASSWORD"]})
            for method, path in (("POST", "/api/accounts"), ("DELETE", f"/api/accounts/{ACCOUNT_ID}"),
                                 ("POST", f"/api/accounts/{ACCOUNT_ID}/use")):
                response = client.request(method, path, json={"password": PASSWORD})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["cache-control"], "no-store")
            headers = {**csrf(client), "Origin": "https://other.example"}
            self.assertEqual(client.post("/api/accounts", headers=headers, json={}).status_code, 403)
        self.assertEqual(requests, [])

    def test_secure_save_forwards_exact_secret_once_and_returns_only_metadata(self):
        requests = []
        def upstream(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://core.example.com/api/accounts")
            self.assertEqual(request.headers["authorization"], "Bearer " + SETTINGS["CORE_TOKEN"])
            body = json.loads(request.content)
            self.assertEqual(body, {"label": "Git High test", "origin": METADATA["origin"],
                                    "username": "fixture-user", "password": PASSWORD})
            return httpx.Response(201, json={"account": {**METADATA, "password": PASSWORD,
                "owner_id": "internal-only", "encrypted_password": "private-ciphertext"}, "grant": "private-grant"})
        with client_for(upstream) as client:
            response = client.post("/api/accounts", headers=csrf(client), json={
                "label": " Git High test ", "origin": "https://ALAMBDA-FORGE.ONRENDER.COM:443/",
                "username": "fixture-user", "password": PASSWORD})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), {"account": METADATA})
            self.assertEqual(response.headers["cache-control"], "no-store")
            for private in (PASSWORD, "private-ciphertext", "private-grant", SETTINGS["CORE_TOKEN"]):
                self.assertNotIn(private, response.text)
        self.assertEqual(len(requests), 1)

    def test_invalid_secret_payloads_are_bounded_and_never_echoed(self):
        requests = []
        def upstream(request):
            requests.append(request)
            return httpx.Response(500)
        baseline = {"label": "Git High test", "origin": METADATA["origin"],
                    "username": "fixture-user", "password": PASSWORD}
        with client_for(upstream) as client:
            invalid = [
                {**baseline, "owner_id": "other-owner"},
                {**baseline, "password": {"private": PASSWORD}},
                {**baseline, "password": PASSWORD * 60},
                {**baseline, "username": [PASSWORD]},
                {**baseline, "origin": "https://outlook.office.com"},
                {**baseline, "origin": METADATA["origin"] + "/user/login?secret=" + PASSWORD},
                {**baseline, "password": "\ud800"},
            ]
            for value in invalid:
                with self.subTest(fields=tuple(value)):
                    # ASCII escaping keeps a lone surrogate transport-valid so
                    # rejection is exercised inside AX, not in the test client.
                    response = client.post("/api/accounts", headers={**csrf(client), "Content-Type": "application/json"},
                                           content=json.dumps(value))
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(set(response.json()), {"code", "detail"})
                    self.assertNotIn(PASSWORD, response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
            oversized = b'{"password":"' + b"x" * 9000 + b'"}'
            response = client.post("/api/accounts", headers=csrf(client), content=oversized)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("xxxx", response.text)
            self.assertEqual(client.get("/api/accounts?owner_id=other-owner").status_code, 422)
        self.assertEqual(requests, [])

    def test_metadata_list_and_unavailable_vault_are_honest_and_secret_free(self):
        replies = [
            {"vault": {"ready": True, "durable": True, "code": None, "key": PASSWORD},
             "accounts": [{**METADATA, "password": PASSWORD}]},
            {"vault": {"ready": False, "durable": False, "code": PASSWORD, "detail": PASSWORD}, "accounts": []},
        ]
        def upstream(request):
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json=replies.pop(0))
        with client_for(upstream) as client:
            response = client.get("/api/accounts")
            self.assertEqual(response.json(), {"vault": {"ready": True, "durable": True, "code": None},
                                               "accounts": [METADATA]})
            self.assertNotIn(PASSWORD, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            response = client.get("/api/accounts")
            self.assertEqual(response.json(), {"vault": {"ready": False, "durable": False,
                                                        "code": "vault_not_ready"}, "accounts": []})
            self.assertNotIn(PASSWORD, response.text)

    def test_only_closed_core_errors_are_returned(self):
        statuses = {"vault_not_ready": 503, "vault_unavailable": 503, "invalid_account": 422,
                    "origin_not_allowed": 422, "account_not_found": 404, "account_exists": 409,
                    "account_use_conflict": 409, "grant_invalid": 403}
        for code in (*statuses, "unknown-private-code", [], None):
            with self.subTest(code=code):
                def upstream(request):
                    return httpx.Response(500, json={"code": code, "detail": PASSWORD,
                                                   "input": {"password": PASSWORD}})
                with client_for(upstream) as client:
                    response = client.get("/api/accounts")
                    expected = code if isinstance(code, str) and code in statuses else "vault_unavailable"
                    self.assertEqual(response.status_code, statuses[expected])
                    self.assertEqual(response.json()["code"], expected)
                    self.assertEqual(set(response.json()), {"code", "detail"})
                    if code == "account_exists":
                        self.assertEqual(response.json()["detail"], "A saved login with that label already exists. Choose a different label.")
                    self.assertNotIn(PASSWORD, response.text)
                    self.assertNotIn("unknown-private-code", response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")

    def test_saved_login_use_forwards_ids_and_returns_graph_snapshot_without_grants(self):
        requests = []
        snapshot = {"thread_id": THREAD_ID, "run_id": "run-fixture", "status": "running", "answer": "",
                    "error": None, "session_id": SESSION_ID, "events": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0}, "memory": "ephemeral",
                    "turns": 1, "max_turns": 12, "continuable": False}
        def upstream(request):
            requests.append(request)
            self.assertEqual(request.url.path, f"/api/accounts/{ACCOUNT_ID}/use")
            self.assertEqual(json.loads(request.content), {"session_id": SESSION_ID, "thread_id": THREAD_ID})
            return httpx.Response(200, json={**snapshot, "password": PASSWORD, "grant": "private-grant"})
        with client_for(upstream) as client:
            response = client.post(f"/api/accounts/{ACCOUNT_ID}/use", headers=csrf(client),
                                   json={"session_id": SESSION_ID, "thread_id": THREAD_ID})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), snapshot)
            self.assertEqual(response.headers["cache-control"], "no-store")
            for value in ({"session_id": SESSION_ID, "owner_id": "other"},
                          {"session_id": SESSION_ID, "password": PASSWORD}, {"session_id": "invalid"}):
                denied = client.post(f"/api/accounts/{ACCOUNT_ID}/use", headers=csrf(client), json=value)
                self.assertEqual(denied.status_code, 422)
                self.assertNotIn(PASSWORD, denied.text)
        self.assertEqual(len(requests), 1)

    def test_remove_only_forwards_the_selected_id(self):
        requests = []
        def upstream(request):
            requests.append(request)
            self.assertEqual(request.method, "DELETE")
            self.assertEqual(request.url.path, f"/api/accounts/{ACCOUNT_ID}")
            return httpx.Response(200, json={"status": "deleted", "id": ACCOUNT_ID, "password": PASSWORD})
        with client_for(upstream) as client:
            denied = client.request("DELETE", f"/api/accounts/{ACCOUNT_ID}", headers=csrf(client),
                                    json={"owner_id": "other"})
            self.assertEqual(denied.status_code, 422)
            response = client.delete(f"/api/accounts/{ACCOUNT_ID}", headers=csrf(client))
            self.assertEqual(response.json(), {"status": "deleted", "id": ACCOUNT_ID})
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(PASSWORD, response.text)
        self.assertEqual(len(requests), 1)

    def test_vault_requests_never_redirect_or_return_transport_error_text(self):
        requests = []
        def redirect(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://other.example/collect"})
        with client_for(redirect) as client:
            response = client.post("/api/accounts", headers=csrf(client), json={
                "label": "Git High test", "origin": METADATA["origin"],
                "username": "fixture-user", "password": PASSWORD})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "vault_unavailable")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "core.example.com")
        def unavailable(request):
            raise httpx.ConnectError(PASSWORD, request=request)
        with client_for(unavailable) as client:
            response = client.get("/api/accounts")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(PASSWORD, response.text)


if __name__ == "__main__":
    unittest.main()
