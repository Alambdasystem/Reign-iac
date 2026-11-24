"""Provider contract fixtures for the owner mailbox pilot."""

import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi import HTTPException
import httpx

from reign.mail_connections import GMAIL, GOOGLE_SCOPE, GRAPH, MailConnections, settings
from reign.team_store import TeamStore
from reign.tool_contracts import tool_specs

ENV = {
    "REIGN_MAIL_ORIGIN": "https://reign.example.com",
    "REIGN_GMAIL_CLIENT_ID": "gmail-fixture",
    "REIGN_GMAIL_CLIENT_SECRET": "gmail-fixture-secret",
    "REIGN_GMAIL_OWNER_EMAIL": "owner@gmail.example",
    "REIGN_OUTLOOK_CLIENT_ID": "outlook-fixture",
    "REIGN_OUTLOOK_CLIENT_SECRET": "outlook-fixture-secret",
    "REIGN_OUTLOOK_OWNER_EMAIL": "owner@outlook.example",
    "REIGN_OUTLOOK_TENANT_ID": "07694da6-b5c4-4efe-a3be-981c0b11a632",
}


class MailTests(unittest.IsolatedAsyncioTestCase):
    def handler(self, request):
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        google = request.url.host in ("oauth2.googleapis.com", "gmail.googleapis.com")
        provider = "gmail" if google else "outlook"
        if request.method == "POST":
            config = settings(provider)
            self.assertEqual(url, config.token)
            params = parse_qs(request.content.decode())
            self.assertEqual(params["client_secret"], [config.secret])
            if params["grant_type"] == ["authorization_code"]:
                self.assertEqual(params["code_verifier"], ["V" * 43])
                self.assertEqual(params["redirect_uri"], [config.redirect])
            else:
                self.assertEqual(params["refresh_token"], ["fixture-refresh"])
            scopes = GOOGLE_SCOPE if google else "User.Read Mail.ReadBasic offline_access"
            return httpx.Response(200, json={"token_type": "Bearer", "access_token": "fixture-access",
                                             "refresh_token": "fixture-refresh", "expires_in": 3600,
                                             "scope": scopes + self.extra_scope})
        self.assertEqual(request.headers["authorization"], "Bearer fixture-access")
        if url in (GMAIL + "/profile", GRAPH):
            mailbox = "wrong@example.com" if self.wrong_mailbox else settings(provider).mailbox
            return httpx.Response(200, json={"emailAddress": mailbox, "mail": mailbox, "id": "verified-mailbox"})
        if url == GMAIL + "/messages":
            self.assertEqual(request.url.params.get_list("labelIds"), ["INBOX", "UNREAD"])
            self.assertNotIn("q", request.url.params)
            return httpx.Response(200, json={"messages": [{"id": "fixture1"}], "nextPageToken": "unused"})
        if url == GMAIL + "/messages/fixture1":
            self.assertEqual(request.url.params["format"], "metadata")
            return httpx.Response(200, json={"payload": {"headers": [{"name": "Subject", "value": "Gmail subject"}]},
                                             "snippet": "Private body text"})
        if url == GRAPH + "/mailFolders/inbox/messages":
            self.assertEqual(request.url.params["$filter"], "isRead eq false")
            return httpx.Response(200, json={"value": [
                {"isRead": False, "subject": "Outlook subject", "body": "Private body text"},
                {"isRead": True, "subject": "Already read"},
            ]})
        self.fail("Unexpected provider request")

    async def asyncSetUp(self):
        self.wrong_mailbox = False
        self.extra_scope = ""
        self.requests = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.store = TeamStore(temp.name, bytes(range(32)))
        self.directory = temp.name
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        self.addAsyncCleanup(self.http.aclose)
        self.mail = MailConnections(self.store, self.http)

    async def test_both_providers_consent_verification_unread_refresh_and_disconnect(self):
        from pathlib import Path

        for provider in ("gmail", "outlook"):
            state, verifier = "T" * 43, "V" * 43
            begun = await self.mail.begin(provider, state, verifier)
            params = parse_qs(urlsplit(begun["url"]).query)
            self.assertEqual(params["state"], [state])
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertNotIn(verifier, begun["url"])
            result = await self.mail.connect(provider, "fixture-code", verifier, begun["revision"])
            self.assertEqual(result, {"status": "connected", "email": settings(provider).mailbox})
            persisted = TeamStore(self.directory, bytes(range(32)))
            current, grant = persisted.owner_mail(provider)
            self.assertEqual(await self.mail.status() != {}, True)
            persisted.owner_mail(provider, (current, {**grant, "expires": 0}))
            sample = await self.mail.unread(provider, 5)
            self.assertEqual(sample["count_kind"], "sample")
            self.assertEqual(sample["sampled_unread"], 1)
            self.assertEqual(sample["messages"][0]["subject"], provider.title() + " subject")
            self.assertNotIn("Private body text", str(sample))
            self.assertNotIn(b"fixture-refresh", (Path(self.directory) / "reign-team.sqlite3").read_bytes())
            self.assertNotIn("reign_mail_unread", tool_specs())
            pending = await self.mail.begin(provider, state, verifier)
            await self.mail.disconnect(provider)
            with self.assertRaises(HTTPException) as stale:
                await self.mail.connect(provider, "fixture-code", verifier, pending["revision"])
            self.assertEqual(stale.exception.status_code, 409)
            with self.assertRaises(HTTPException):
                await self.mail.unread(provider, 5)

    async def test_real_stdio_mcp_reaches_the_loopback_only_with_owner_authorization(self):
        import asyncio
        import socket

        from fastapi import FastAPI, Request
        import uvicorn

        from reign.mail_routes import install
        from reign.mcp_client import WorkspaceMCP

        async def authorize(request: Request):
            if request.headers.get("authorization") != "Bearer fixture-core":
                raise HTTPException(401, "Service authorization required")

        app = FastAPI()
        install(app, authorize)
        app.state.mail = self.mail
        mcp = WorkspaceMCP()
        mcp.mail_key = "p" * 43
        app.state.mail_mcp = mcp
        await self.mail.connect("gmail", "fixture-code", "V" * 43, 0)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(6):
                    while not server.started:
                        await asyncio.sleep(.01)
                with patch.dict(os.environ, {"PORT": str(port)}):
                    async with mcp.connect():
                        with self.assertRaises(HTTPException) as unauthorized:
                            await mcp.invoke("reign_mail_unread", {"provider": "gmail"})
                        self.assertEqual(unauthorized.exception.status_code, 403)
                        result = await mcp.invoke("reign_mail_unread", {"provider": "gmail"}, mailbox_owner=True)
                        self.assertEqual(result["messages"][0]["subject"], "Gmail subject")
            finally:
                server.should_exit = True
                await task

    async def test_wrong_mailbox_and_extra_scope_do_not_save_tokens(self):
        self.wrong_mailbox = True
        with self.assertRaises(HTTPException) as wrong:
            await self.mail.connect("outlook", "fixture-code", "V" * 43, 0)
        self.assertEqual(wrong.exception.status_code, 403)
        self.assertEqual(self.store.owner_mail("outlook"), (0, None))
        self.wrong_mailbox = False
        self.extra_scope = " https://www.googleapis.com/auth/gmail.modify"
        with self.assertRaises(HTTPException) as scope:
            await self.mail.connect("gmail", "fixture-code", "V" * 43, 0)
        self.assertEqual(scope.exception.status_code, 409)
        self.assertEqual(self.store.owner_mail("gmail"), (0, None))
