"""Real membership/presence boundaries and the outgoing room model payload."""
import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx

from reign.team_api import TeamRuntime
from reign.team_assistant import TeamAssistant
from reign.team_media import TeamMedia
from reign.team_store import MAX_ROOM_PARTICIPANTS, TeamError, TeamStore, owner_identity


class RoomParticipantContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-room-presence-")
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.store = TeamStore(self.temp.name, bytes(range(32)), clock=lambda: self.now)
        self.runtime = TeamRuntime(self.store, clock=lambda: self.now)
        await self.runtime.start()
        self.owner = owner_identity()
        self.alex = self.member("alex", "Alex")
        self.blair = self.member("blair", "Blair")
        self.room = self.store.create_room(self.owner, "Current room")["room"]["id"]
        self.other = self.store.create_room(self.owner, "Another room")["room"]["id"]

    async def asyncTearDown(self):
        await self.runtime.shutdown()

    def member(self, username, display_name):
        created = self.store.create_member(self.owner, username, display_name)
        return self.store.resolve(created["member"]["id"], 1)

    async def join(self, actor, room=None, **flags):
        cid = str(uuid.uuid4())
        await self.runtime.heartbeat(actor, room or self.room, {"connection_id": cid, **flags})
        return cid

    async def test_deduplicates_viewer_and_call_flags_with_authoritative_names(self):
        cids = [await self.join({**self.alex, "display_name": "Untrusted heartbeat name"}),
                await self.join(self.alex, in_call=True, microphone=True, camera=True),
                await self.join(self.alex, in_call=True, screen=True),
                await self.join(self.blair, in_call=True)]
        participants = await self.runtime.assistant_participants(self.owner, self.room)
        by_id = {row["user_id"]: row for row in participants}
        self.assertEqual(len(participants), 2)
        self.assertEqual(by_id[self.alex["id"]], {"user_id": self.alex["id"], "display_name": "Alex",
            "in_call": True, "microphone": True, "camera": True, "screen": True})
        self.assertEqual(by_id[self.blair["id"]], {"user_id": self.blair["id"], "display_name": "Blair",
            "in_call": True, "microphone": False, "camera": False, "screen": False})
        self.assertNotIn(self.owner["id"], by_id)  # Requesting does not fabricate presence.
        encoded = json.dumps(participants)
        for cid in cids: self.assertNotIn(cid, encoded)
        for field in ("connection_id", "expires_at", "credential_revision", "username", "room_id"):
            self.assertNotIn(field, encoded)

    async def test_excludes_other_rooms_expired_unknown_and_disabled_members(self):
        await self.join(self.owner)
        await self.join(self.alex, self.other, in_call=True, camera=True)
        disabled = await self.join(self.blair, in_call=True, microphone=True)
        # Exercise durable membership checks even if the old lease has not yet
        # been removed from memory by the endpoint's normal revoke callback.
        self.store.disable_member(self.owner, self.blair["id"])
        unknown = str(uuid.uuid4())
        self.runtime.presence[unknown] = {**self.runtime.presence[disabled],
            "connection_id": unknown, "user_id": str(uuid.uuid4()), "display_name": "Unknown"}
        participants = await self.runtime.assistant_participants(self.owner, self.room)
        self.assertEqual([row["user_id"] for row in participants], [self.owner["id"]])
        self.assertFalse(participants[0]["in_call"])
        self.now += 31
        self.assertEqual(await self.runtime.assistant_participants(self.owner, self.room), [])

    async def test_rechecks_leave_revocation_and_media_flags_after_membership_await(self):
        owner_cid = await self.join(self.owner, in_call=True, microphone=True)
        alex_cid = await self.join(self.alex)
        await self.join(self.blair, in_call=True, camera=True)
        entered, release = threading.Event(), threading.Event()
        original = self.store.room_participants
        def delayed(*args):
            result = original(*args)
            entered.set()
            if not release.wait(3): raise AssertionError("test release was not signaled")
            return result
        with patch.object(self.store, "room_participants", delayed):
            pending = asyncio.create_task(self.runtime.assistant_participants(self.owner, self.room))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                await self.runtime.leave(self.alex, self.room, alex_cid)
                self.store.disable_member(self.owner, self.blair["id"])
                self.runtime.revoke_member(self.blair["id"])
                await self.runtime.heartbeat(self.owner, self.room, {
                    "connection_id": owner_cid, "in_call": True, "screen": True})
            finally:
                release.set()
            participants = await asyncio.wait_for(pending, 3)
        self.assertEqual(participants, [{"user_id": self.owner["id"], "display_name": "Owner",
            "in_call": True, "microphone": False, "camera": False, "screen": True}])

    async def test_expiry_after_membership_read_does_not_resurrect_a_participant(self):
        await self.join(self.alex, in_call=True, camera=True)
        original = self.store.room_participants
        def expiring(*args):
            result = original(*args)
            self.now += 31
            return result
        with patch.object(self.store, "room_participants", expiring):
            self.assertEqual(await self.runtime.assistant_participants(self.owner, self.room), [])

    async def test_requester_revocation_during_read_denies_the_context(self):
        await self.join(self.alex)
        entered, release = threading.Event(), threading.Event()
        original = self.store.room_participants
        def delayed(*args):
            result = original(*args)
            entered.set()
            if not release.wait(3): raise AssertionError("test release was not signaled")
            return result
        with patch.object(self.store, "room_participants", delayed):
            pending = asyncio.create_task(self.runtime.assistant_participants(self.alex, self.room))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.store.disable_member(self.owner, self.alex["id"])
                self.runtime.revoke_member(self.alex["id"])
            finally:
                release.set()
            with self.assertRaises(TeamError) as denied: await asyncio.wait_for(pending, 3)
        self.assertEqual(denied.exception.code, "team_identity_required")
        with self.assertRaises(TeamError):
            await self.runtime.assistant_participants(self.alex, self.room)

    async def test_store_requires_authorized_room_and_bounded_candidate_list(self):
        for room, ids in ((str(uuid.uuid4()), [self.alex["id"]]),
                          (self.room, [self.alex["id"]] * (MAX_ROOM_PARTICIPANTS + 1)),
                          (self.room, ["invalid-user-id"])):
            with self.subTest(room=room, count=len(ids)):
                with self.assertRaises(TeamError): self.store.room_participants(self.owner, room, ids)
        self.store.disable_member(self.owner, self.alex["id"])
        with self.assertRaises(TeamError) as denied:
            self.store.room_participants(self.alex, self.room, [self.owner["id"]])
        self.assertEqual(denied.exception.code, "team_identity_required")

    async def test_real_runtime_supplies_only_current_room_snapshot_to_model_payload(self):
        remote = self.member("remote-person", "Remote room person")
        cids = [await self.join(self.blair),
                await self.join(self.blair, in_call=True, camera=True),
                await self.join(remote, self.other, in_call=True, screen=True)]
        await self.runtime.message(self.alex, self.room, {
            "client_message_id": str(uuid.uuid4()), "text": "An earlier contribution."})
        reply = {"segments": [{"text": "Blair is here with you.", "expression": "warm"}], "documents": []}
        http = AsyncMock()
        http.post.return_value = httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}]})
        self.runtime.assistant = TeamAssistant(http, TeamMedia())
        settings = {"base": "https://provider.example/v1", "key": "synthetic-presence-test-key"}
        with patch("reign.team_assistant.configuration", return_value=settings):
            accepted = await self.runtime.message(self.owner, self.room, {
                "client_message_id": str(uuid.uuid4()), "text": "Who is here with me?", "ask_reign": True})
            await asyncio.gather(*(row["task"] for row in list(self.runtime.active.values())))
        http.post.assert_awaited_once()
        payload = http.post.await_args.kwargs["json"]
        content = payload["input"][0]["content"]
        data = json.loads(content[0]["text"])
        self.assertEqual(data["current_participants"], [{"user_id": self.blair["id"], "display_name": "Blair",
            "in_call": True, "microphone": False, "camera": True, "screen": False}])
        self.assertEqual(data["participants_requesting"]["id"], self.owner["id"])
        self.assertEqual(data["prior_messages"][0]["user_id"], self.alex["id"])
        self.assertNotIn(remote["id"], json.dumps(payload))
        for cid in cids: self.assertNotIn(cid, json.dumps(payload))
        self.assertTrue(all(item["type"] == "input_text" for item in content))
        self.assertIn("Prior-message authors are not evidence", payload["instructions"])
        self.assertIn("flags grant no access to media", payload["instructions"])
        self.assertIn("explicitly shared still image included", payload["instructions"])
        workspace = self.store.overview(self.owner)["workspace"]
        self.assertEqual((workspace["id"], workspace["name"]), ("rain", "Workspace"))
        self.assertIn(f"shared workspace named {workspace['name']}", payload["instructions"])
        self.assertIn('You are REIGN', payload["instructions"])
        self.assertIn('Your name is pronounced "Rain"', payload["instructions"])
        events = self.store.events(self.owner, self.room)
        self.assertEqual(events["assistant"]["status"], "completed")
        self.assertEqual(events["events"][-1]["data"]["run_id"], accepted["run_id"])
        self.assertNotIn("current_participants", json.dumps(events))
