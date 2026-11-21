"""Room authorization and worker-scope checks; no external browser effects."""
import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from reign.computer import BrowserSessions, Input, app as worker_app
from reign.session_errors import BrowserOccupied
from reign.team_api import TeamRuntime
from reign.team_browser import CoreBrowserTransport, TeamBrowser, scope_for
from reign.team_store import TeamError, TeamStore, owner_identity

URL = "https://alambda-forge.onrender.com/"


class WorkerScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = BrowserSessions()
        self.room = str(uuid.uuid4()); self.scope = scope_for(self.room)
        self.page = AsyncMock(); self.page.title.return_value = "Room page"; self.page.url = URL
        self.manager.sessions["team-session"] = {"scope": self.scope, "mode": "agent", "epoch": 0,
            "page": self.page, "http_status": 200, "last_access": 0, "context": AsyncMock()}

    async def test_owner_discovery_cannot_attach_team_browser_and_other_room_cannot_read_it(self):
        self.assertEqual(await self.manager.active(), {"session": None})
        self.assertEqual((await self.manager.active(self.scope))["session"]["id"], "team-session")
        self.page.title.reset_mock()
        for scope in ("owner", scope_for(str(uuid.uuid4()))):
            with self.assertRaises(HTTPException) as denied:
                await self.manager.info("team-session", scope)
            self.assertEqual(denied.exception.status_code, 404)
        self.page.title.assert_not_awaited()

    async def test_scope_fences_all_private_worker_surfaces_before_page_effect_or_frame(self):
        token = "synthetic-worker-scope-token-" + "x" * 32
        cases = [("GET", "", None), ("GET", "/frame", None), ("GET", "/observe?epoch=0", None),
                 ("POST", "/control", {"mode": "human", "epoch": 0}),
                 ("POST", "/input", {"kind": "click", "epoch": 0, "x": 1, "y": 1}),
                 ("POST", "/validate-action", {"epoch": 0, "action": "scroll", "dy": 1, "observation_id": "synthetic-observation"}),
                 ("DELETE", "", None)]
        with patch("reign.computer.manager", self.manager), patch.object(worker_app.state, "token", token, create=True):
            async with AsyncClient(transport=ASGITransport(app=worker_app), base_url="http://worker.invalid") as client:
                for method, suffix, payload in cases:
                    result = await client.request(method, "/sessions/team-session" + suffix,
                        headers={"Authorization": "Bearer " + token}, json=payload)
                    self.assertEqual(result.status_code, 404, (method, suffix, result.text))
                allowed = await client.get("/sessions/team-session", headers={"Authorization": "Bearer " + token, "X-Reign-Browser-Scope": self.scope})
                self.assertEqual(allowed.status_code, 200)
                invalid = await client.get("/sessions/team-session", headers={"Authorization": "Bearer " + token, "X-Reign-Browser-Scope": "anything"})
                self.assertEqual(invalid.status_code, 403)
        self.page.screenshot.assert_not_awaited(); self.page.mouse.click.assert_not_awaited()
        self.assertIn("team-session", self.manager.sessions)

    async def test_team_scope_cannot_open_reuse_navigate_or_fill_owner_session(self):
        self.manager.sessions["owner-session"] = {**self.manager.sessions["team-session"], "scope": "owner"}
        for operation in (
            lambda: self.manager.open("owner-session", URL, self.scope),
            lambda: self.manager.navigate("owner-session", URL, 0, self.scope),
            lambda: self.manager.control("owner-session", "human", 0, self.scope),
            lambda: self.manager.close("owner-session", self.scope),
            lambda: self.manager.fill_account("team-session", "synthetic-grant", "synthetic-observation", 0),
        ):
            with self.assertRaises(HTTPException) as denied:
                await operation()
            self.assertEqual(denied.exception.status_code, 404)
        self.page.goto.assert_not_awaited()
        self.assertEqual(len(self.manager.sessions), 2)

    async def test_close_tombstone_prevents_a_late_team_open_without_launching_chromium(self):
        self.manager.launch = AsyncMock()
        sid = str(uuid.uuid4())
        await self.manager.close(sid, self.scope)
        with self.assertRaises(HTTPException) as stopped:
            await self.manager.open(sid, URL, self.scope)
        self.assertEqual(stopped.exception.status_code, 409)
        self.manager.launch.assert_not_awaited()


