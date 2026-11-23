"""Continuous room voice negotiation, authorization, and lifecycle contracts."""
import asyncio
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from reign.team_api import TeamRuntime, install as install_team
from reign.team_identity import ACTOR_HEADER, issue_actor
from reign.team_store import TeamError, TeamStore, owner_identity
from reign.team_voice import TeamVoice, install as install_voice
from reign.voice import VoiceBroker, VoiceError, voice_error_response

SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
TOKEN = "synthetic-team-live-voice-service-token-12345678"


def uid(): return str(uuid.uuid4())


class LiveVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="reign-live-room-")
        self.addCleanup(self.directory.cleanup)
        self.now = 1000.0
        self.store = TeamStore(self.directory.name, bytes(range(32)))
        self.runtime = TeamRuntime(self.store, clock=lambda: self.now)
        await self.runtime.start()
        self.owner = owner_identity()
        self.room = self.store.create_room(self.owner, "Synthetic live voice room")["room"]["id"]
        self.other_room = self.store.create_room(self.owner, "Other room")["room"]["id"]
        member = self.store.create_member(self.owner, "live-voice-fixture", "Synthetic member")["member"]
        self.member = {key: member[key] for key in ("id", "username", "display_name", "role", "credential_revision")}
        self.connection, self.session = uid(), uid()
        self.broker = AsyncMock()
        self.broker.offer.side_effect = lambda *args, **kwargs: {
            "sdp": SDP, "type": "answer", "model": "synthetic-model", "max_duration_seconds": 3600,
            "call_id": "rtc_server_only_fixture"}
        self.voice = TeamVoice(self.runtime, self.broker, clock=lambda: self.now)
        self.contexts = []
        async def assistant(context, actor, message, attachment_ids, speak):
            self.contexts.append((context, actor, message, speak))
            return {"text": "I heard your spoken request.", "segments": [
                {"text": "I heard your spoken request.", "expression": "warm"}], "speech_status": "not_requested"}
        self.runtime.assistant = assistant
        await self.presence()

    async def asyncTearDown(self):
        await self.voice.shutdown()
        await self.runtime.shutdown()

    async def presence(self, actor=None, room=None, connection=None):
        return await self.runtime.heartbeat(actor or self.owner, room or self.room,
            {"connection_id": connection or self.connection})

    async def open(self, actor=None, room=None, connection=None, session=None):
        return await self.voice.offer(actor or self.owner, room or self.room,
            connection or self.connection, session or self.session, SDP)

    async def drain(self):
        tasks = [row["task"] for row in self.runtime.active.values()]
        if tasks: await asyncio.gather(*tasks)

    async def test_same_stream_spans_multiple_turns_and_persists_spoken_source(self):
        result = await self.open()
        self.assertEqual(result["voice_session_id"], self.session)
        self.assertEqual(result["max_duration_seconds"], 3600)
        self.assertNotIn("call_id", result)
        self.assertEqual(self.broker.offer.await_args.kwargs["scope"], "team")
        self.assertTrue(self.broker.offer.await_args.kwargs["include_call_id"])
        self.assertTrue(callable(self.broker.offer.await_args.kwargs["on_call_id"]))
        for message in ("Can you hear me?", "Now help me plan the next step."):
            accepted = await self.voice.turn(self.owner, self.room, self.session, uid(), message)
            self.assertEqual(accepted["event"]["data"]["input_source"], "voice")
            self.assertNotIn("transcription_id", accepted["event"]["data"])
            await self.drain()
        self.assertEqual(len(self.contexts), 2)
        self.assertTrue(all(row[0]["voice_session_id"] == self.session and row[0]["input_source"] == "voice" for row in self.contexts))
        self.assertTrue(all(row[3] for row in self.contexts))
        events = self.store.events(self.owner, self.room)["events"]
        replies = [event for event in events if event["type"] == "assistant_message"]
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(event["data"]["voice_session_id"] == self.session for event in replies))
        self.assertTrue(all(event["data"]["segments"][0]["expression"] == "warm" for event in replies))
        self.broker.offer.assert_awaited_once()
        self.broker.hangup.assert_not_awaited()
        # Neither twenty seconds nor fifteen minutes is an utterance cutoff.
        for advance in (21, 1000):
            self.now += advance
            await self.presence()
            await self.voice.reap()
            self.voice.validate(self.owner, self.room, self.session)

    async def test_duplicate_offer_uses_one_provider_call_and_rejects_changed_binding(self):
        first, second = await asyncio.gather(self.open(), self.open())
        self.assertEqual(first, second)
        self.broker.offer.assert_awaited_once()
        with self.assertRaises(TeamError):
            await self.voice.offer(self.owner, self.room, self.connection, self.session, SDP + "a=sendrecv\r\n")
        with self.assertRaises(TeamError): await self.open(self.member)
        with self.assertRaises(TeamError): await self.open(room=self.other_room)

    async def test_off_before_offer_is_a_tombstone_and_never_opens_provider(self):
        await self.voice.close(self.owner, self.room, self.session)
        with self.assertRaises(TeamError): await self.open()
        self.broker.offer.assert_not_awaited()

    async def test_off_during_negotiation_closes_late_provider_call(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set(); await finish.wait()
            return {"sdp": SDP, "type": "answer", "call_id": "rtc_late_fixture", "model": "synthetic", "max_duration_seconds": 3600}
        self.broker.offer.side_effect = delayed
        opening = asyncio.create_task(self.open())
        await started.wait()
        await self.voice.close(self.owner, self.room, self.session)
        with self.assertRaises(TeamError) as pending:
            await self.open(session=uid())
        self.assertEqual(pending.exception.code, "team_voice_closing")
        self.assertEqual(pending.exception.status, 409)
        self.broker.offer.assert_awaited_once()
        finish.set()
        with self.assertRaises(TeamError): await opening
        self.broker.hangup.assert_awaited_with("rtc_late_fixture")
        self.assertNotIn(self.session, self.voice.calls)
        await self.open(session=uid())
        self.assertEqual(self.broker.offer.await_count, 2)

    async def test_off_before_negotiation_task_starts_never_opens_provider(self):
        with patch.object(self.runtime, "authorize_room", new_callable=AsyncMock):
            opening = asyncio.create_task(self.open())
            await asyncio.sleep(0)
            self.assertIn(self.session, self.voice.calls)
            await self.voice.close(self.owner, self.room, self.session)
            with self.assertRaises(TeamError): await opening
        self.broker.offer.assert_not_awaited()

    async def test_disconnected_offer_request_does_not_lose_late_cleanup(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set(); await finish.wait()
            return {"sdp": SDP, "type": "answer", "call_id": "rtc_disconnected_fixture", "model": "synthetic"}
        self.broker.offer.side_effect = delayed
        opening = asyncio.create_task(self.open())
        await started.wait(); opening.cancel()
        with self.assertRaises(asyncio.CancelledError): await opening
        await self.voice.close(self.owner, self.room, self.session)
        background = self.voice.calls[self.session]["task"]
        finish.set(); await asyncio.gather(background, return_exceptions=True)
        self.broker.hangup.assert_awaited_with("rtc_disconnected_fixture")

    async def test_room_and_actor_bound_turns_and_durable_retry_after_off(self):
        await self.open()
        mid, message = uid(), "Can you hear me?"
        accepted = await self.voice.turn(self.owner, self.room, self.session, mid, message)
        await self.drain()
        for actor, room, session in ((self.member, self.room, self.session),
                                     (self.owner, self.other_room, self.session),
                                     (self.owner, self.room, uid())):
            with self.assertRaises(TeamError): await self.voice.turn(actor, room, session, uid(), message)
        await self.voice.close(self.owner, self.room, self.session)
        self.assertEqual(await self.voice.turn(self.owner, self.room, self.session, mid, message), accepted)
        self.assertEqual(len(self.contexts), 1)
        with self.assertRaises(TeamError): await self.voice.turn(self.owner, self.room, self.session, uid(), message)
        with self.assertRaises(TeamError): await self.voice.turn(self.owner, self.room, self.session, mid, "Different message")

    async def test_off_cancels_only_its_own_live_run(self):
        entered = asyncio.Event()
        async def blocked(*args):
            entered.set(); await asyncio.Event().wait()
        self.runtime.assistant = blocked
        await self.open()
        receipt = await self.voice.turn(self.owner, self.room, self.session, uid(), "A spoken request")
        await entered.wait()
        await self.voice.close(self.owner, self.room, self.session)
        self.assertNotIn(self.room, self.runtime.active)
        self.assertEqual(self.store.events(self.owner, self.room)["assistant"]["status"], "failed")
        self.assertTrue(any(event["type"] == "assistant_error" and event["data"]["run_id"] == receipt["run_id"]
                            for event in self.store.events(self.owner, self.room)["events"]))
        entered.clear()
        await self.runtime.message(self.owner, self.room, {"client_message_id": uid(), "text": "A typed task", "ask_reign": True})
        await entered.wait()
        await self.voice.close(self.owner, self.room, self.session)
        self.assertFalse(self.runtime.active[self.room]["task"].done())

    async def test_expired_presence_and_room_leave_close_provider(self):
        await self.open()
        self.now += 31
        await self.voice.reap()
        self.assertNotIn(self.session, self.voice.calls)
        self.broker.hangup.assert_awaited_once()
        self.session, self.connection = uid(), uid()
        await self.presence(); await self.open()
        await self.runtime.leave(self.owner, self.room, self.connection)
        self.assertNotIn(self.session, self.voice.calls)
        self.assertEqual(self.broker.hangup.await_count, 2)

    async def test_revoked_member_loses_call_and_cannot_submit_new_turn(self):
        self.connection = uid()
        await self.presence(self.member)
        await self.open(self.member)
        self.store.disable_member(self.owner, self.member["id"])
        self.runtime.revoke_member(self.member["id"])
        with self.assertRaises(TeamError): self.voice.validate(self.member, self.room, self.session)
        await asyncio.gather(*tuple(self.voice.jobs), return_exceptions=True)
        self.broker.hangup.assert_awaited_once()
        with self.assertRaises(TeamError): await self.voice.turn(self.member, self.room, self.session, uid(), "Still here")

    async def test_failed_hangup_retains_reservation_for_reaper_retry(self):
        await self.open()
        self.broker.hangup.side_effect = VoiceError("voice_provider_unavailable")
        with self.assertRaises(VoiceError): await self.voice.close(self.owner, self.room, self.session)
        self.assertEqual(self.voice.calls[self.session]["state"], "closing")
        with self.assertRaises(TeamError): await self.open(session=uid())
        self.broker.hangup.side_effect = None
        await self.voice.reap()
        self.assertNotIn(self.session, self.voice.calls)

    async def test_failed_negotiation_cleanup_retains_internal_call_for_retry(self):
        error = VoiceError("voice_invalid_answer")
        error.provider_call_id = "rtc_failed_negotiation_fixture"
        self.broker.offer.side_effect = error
        self.broker.hangup.side_effect = VoiceError("voice_provider_unavailable")
        with self.assertRaises(VoiceError): await self.open()
        self.assertEqual(self.voice.calls[self.session]["state"], "closing")
        self.assertEqual(self.voice.calls[self.session]["call_id"], error.provider_call_id)
        with self.assertRaises(TeamError): await self.open(session=uid())
        self.broker.hangup.side_effect = None
        await self.voice.reap()
        self.broker.hangup.assert_awaited_with("rtc_failed_negotiation_fixture")
        self.assertNotIn(self.session, self.voice.calls)

    async def test_confirmed_hangup_retains_failed_durable_cancellation_for_reaper_retry(self):
        await self.open()
        # Drive acceptance directly so cancellation can occur before the
        # separately scheduled worker gets its first instruction.
        payload = self.runtime.message_data({"client_message_id": uid(), "text": "A pending spoken task",
            "ask_reign": True, "speak": True, "attachment_ids": [],
            "input_source": "voice", "voice_session_id": self.session})
        receipt = await self.runtime._accept_message(self.owner, self.room, payload)
        task = self.runtime.active[self.room]["task"]
        task.cancel()  # Cancel before its coroutine can enter its own finally.
        await asyncio.gather(task, return_exceptions=True)
        self.assertIn(self.room, self.runtime.active)
        fail_run = self.store.fail_run
        attempts = 0
        def transient_failure(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1: raise OSError("synthetic temporary storage failure")
            return fail_run(*args)
        with patch.object(self.store, "fail_run", side_effect=transient_failure):
            with self.assertRaises(OSError):
                await self.voice.close(self.owner, self.room, self.session)
            row = self.voice.calls[self.session]
            self.assertTrue(row["hangup_confirmed"])
            self.assertIn(self.room, self.runtime.active)
            self.broker.hangup.assert_awaited_once()
            await self.voice.reap()
        self.assertNotIn(self.session, self.voice.calls)
        self.assertNotIn(self.room, self.runtime.active)
        self.broker.hangup.assert_awaited_once()
        errors = [event for event in self.store.events(self.owner, self.room)["events"]
                  if event["type"] == "assistant_error" and event["data"]["run_id"] == receipt["run_id"]]
        self.assertEqual(len(errors), 1)

    async def test_shutdown_closes_all_known_calls_without_waiting_for_room_cancellation(self):
        await self.open()
        member_connection, member_session = uid(), uid()
        await self.presence(self.member, connection=member_connection)
        self.broker.offer.side_effect = lambda *args, **kwargs: {
            "sdp": SDP, "type": "answer", "model": "synthetic", "call_id": "rtc_member_fixture"}
        await self.open(self.member, connection=member_connection, session=member_session)
        all_started, cancellation_release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        started = set()
        async def hangup(call_id):
            started.add(call_id)
            if len(started) == 2: all_started.set()
            await all_started.wait()
        async def slow_cancel(row):
            try: await cancellation_release.wait()
            finally: cancelled.set()
        self.broker.hangup.side_effect = hangup
        loop = asyncio.get_running_loop()
        begin = loop.time()
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.3), \
                patch.object(self.voice, "_cancel_turn", side_effect=slow_cancel):
            shutting_down = asyncio.create_task(self.voice.shutdown())
            await asyncio.wait_for(all_started.wait(), 0.2)
            self.assertTrue(self.voice.closed)
            self.assertTrue(all(row["state"] == "closing" for row in self.voice.calls.values()))
            self.assertEqual(len(self.voice.calls), 2, "Pending room settlement must retain its reservation")
            cancellation_release.set()
            await asyncio.wait_for(shutting_down, 0.7)
        self.assertLess(loop.time() - begin, 0.7)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.voice.calls)
        self.assertEqual(self.broker.hangup.await_count, 2)
        await self.voice.shutdown()
        self.assertEqual(self.broker.hangup.await_count, 2, "Confirmed closes must not be repeated")

    async def test_shutdown_retries_failed_close_once_and_keeps_failure_unconfirmed(self):
        await self.open()
        self.broker.hangup.side_effect = VoiceError("voice_provider_unavailable")
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.2):
            await asyncio.wait_for(self.voice.shutdown(), 0.5)
        self.assertEqual(self.broker.hangup.await_count, 2)
        row = self.voice.calls[self.session]
        self.assertEqual(row["state"], "closing")
        self.assertFalse(row["hangup_confirmed"])

    async def test_shutdown_retry_can_confirm_close_without_an_extra_successful_hangup(self):
        await self.open()
        self.broker.hangup.side_effect = [VoiceError("voice_provider_unavailable"), True]
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.2):
            await asyncio.wait_for(self.voice.shutdown(), 0.5)
        self.assertFalse(self.voice.calls)
        self.assertEqual(self.broker.hangup.await_count, 2)
        await self.voice.shutdown()
        self.assertEqual(self.broker.hangup.await_count, 2)

    async def test_shutdown_deadline_cancels_unresponsive_provider_without_claiming_success(self):
        await self.open()
        started = asyncio.Event()
        async def unresponsive(call_id):
            started.set(); await asyncio.Event().wait()
        self.broker.hangup.side_effect = unresponsive
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.12):
            await asyncio.wait_for(self.voice.shutdown(), 0.5)
        self.assertTrue(started.is_set())
        self.assertFalse(self.voice.calls[self.session]["hangup_confirmed"])
        self.assertTrue(all(task.done() for task in self.voice.jobs))

    async def test_shutdown_bounds_unknown_allocation_and_retains_unconfirmed_reservation(self):
        started = asyncio.Event()
        async def no_headers(*args, **kwargs):
            started.set(); await asyncio.Event().wait()
        self.broker.offer.side_effect = no_headers
        opening = asyncio.create_task(self.open())
        await started.wait()
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.12):
            await asyncio.wait_for(self.voice.shutdown(), 0.5)
        with self.assertRaises(asyncio.CancelledError): await opening
        row = self.voice.calls[self.session]
        self.assertEqual(row["state"], "closing")
        self.assertTrue(row["unknown_allocation"])
        self.assertFalse(row["hangup_confirmed"])
        self.broker.hangup.assert_not_awaited()

    async def test_shutdown_retains_natural_unconfirmed_offer_failure_before_cutoff(self):
        started, fail = asyncio.Event(), asyncio.Event()
        async def unknown_failure(*args, **kwargs):
            started.set(); await fail.wait()
            raise VoiceError("voice_provider_timeout")
        self.broker.offer.side_effect = unknown_failure
        opening = asyncio.create_task(self.open())
        await started.wait()
        with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.2):
            shutting_down = asyncio.create_task(self.voice.shutdown())
            await asyncio.sleep(0)
            fail.set()
            with self.assertRaises(VoiceError): await opening
            await asyncio.wait_for(shutting_down, 0.5)
        row = self.voice.calls[self.session]
        self.assertTrue(row["unknown_allocation"])
        self.assertFalse(row["hangup_confirmed"])
        self.broker.hangup.assert_not_awaited()

    async def test_provider_location_before_stalled_sdp_is_closed_once_during_shutdown(self):
        body_started, body_closed = asyncio.Event(), asyncio.Event()
        requests = []
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                body_started.set()
                await asyncio.Event().wait()
                yield SDP.encode()
            async def aclose(self): body_closed.set()
        def provider(request):
            requests.append(request.url.path)
            if request.url.path.endswith("/hangup"): return httpx.Response(204)
            return httpx.Response(201, headers={"location": "/v1/realtime/calls/rtc_slow_sdp"}, stream=SlowBody())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-shutdown-provider-key",
                                     "OPENAI_BASE_URL": "https://voice.example.test/v1"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
                self.voice.broker = VoiceBroker(http)
                opening = asyncio.create_task(self.open())
                await asyncio.wait_for(body_started.wait(), 0.5)
                self.assertEqual(self.voice.calls[self.session]["call_id"], "rtc_slow_sdp")
                with patch("reign.team_voice.SHUTDOWN_TIMEOUT_SECONDS", 0.3):
                    await asyncio.wait_for(self.voice.shutdown(), 0.6)
                with self.assertRaises(asyncio.CancelledError): await opening
        self.assertTrue(body_closed.is_set())
        self.assertFalse(self.voice.calls)
        self.assertEqual(requests, ["/v1/realtime/calls", "/v1/realtime/calls/rtc_slow_sdp/hangup"])

    async def test_public_routes_require_identity_and_only_voice_route_supplies_origin(self):
        def authorize(request):
            if request.headers.get("authorization") != "Bearer " + TOKEN: raise HTTPException(401, "Unauthorized")
        app = FastAPI()
        app.state.team, app.state.team_voice = self.runtime, self.voice
        app.add_exception_handler(VoiceError, voice_error_response)
        install_team(app, authorize); install_voice(app)
        with patch.dict(os.environ, {"CORE_TOKEN": TOKEN}):
            headers = {"Authorization": "Bearer " + TOKEN, ACTOR_HEADER: issue_actor(self.owner["id"], "owner", 0)}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://core.test") as client:
                prefix = "/api/team/rooms/" + self.room
                requests = [("GET", prefix + "/voice", None), ("POST", prefix + "/voice/offer", {}),
                    ("DELETE", prefix + "/voice/sessions/" + self.session, None),
                    ("POST", prefix + "/voice/sessions/" + self.session + "/turns", {})]
                for method, path, data in requests:
                    denied = await client.request(method, path, json=data)
                    self.assertEqual(denied.status_code, 401)
                forged = await client.post(prefix + "/messages", headers=headers, json={
                    "client_message_id": uid(), "text": "Spoofed origin", "input_source": "voice", "voice_session_id": self.session})
                self.assertEqual(forged.status_code, 422)
                offer = await client.post(prefix + "/voice/offer", headers=headers, json={
                    "sdp": SDP, "connection_id": self.connection, "client_session_id": self.session})
                self.assertEqual(offer.status_code, 200)
                self.assertNotIn("call_id", offer.json())
                sent = await client.post(prefix + "/voice/sessions/" + self.session + "/turns", headers=headers,
                    json={"client_message_id": uid(), "text": "Spoken through the live model"})
                self.assertEqual(sent.status_code, 200)
                self.assertEqual(sent.headers["cache-control"], "no-store")
                await self.drain()
                ended = await client.delete(prefix + "/voice/sessions/" + self.session, headers=headers)
                self.assertEqual(ended.json(), {"status": "ended"})


if __name__ == "__main__": unittest.main()
