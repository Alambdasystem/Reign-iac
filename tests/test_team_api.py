import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx

from reign.team_api import TeamRuntime, install
from reign.team_identity import ACTOR_HEADER, OWNER_ID, TeamIdentityError, issue_actor, verify_actor
from reign.team_store import TeamError, TeamStore, owner_identity

TOKEN = "synthetic-core-service-token-for-team-tests-123456"


def authorize(request):
    if request.headers.get("authorization") != "Bearer " + TOKEN: raise HTTPException(401, "Unauthorized")


def headers(actor):
    return {"Authorization": "Bearer " + TOKEN, ACTOR_HEADER: issue_actor(actor["id"], actor["role"], actor["credential_revision"])}


def make_app(runtime):
    @asynccontextmanager
    async def lifespan(app):
        await runtime.start(); app.state.team = runtime
        try: yield
        finally: await runtime.shutdown()
    app = FastAPI(lifespan=lifespan)
    app.state.team = runtime
    install(app, authorize)
    return app


class IdentityTests(unittest.TestCase):
    def test_assertion_binds_signature_audience_expiry_role_and_stable_subject(self):
        with patch.dict(os.environ, {"CORE_TOKEN": TOKEN}):
            uid = str(uuid.uuid4())
            token = issue_actor(uid, "employee", 3, now=100)
            self.assertEqual(verify_actor(token, now=101)["sub"], uid)
            for stamp in (99, 160, 1000):
                with self.assertRaises(TeamIdentityError): verify_actor(token, now=stamp)
            with self.assertRaises(TeamIdentityError): verify_actor(token[:-1] + ("A" if token[-1] != "A" else "B"), now=101)
            with self.assertRaises(TeamIdentityError): issue_actor(uid, "owner", 0)
            with self.assertRaises(TeamIdentityError): issue_actor(OWNER_ID, "employee", 1)
            body = token.split(".")[0]
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            payload["aud"] = "unrelated-service"
            changed = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            key = hmac.digest(TOKEN.encode(), b"reign/team-actor/v1", "sha256")
            signature = base64.urlsafe_b64encode(hmac.digest(key, changed.encode(), "sha256")).decode().rstrip("=")
            with self.assertRaises(TeamIdentityError): verify_actor(changed + "." + signature, now=101)


class TeamAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-team-api-"); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"CORE_TOKEN": TOKEN}); self.env.start(); self.addCleanup(self.env.stop)
        self.runtime = TeamRuntime(TeamStore(self.temp.name, bytes(range(32))))
        self.client = TestClient(make_app(self.runtime)); self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.owner = owner_identity()
        result = self.client.post("/api/team/members", headers=headers(self.owner), json={"username": "fixture-user", "display_name": "Fixture User"})
        self.assertEqual(result.status_code, 201)
        self.created = result.json()
        self.member = {key: self.created["member"][key] for key in ("id", "username", "display_name", "role", "credential_revision")}
        self.room = self.client.post("/api/team/rooms", headers=headers(self.member), json={"title": "Fixture room"}).json()["room"]["id"]

    def test_every_team_endpoint_requires_service_and_actor_identity(self):
        for route in self.client.app.routes:
            if not getattr(route, "path", "").startswith("/api/team"): continue
            path = route.path.replace("{room_id}", self.room)
            for name in ("user_id", "document_id", "connection_id"): path = path.replace("{" + name + "}", str(uuid.uuid4()))
            for method in route.methods:
                with self.subTest(path=path, method=method):
                    denied = self.client.request(method, path, json={"user_id": OWNER_ID, "role": "owner"})
                    self.assertEqual(denied.status_code, 401)
                    self.assertEqual(denied.headers["cache-control"], "no-store")
                    denied = self.client.request(method, path, headers={"Authorization": "Bearer " + TOKEN}, json={})
                    self.assertEqual(denied.status_code, 401)

    def test_employee_login_resolution_and_member_secrets_are_not_reflected(self):
        auth = {"Authorization": "Bearer " + TOKEN}
        response = self.client.post("/internal/team/authenticate", headers=auth, json={"username": "fixture-user", "password": self.created["temporary_password"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"identity": self.member})
        self.assertNotIn(self.created["temporary_password"], response.text)
        self.assertEqual(self.client.post("/internal/team/identity", headers=auth, json={"id": self.member["id"], "credential_revision": 1}).status_code, 200)
        for payload in ({"username": "fixture-user", "password": {"private": "fixture-secret"}},
                        {"username": "fixture-user", "password": "fixture-secret", "role": "owner"}):
            error = self.client.post("/internal/team/authenticate", headers=auth, json=payload)
            self.assertIn(error.status_code, (401, 422)); self.assertNotIn("fixture-secret", error.text)
        listed = self.client.get("/api/team", headers=headers(self.member))
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(listed.json()["capabilities"]["manage_members"])
        self.assertFalse(listed.json()["capabilities"]["shared_browser"])
        self.assertNotIn("password", listed.text)
        self.assertEqual(self.client.post("/api/team/members", headers=headers(self.member), json={"username": "bad", "display_name": "Bad"}).status_code, 403)
        disabled = self.client.patch("/api/team/members/" + self.member["id"], headers=headers(self.owner), json={"active": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.client.get("/api/team", headers=headers(self.member)).status_code, 401)
        self.assertEqual(self.client.post("/internal/team/identity", headers=auth, json={"id": self.member["id"], "credential_revision": 1}).status_code, 401)

    def test_identity_fields_cannot_override_actor_or_document_room(self):
        path = f"/api/team/rooms/{self.room}"
        bad = self.client.post(path + "/messages", headers=headers(self.member), json={"client_message_id": str(uuid.uuid4()), "text": "hello", "user_id": OWNER_ID})
        self.assertEqual(bad.status_code, 422)
        created = self.client.post(path + "/documents", headers=headers(self.member), json={"client_document_id": str(uuid.uuid4()), "title": "Plan", "content": "Initial"}).json()["document"]
        other = self.client.post("/api/team/rooms", headers=headers(self.member), json={"title": "Second"}).json()["room"]["id"]
        self.assertEqual(self.client.get(f"/api/team/rooms/{other}/documents/{created['id']}", headers=headers(self.member)).status_code, 404)
        edit = {"title": "Plan", "content": "Changed", "expected_version": 1}
        self.assertEqual(self.client.put(path + "/documents/" + created["id"], headers=headers(self.member), json=edit).status_code, 200)
        self.assertEqual(self.client.put(path + "/documents/" + created["id"], headers=headers(self.member), json=edit).status_code, 409)

    def test_login_work_is_bounded_before_native_password_work(self):
        self.runtime.pending_auth = 2
        with patch.object(self.runtime.store, "authenticate") as work:
            response = self.client.post("/internal/team/authenticate", headers={"Authorization": "Bearer " + TOKEN}, json={"username": "fixture-user", "password": "fixture"})
        self.assertEqual(response.status_code, 429); work.assert_not_called()
        self.assertEqual(self.runtime.pending_auth, 2)
        self.runtime.pending_auth = 0

    def test_call_config_offers_public_stun_but_never_claims_turn_relay(self):
        with patch.dict(os.environ, {"REIGN_TEAM_STUN_DISABLED": "0"}):
            response = self.client.get("/api/team/call-config", headers=headers(self.member))
        self.assertEqual(response.json()["ice_servers"], [{"urls": "stun:stun.cloudflare.com:3478"}])
        self.assertFalse(response.json()["relay_configured"])
        with patch.dict(os.environ, {"REIGN_TEAM_STUN_DISABLED": "1"}):
            self.assertEqual(self.client.get("/api/team/call-config", headers=headers(self.member)).json()["ice_servers"], [])


class TeamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-team-runtime-"); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"CORE_TOKEN": TOKEN}); self.env.start(); self.addCleanup(self.env.stop)
        self.now = 1000.0
        self.store = TeamStore(self.temp.name, bytes(range(32)), clock=lambda: self.now)
        self.runtime = TeamRuntime(self.store, clock=lambda: self.now); await self.runtime.start()
        self.owner = owner_identity()
        created = self.store.create_member(self.owner, "worker", "Worker")
        self.member = self.store.authenticate("worker", created["temporary_password"])
        self.rooms = [self.store.create_room(self.owner, f"Room {i}")["room"]["id"] for i in range(3)]

    async def asyncTearDown(self): await self.runtime.shutdown()

    def message(self, **extra):
        return {"client_message_id": str(uuid.uuid4()), "text": "A request", **extra}

    async def test_room_wait_wakes_on_media_change_and_leave_but_not_lease_renewal(self):
        room_id = self.rooms[0]
        cid = str(uuid.uuid4())
        presence = {"connection_id": cid, "in_call": True, "microphone": False, "camera": False, "screen": False}
        await self.runtime.heartbeat(self.owner, room_id, presence)
        app = make_app(self.runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/team/rooms/{room_id}/events?after=0&wait=2"
            waiting = asyncio.create_task(client.get(path, headers=headers(self.owner)))
            await asyncio.sleep(.05)
            self.now += 1  # Change expires_at while keeping all visible flags.
            await self.runtime.heartbeat(self.owner, room_id, presence)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiting), .35)
            await self.runtime.heartbeat(self.owner, room_id, {**presence, "microphone": True, "camera": True})
            response = await asyncio.wait_for(waiting, .75)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["presence"][0]["microphone"])
            self.assertTrue(response.json()["presence"][0]["camera"])
            waiting = asyncio.create_task(client.get(path, headers=headers(self.owner)))
            await asyncio.sleep(.05)
            await self.runtime.leave(self.owner, room_id, cid)
            response = await asyncio.wait_for(waiting, .75)
            self.assertEqual(response.json()["presence"], [])

    async def test_one_run_per_room_global_limit_and_idempotency_preserve_authorized_context(self):
        gate = asyncio.Event(); seen = []
        async def assistant(context, actor, text, attachments, speak):
            seen.append(context); await gate.wait()
            return {"text": "Shared answer", "segments": [{"text": "Shared answer", "expression": "warm", "audio_id": "fixture_audio_opaque_id"}],
                    "documents": [{"title": "Assistant note", "content": "A saved result"}]}
        self.runtime.assistant = assistant
        await self.runtime.message(self.member, self.rooms[0], self.message(text="Room zero prior context"))
        await self.runtime.message(self.member, self.rooms[1], self.message(text="Room one private context"))
        first_data = self.message(ask_reign=True)
        first = await self.runtime.message(self.member, self.rooms[0], first_data)
        repeated = await self.runtime.message(self.member, self.rooms[0], first_data)
        self.assertEqual(first, repeated)
        with self.assertRaises(TeamError) as busy:
            await self.runtime.message(self.owner, self.rooms[0], self.message(ask_reign=True))
        self.assertEqual(busy.exception.code, "assistant_busy")
        await self.runtime.message(self.member, self.rooms[1], self.message(ask_reign=True))
        with self.assertRaises(TeamError) as limited:
            await self.runtime.message(self.member, self.rooms[2], self.message(ask_reign=True))
        self.assertEqual(limited.exception.code, "team_limit")
        tasks = [value["task"] for value in self.runtime.active.values()]
        gate.set(); await asyncio.gather(*tasks)
        self.assertEqual(len(seen), 2)
        context = next(value for value in seen if value["room"]["id"] == self.rooms[0])
        self.assertEqual([value["text"] for value in context["messages"]], ["Room zero prior context"])
        self.assertNotIn("Room one private context", json.dumps(context))
        result = self.store.events(self.member, self.rooms[0])
        self.assertEqual(result["assistant"]["status"], "completed")
        self.assertEqual(result["events"][-1]["data"]["segments"][0]["audio_id"], "fixture_audio_opaque_id")
        self.assertEqual(result["documents"][0]["title"], "Assistant note")

    async def test_assistant_failures_are_recorded_without_private_error_text(self):
        async def assistant(*args): raise RuntimeError("private-provider-token-fixture")
        self.runtime.assistant = assistant
        await self.runtime.message(self.member, self.rooms[0], self.message(ask_reign=True))
        await asyncio.gather(*(value["task"] for value in list(self.runtime.active.values())))
        result = self.store.events(self.member, self.rooms[0])
        self.assertEqual(result["assistant"]["status"], "failed")
        self.assertNotIn("private-provider-token-fixture", json.dumps(result))

    async def test_disconnected_message_commit_still_dispatches_once_and_retry_recovers_receipt(self):
        entered, release = threading.Event(), threading.Event()
        original = self.store.append_message
        def blocked(*args):
            entered.set()
            if not release.wait(3): raise AssertionError("test release was not signaled")
            return original(*args)
        calls = []
        async def assistant(*args):
            calls.append(args); return {"text": "Completed after caller disconnect"}
        self.runtime.assistant = assistant
        data = self.message(ask_reign=True)
        with patch.object(self.store, "append_message", blocked):
            request = asyncio.create_task(self.runtime.message(self.member, self.rooms[0], data))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError): await request
            self.assertEqual(len(self.runtime.accepting), 1)
            release.set()
            await asyncio.gather(*tuple(self.runtime.accepting))
        await asyncio.gather(*(row["task"] for row in list(self.runtime.active.values())))
        recovered = await self.runtime.message(self.member, self.rooms[0], data)
        self.assertIsNotNone(recovered["run_id"])
        self.assertEqual(len(calls), 1)
        snapshot = self.store.events(self.member, self.rooms[0])
        self.assertEqual(snapshot["assistant"]["status"], "completed")
        self.assertEqual([row["type"] for row in snapshot["events"]], ["message", "assistant_started", "assistant_message"])

    async def test_attachments_validate_before_write_but_idempotent_retries_do_not_revalidate(self):
        calls = []
        async def validator(actor, room_id, attachments):
            calls.append(attachments)
            if len(calls) == 1: raise TeamError("team_not_found")
        async def assistant(*args):
            return {"text": "The text reply still succeeds.", "speech_status": "unavailable"}
        self.runtime.assistant = assistant; self.runtime.attachment_validator = validator
        data = self.message(ask_reign=True, speak=True, attachment_ids=[str(uuid.uuid4())])
        with self.assertRaises(TeamError): await self.runtime.message(self.member, self.rooms[0], data)
        self.assertEqual(self.store.events(self.member, self.rooms[0])["events"], [])
        first = await self.runtime.message(self.member, self.rooms[0], data)
        again = await self.runtime.message(self.member, self.rooms[0], data)
        self.assertEqual(first, again); self.assertEqual(len(calls), 2)
        await asyncio.gather(*(value["task"] for value in list(self.runtime.active.values())))
        result = self.store.events(self.member, self.rooms[0])
        self.assertEqual(result["assistant"]["status"], "completed")
        self.assertEqual(result["events"][-1]["data"]["speech_status"], "unavailable")

    async def test_call_presence_requires_opt_in_and_signals_are_connection_and_room_bound(self):
        a, b, c = (str(uuid.uuid4()) for _ in range(3))
        room = self.rooms[0]
        await self.runtime.heartbeat(self.owner, room, {"connection_id": a})
        await self.runtime.heartbeat(self.member, room, {"connection_id": b, "in_call": True})
        signal = {"connection_id": a, "to": b, "kind": "offer", "payload": {"type": "offer", "sdp": "synthetic-sdp"}}
        with self.assertRaises(TeamError): await self.runtime.send_signal(self.owner, room, signal)
        await self.runtime.heartbeat(self.owner, room, {"connection_id": a, "in_call": True})
        await self.runtime.send_signal(self.owner, room, signal)
        found = await self.runtime.read_signals(self.member, room, b, 0)
        self.assertEqual(found["signals"][0]["from"], a)
        with self.assertRaises(TeamError): await self.runtime.read_signals(self.owner, room, b, 0)
        await self.runtime.heartbeat(self.owner, self.rooms[1], {"connection_id": c, "in_call": True})
        with self.assertRaises(TeamError): await self.runtime.send_signal(self.owner, room, {**signal, "to": c})
        with self.assertRaises(TeamError): await self.runtime.heartbeat(self.owner, room, {"connection_id": b, "in_call": True})
        await self.runtime.leave(self.member, room, b)
        self.assertEqual(self.runtime.signals, [])
        self.assertTrue(self.store.events(self.owner, room)["room"])
        self.now += 31
        self.assertEqual(self.runtime.presence_rows(room), [])

    async def test_presence_call_and_signal_bounds_and_disabled_member_cleanup(self):
        room = self.rooms[0]; connections = [str(uuid.uuid4()) for _ in range(5)]
        for cid in connections[:3]: await self.runtime.heartbeat(self.owner, room, {"connection_id": cid, "in_call": True})
        await self.runtime.heartbeat(self.member, room, {"connection_id": connections[3], "in_call": True})
        with self.assertRaises(TeamError) as full:
            await self.runtime.heartbeat(self.member, room, {"connection_id": connections[4], "in_call": True})
        self.assertEqual(full.exception.code, "team_call_full")
        with patch("reign.team_api.MAX_SIGNALS", 1):
            signal = {"connection_id": connections[0], "to": connections[3], "kind": "ice", "payload": None}
            await self.runtime.send_signal(self.owner, room, signal)
            with self.assertRaises(TeamError): await self.runtime.send_signal(self.owner, room, signal)
        self.store.disable_member(self.owner, self.member["id"]); self.runtime.revoke_member(self.member["id"])
        self.assertNotIn(connections[3], self.runtime.presence)
        self.assertEqual(self.runtime.signals, [])
        with self.assertRaises(TeamError): await self.runtime.read_signals(self.member, room, connections[3], 0)

    async def test_deleted_connection_cannot_be_revived_by_a_reordered_heartbeat(self):
        cid, room = str(uuid.uuid4()), self.rooms[0]
        await self.runtime.heartbeat(self.member, room, {"connection_id": cid, "in_call": True})
        original = self.runtime.authorize_room; entered = asyncio.Event(); release = asyncio.Event()
        async def delayed(actor, room_id):
            result = await original(actor, room_id)
            if asyncio.current_task().get_name() == "late-heartbeat":
                entered.set(); await release.wait()
            return result
        with patch.object(self.runtime, "authorize_room", side_effect=delayed):
            late = asyncio.create_task(self.runtime.heartbeat(self.member, room, {"connection_id": cid, "in_call": True}), name="late-heartbeat")
            await entered.wait()
            await self.runtime.leave(self.member, room, cid)
            release.set()
            with self.assertRaises(TeamError): await late
        self.assertNotIn(cid, self.runtime.presence)
        self.assertIn(cid, self.runtime.tombstones)
        fresh = str(uuid.uuid4())
        await self.runtime.heartbeat(self.member, room, {"connection_id": fresh, "in_call": True})
        self.assertIn(fresh, self.runtime.presence)

    async def test_revoked_member_cannot_reappear_from_an_already_authorized_heartbeat(self):
        cid, room = str(uuid.uuid4()), self.rooms[0]
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.runtime.authorize_room
        async def delayed(actor, room_id):
            result = await original(actor, room_id)
            entered.set(); await release.wait()
            return result
        with patch.object(self.runtime, "authorize_room", delayed):
            task = asyncio.create_task(self.runtime.heartbeat(self.member, room, {"connection_id": cid, "in_call": True}))
            await entered.wait()
            self.store.disable_member(self.owner, self.member["id"])
            self.runtime.revoke_member(self.member["id"])
            release.set()
            with self.assertRaises(TeamError) as error: await task
        self.assertEqual(error.exception.code, "team_identity_required")
        self.assertEqual(self.runtime.presence_rows(room), [])

    async def test_long_poll_rechecks_membership_after_wait(self):
        app = make_app(self.runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixture.test") as client:
            polling = asyncio.create_task(client.get(f"/api/team/rooms/{self.rooms[0]}/events?after=0&wait=1", headers=headers(self.member)))
            await asyncio.sleep(0.05)
            await asyncio.to_thread(self.store.disable_member, self.owner, self.member["id"])
            result = await polling
        self.assertEqual(result.status_code, 401)


if __name__ == "__main__": unittest.main()