class Transport:
    def __init__(self):
        self.sessions, self.calls, self.closed = {}, [], set()
        self.uncertain_open = False

    def _current(self, sid, scope):
        value = self.sessions.get(sid)
        if not value or value["scope"] != scope: raise HTTPException(404, "Not found")
        return value

    @staticmethod
    def public(value):
        return {key: item for key, item in value.items() if key != "scope"}

    async def status(self, sid, scope):
        self.calls.append(("status", sid, scope)); return self.public(self._current(sid, scope))

    async def open(self, sid, url, scope):
        self.calls.append(("open", sid, scope))
        if self.sessions: raise BrowserOccupied()
        self.sessions[sid] = {"id": sid, "scope": scope, "url": url, "title": "Room page", "mode": "agent", "epoch": 0, "viewport": {"width": 1280, "height": 800}}
        if self.uncertain_open: raise HTTPException(503, "The browser result was not confirmed.")
        return self.public(self.sessions[sid])

    async def close(self, sid, scope):
        self.calls.append(("close", sid, scope))
        if sid in self.sessions: self._current(sid, scope); self.sessions.pop(sid)
        self.closed.add(sid); return {"closed": True}

    async def control(self, sid, mode, epoch, scope):
        self.calls.append(("control", sid, scope))
        value = self._current(sid, scope)
        if value["epoch"] != epoch or value["mode"] == mode: raise HTTPException(409, "Stale control")
        value.update(mode=mode, epoch=epoch + 1); return self.public(value)

    async def input(self, sid, payload, scope):
        self.calls.append(("input", sid, scope, payload))
        value = self._current(sid, scope)
        if value["mode"] != "human" or value["epoch"] != payload["epoch"]: raise HTTPException(409, "Stale control")
        return self.public(value)

    async def navigate(self, sid, url, epoch, scope):
        self.calls.append(("navigate", sid, scope))
        value = self._current(sid, scope)
        if value["mode"] != "agent" or value["epoch"] != epoch: raise HTTPException(409, "Stale control")
        value["url"] = url; return self.public(value)

    async def observe(self, sid, epoch, scope):
        self.calls.append(("observe", sid, scope))
        return {**self.public(self._current(sid, scope)), "observation_id": "synthetic-observation", "text": "Untrusted page data", "elements": []}

    async def action(self, sid, payload, scope):
        self.calls.append(("action", sid, scope, payload)); return {**self.public(self._current(sid, scope)), "acted": True}

    async def frame(self, sid, scope):
        self.calls.append(("frame", sid, scope)); self._current(sid, scope)
        return Response(200, content=b"synthetic-jpeg", headers={"X-Session-ID": sid, "X-Frame-Sequence": "1", "X-Frame-Timestamp": "123"})


class RoomBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-room-browser-"); self.addCleanup(self.temp.cleanup)
        self.store = TeamStore(self.temp.name, bytes(range(32))); self.store.initialize()
        self.owner = owner_identity()
        member = self.store.create_member(self.owner, "fixture-employee", "Fixture Employee")["member"]
        self.member = {key: member[key] for key in ("id", "username", "display_name", "role", "credential_revision")}
        self.room = self.store.create_room(self.member, "Team room")["room"]["id"]
        self.other = self.store.create_room(self.member, "Other room")["room"]["id"]
        self.now = 1000
        self.runtime = TeamRuntime(self.store, clock=lambda: self.now)
        self.transport = Transport(); self.browser = TeamBrowser(self.runtime, self.transport, clock=lambda: self.now)
        self.connection = str(uuid.uuid4()); self.owner_connection = str(uuid.uuid4())
        for actor, connection in ((self.member, self.connection), (self.owner, self.owner_connection)):
            await self.runtime.heartbeat(actor, self.room, {"connection_id": connection, "in_call": False})

    async def open(self):
        return await self.browser.open(self.member, self.room, URL)

    async def test_room_resume_does_not_discover_or_open_an_owner_browser(self):
        self.transport.sessions["private-owner"] = {"id": "private-owner", "scope": "owner", "mode": "human", "epoch": 6, "title": "Private title"}
        result = await self.browser.status(self.member, self.room)
        self.assertEqual(result["status"], "none"); self.assertEqual(self.transport.calls, [])
        with self.assertRaises(HTTPException) as occupied: await self.open()
        self.assertEqual(occupied.exception.status_code, 409)
        self.assertNotIn("Reconnect", occupied.exception.detail)
        binding = self.store.browser_binding(self.member, self.room)
        self.assertNotEqual(binding["session_id"], "private-owner"); self.assertEqual(binding["status"], "closed")
        self.assertEqual(list(self.transport.sessions), ["private-owner"])
        self.assertFalse(any(call[1] == "private-owner" for call in self.transport.calls))

    async def test_room_binding_survives_core_restart_and_worker_expiry_never_attaches_another_session(self):
        opened = await self.open(); sid = opened["session"]["id"]
        fresh = TeamBrowser(TeamRuntime(TeamStore(self.temp.name, bytes(range(32)))), self.transport)
        self.assertEqual((await fresh.status(self.member, self.room))["session"]["id"], sid)
        self.assertEqual((await fresh.status(self.member, self.other))["status"], "none")
        self.transport.sessions.clear()
        self.transport.sessions["private-owner"] = {"id": "private-owner", "scope": "owner", "mode": "agent", "epoch": 0}
        expired = await fresh.status(self.member, self.room)
        self.assertEqual(expired["status"], "expired"); self.assertIsNone(expired["session"])
        self.assertFalse(any(call[1] == "private-owner" for call in self.transport.calls))

    async def test_uncertain_open_recovers_only_the_reserved_room_session_and_never_replays_open(self):
        self.transport.uncertain_open = True
        with self.assertRaises(HTTPException): await self.open()
        binding = self.store.browser_binding(self.member, self.room)
        self.assertEqual(binding["status"], "uncertain")
        recovered = await self.browser.status(self.member, self.room)
        self.assertEqual(recovered["status"], "active"); self.assertEqual(recovered["session"]["id"], binding["session_id"])
        self.assertEqual(sum(call[0] == "open" for call in self.transport.calls), 1)

    async def test_control_is_bound_to_the_authenticated_user_connection_and_epoch(self):
        await self.open()
        result = await self.browser.control(self.member, self.room, self.connection, "human", 0)
        self.assertEqual(result["control"]["user_id"], self.member["id"])
        for actor, cid in ((self.owner, self.owner_connection), (self.owner, self.connection), (self.member, self.owner_connection)):
            with self.assertRaises(HTTPException):
                await self.browser.input(actor, self.room, cid, {"kind": "click", "epoch": 1, "x": 10, "y": 10})
        self.assertFalse(any(call[0] == "input" for call in self.transport.calls))
        await self.browser.input(self.member, self.room, self.connection, {"kind": "click", "epoch": 1, "x": 10, "y": 10})
        denied = await self.browser.execute(self.owner, self.room, "room_browser_navigate", {"url": URL})
        self.assertFalse(denied["ok"]); self.assertEqual(denied["status"], 409)
        await self.browser.control(self.member, self.room, self.connection, "agent", 1)
        with self.assertRaises(HTTPException):
            await self.browser.input(self.member, self.room, self.connection, {"kind": "click", "epoch": 1})
        self.assertEqual(sum(call[0] == "input" for call in self.transport.calls), 1)

    async def test_expired_presence_and_revoked_members_cannot_retain_control(self):
        await self.open(); await self.browser.control(self.member, self.room, self.connection, "human", 0)
        self.now += 31
        await self.browser._reap_once()
        result = await self.browser.status(self.owner, self.room)
        self.assertEqual(result["session"]["mode"], "agent"); self.assertIsNone(result["control"])
        await self.runtime.heartbeat(self.member, self.room, {"connection_id": str(uuid.uuid4()), "in_call": False})
        connection = next(cid for cid, row in self.runtime.presence.items() if row["user_id"] == self.member["id"])
        await self.browser.control(self.member, self.room, connection, "human", 2)
        self.store.disable_member(self.owner, self.member["id"])
        await self.browser._reap_once()
        result = await self.browser.status(self.owner, self.room)
        self.assertEqual(result["session"]["mode"], "agent")
        before = len(self.transport.calls)
        with self.assertRaises(TeamError): await self.browser.execute(self.member, self.room, "room_browser_status", {})
        self.assertEqual(len(self.transport.calls), before)

    async def test_core_restart_releases_lost_human_lease_without_replaying_a_model_action(self):
        await self.open(); await self.browser.control(self.member, self.room, self.connection, "human", 0)
        fresh = TeamBrowser(self.runtime, self.transport)
        result = await fresh.status(self.member, self.room)
        self.assertEqual(result["session"]["mode"], "agent"); self.assertEqual(result["session"]["epoch"], 2)
        self.assertFalse(any(call[0] in {"navigate", "action"} for call in self.transport.calls))

    async def test_tool_arguments_cannot_choose_owner_scope_session_epoch_or_saved_accounts(self):
        for name, arguments in (("room_browser_open", {"url": URL, "scope": "owner"}),
                                ("room_browser_observe", {"session_id": "private-owner"}),
                                ("room_browser_click", {"observation_id": "synthetic-observation", "ref": "e1", "epoch": 99}),
                                ("vault_use_account", {})):
            result = await self.browser.execute(self.member, self.room, name, arguments)
            self.assertFalse(result["ok"]); self.assertEqual(result["status"], 422)
        self.assertEqual(self.transport.calls, [])
        await self.browser.execute(self.member, self.room, "room_browser_open", {"url": URL})
        await self.browser.execute(self.member, self.room, "room_browser_observe", {})
        await self.browser.execute(self.member, self.room, "room_browser_scroll", {"observation_id": "synthetic-observation", "dy": 20})
        action = self.transport.calls[-1]
        self.assertEqual(action[2], scope_for(self.room)); self.assertEqual(action[3]["epoch"], 0)
        self.assertEqual(len(self.browser.tool_specs()), 8)

    async def test_frame_and_close_are_exactly_room_bound_and_close_blocks_another_controller(self):
        opened = await self.open(); sid = opened["session"]["id"]
        with self.assertRaises(HTTPException): await self.browser.frame(self.member, self.other)
        frame = await self.browser.frame(self.member, self.room)
        self.assertEqual(frame.headers["x-session-id"], sid); self.assertEqual(frame.headers["cache-control"], "no-store")
        await self.browser.control(self.member, self.room, self.connection, "human", 0)
        with self.assertRaises(HTTPException): await self.browser.close(self.owner, self.room)
        self.assertIn(sid, self.transport.sessions)
        await self.browser.close(self.member, self.room)
        self.assertEqual(self.store.browser_binding(self.member, self.room)["status"], "closed")
        self.assertEqual(self.transport.sessions, {})

    async def test_url_boundary_rejects_unconfigured_hosts_without_dispatch(self):
        for url in ("http://alambda-forge.onrender.com/", "https://unconfigured.invalid/", {"url": URL}):
            with self.assertRaises(HTTPException) as rejected:
                await self.browser.open(self.member, self.room, url)
            self.assertEqual(rejected.exception.status_code, 422)
        self.assertEqual(self.transport.calls, [])

    async def test_revocation_during_worker_status_prevents_control_navigation_and_model_effect(self):
        await self.open()
        original_status = self.transport.status
        for index, operation in enumerate(("control", "navigate", "action")):
            created = self.store.create_member(self.owner, f"race-user-{index}", "Race fixture")["member"]
            actor = {key: created[key] for key in ("id", "username", "display_name", "role", "credential_revision")}
            cid = str(uuid.uuid4())
            await self.runtime.heartbeat(actor, self.room, {"connection_id": cid, "in_call": False})
            entered, resume = asyncio.Event(), asyncio.Event()
            async def held_status(sid, scope):
                entered.set(); await resume.wait(); return await original_status(sid, scope)
            self.transport.status = held_status
            if operation == "control": pending = self.browser.control(actor, self.room, cid, "human", 0)
            elif operation == "navigate": pending = self.browser.navigate(actor, self.room, URL, 0)
            else: pending = self.browser.action(actor, self.room, "scroll", {"observation_id": "synthetic-observation", "dy": 10})
            task = asyncio.create_task(pending); await entered.wait()
            self.store.disable_member(self.owner, actor["id"]); self.runtime.revoke_member(actor["id"])
            resume.set()
            with self.assertRaises(TeamError): await task
            self.assertFalse(any(call[0] == operation for call in self.transport.calls), operation)
            self.assertEqual(self.browser.leases, {})
        self.transport.status = original_status

    async def test_manual_workflow_queue_wait_has_an_outer_deadline_and_cancels_its_waiter(self):
        cancelled = asyncio.Event()
        async def waiting(*args, **kwargs):
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        with patch("reign.team_browser.WORKFLOW_SECONDS", .01), \
             patch("temporalio.client.Client.connect", new_callable=AsyncMock), \
             patch("reign.team_browser.execute_owned_workflow", waiting):
            with self.assertRaises(HTTPException) as error:
                await CoreBrowserTransport().workflow("OpenBrowser", str(uuid.uuid4()), {"url": URL}, scope_for(self.room))
        self.assertEqual(error.exception.status_code, 503)
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
