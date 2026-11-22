"""Room-scoped continuous WebRTC calls; audio stays on the media connection.

Only negotiation and delegated requests pass through Core. A live viewer lease
owns each call. The provider call identifier and credential never reach AX, the
browser, or the archive. Text forwarded by the voice client is a spoken-request
representation, not a server-verified recording or a new source of authority.
"""
import asyncio
from contextlib import suppress
import hashlib
import time

from fastapi import Request

from reign.team_api import body, get_runtime, reply, require_actor
from reign.team_store import TeamError, identifier, text
from reign.voice import HANGUP_TIMEOUT_SECONDS, VoiceError, capabilities, valid_sdp

MAX_CALLS = 4
MAX_RETIRED = 512
MAX_CALL_SECONDS = 3600
RETIRED_SECONDS = 3600
SHUTDOWN_TIMEOUT_SECONDS = 18


class TeamVoice:
    def __init__(self, runtime, broker, *, clock=None):
        self.runtime, self.broker = runtime, broker
        self.clock = clock or time.time
        self.calls = {}
        self.retired = {}
        self.jobs = set()
        self.reaper = None
        self.closed = False
        self.shutdown_task = None
        self.shutdown_deadline = None
        self.shutdown_expired = False
        runtime.voice_sessions = self
        runtime.live_voice_validator = self.validate

    async def start(self):
        if self.reaper is None:
            self.reaper = asyncio.create_task(self._reap_loop())

    def _track(self, task):
        self.jobs.add(task)
        def completed(done):
            self.jobs.discard(done)
            if not done.cancelled(): done.exception()
        task.add_done_callback(completed)
        return task

    def _prune(self):
        now = self.clock()
        self.retired = {key: value for key, value in self.retired.items() if value["expires"] > now}

    def _connection(self, actor, room_id, connection_id):
        identifier(room_id); identifier(connection_id)
        self.runtime._prune()
        row = self.runtime.presence.get(connection_id)
        if (self.closed or self.runtime.closed or actor["id"] in self.runtime.revoked_members
                or not row or row["room_id"] != room_id or row["user_id"] != actor["id"]):
            raise TeamError("team_voice_closed")
        return row

    def validate(self, actor, room_id, session_id):
        identifier(session_id)
        row = self.calls.get(session_id)
        if (not row or row["state"] != "active" or row["room_id"] != room_id
                or row["actor"]["id"] != actor["id"]
                or row["actor"]["credential_revision"] != actor["credential_revision"]
                or self.clock() >= row["deadline"]):
            raise TeamError("team_voice_closed")
        self._connection(actor, room_id, row["connection_id"])
        return row

    async def offer(self, actor, room_id, connection_id, session_id, sdp):
        identifier(session_id)
        if not valid_sdp(sdp): raise VoiceError("voice_invalid_offer")
        await self.runtime.authorize_room(actor, room_id)
        self._connection(actor, room_id, connection_id)
        self._prune()
        if session_id in self.retired: raise TeamError("team_voice_closed")
        fingerprint = hashlib.sha256(sdp.encode()).hexdigest()
        row = self.calls.get(session_id)
        if row:
            if (row["actor"]["id"] != actor["id"] or row["room_id"] != room_id
                    or row["connection_id"] != connection_id or row["fingerprint"] != fingerprint
                    or row["state"] == "closing"):
                raise TeamError("team_voice_closed")
        else:
            # No await between capacity checks and reservation. Do not replace
            # another tab's open call or discard an unconfirmed provider close.
            if any(item["actor"]["id"] == actor["id"] and item["state"] == "closing"
                   for item in self.calls.values()):
                raise TeamError("team_voice_closing")
            if (len(self.calls) >= MAX_CALLS or len(self.retired) >= MAX_RETIRED
                    or any(item["actor"]["id"] == actor["id"] for item in self.calls.values())):
                raise TeamError("team_limit")
            row = {"id": session_id, "actor": dict(actor), "room_id": room_id,
                   "connection_id": connection_id, "fingerprint": fingerprint,
                   "state": "opening", "call_id": None, "closing_task": None,
                   "cancel_task": None, "cancel_run": None, "hangup_confirmed": False, "shutdown_attempts": 0,
                   "unknown_allocation": False, "offer_started": False,
                   "deadline": self.clock() + MAX_CALL_SECONDS}
            self.calls[session_id] = row
            row["task"] = self._track(asyncio.create_task(self._open(row, sdp)))
        # A disconnected HTTP client must not orphan a provider call whose
        # Location header arrives after the browser has already pressed Off.
        answer = await asyncio.shield(row["task"])
        self.validate(actor, room_id, session_id)
        return dict(answer)

    async def _open(self, row, sdp):
        try:
            if self.closed or row["state"] == "closing": raise TeamError("team_voice_closed")
            self._connection(row["actor"], row["room_id"], row["connection_id"])
            row["offer_started"] = True
            answer = await self.broker.offer(sdp, scope="team", include_call_id=True,
                                            on_call_id=lambda call_id: self._allocated(row, call_id))
            row["call_id"] = answer.pop("call_id")
            await self.runtime.authorize_room(row["actor"], row["room_id"])
            self._connection(row["actor"], row["room_id"], row["connection_id"])
            if row["state"] == "closing" or row["id"] in self.retired or self.closed:
                raise TeamError("team_voice_closed")
            row["state"] = "active"
            return {**answer, "voice_session_id": row["id"]}
        except BaseException as error:
            # Negotiation can create a provider call before an invalid SDP or
            # timeout is detected. If the broker could not close it, retain
            # the internal ID and capacity reservation for the lease reaper.
            if not row["call_id"]: row["call_id"] = getattr(error, "provider_call_id", None)
            if (self.closed and row["offer_started"] and not row["call_id"]
                    and (isinstance(error, asyncio.CancelledError) or
                         isinstance(error, VoiceError) and error.code in {
                             "voice_provider_timeout", "voice_provider_unavailable", "voice_invalid_answer"})):
                row["unknown_allocation"] = True
            self._retire(row)
            if row["call_id"]:
                with suppress(Exception): await self._hangup(row)
            elif not row["unknown_allocation"]:
                self.calls.pop(row["id"], None)
            raise

    def _allocated(self, row, call_id):
        # Never serialized: the browser knows only its own session UUID. The
        # ID must be retained as soon as headers arrive, even if the SDP stalls.
        row["call_id"] = call_id
        row["unknown_allocation"] = False
        if row["state"] == "closing" and not self.shutdown_expired:
            self._track(asyncio.create_task(self._hangup(row)))
        if self.closed and not row["task"].done():
            row["task"].cancel()

    def _retire(self, row):
        row["state"] = "closing"
        self.retired[row["id"]] = {"actor_id": row["actor"]["id"], "room_id": row["room_id"],
                                    "expires": self.clock() + RETIRED_SECONDS}

    async def _hangup(self, row):
        if self.shutdown_expired: return row["hangup_confirmed"]
        if row["cancel_task"] is None: self._cancel_task(row)
        if row["hangup_confirmed"]: return True
        if not row["call_id"]: return False
        task = row["closing_task"]
        if task is None or task.done():
            if self.closed:
                if row["shutdown_attempts"] >= 2: return False
                remaining = self.shutdown_deadline - asyncio.get_running_loop().time()
                if remaining <= 0: return False
                row["shutdown_attempts"] += 1
            async def finish():
                if self.closed:
                    async with asyncio.timeout_at(self.shutdown_deadline):
                        await self.broker.hangup(row["call_id"])
                else:
                    await self.broker.hangup(row["call_id"])
                row["hangup_confirmed"] = True
                self._release(row)
                return True
            task = row["closing_task"] = self._track(asyncio.create_task(finish()))
        return await asyncio.shield(task)

    async def _cancel_turn(self, row):
        # Acceptance owns this lock through the durable commit and dispatch.
        # Marking the call closed before acquiring it prevents a new request
        # racing Off from starting after this cancellation check.
        async with self.runtime.lock:
            active = self.runtime.active.get(row["room_id"])
            if (active and active.get("voice_session_id") == row["id"]
                    and active["actor"] == row["actor"]["id"]):
                row["cancel_run"] = active
            active = row["cancel_run"]
            task = active["task"] if active else None
            if task: task.cancel()
        if task: await asyncio.gather(task, return_exceptions=True)
        if task:
            # Verify durable settlement even if the worker already removed its
            # in-memory row. fail_run is idempotent for completed/failed runs.
            # Retain this exact run on transient storage failure for a retry.
            async with self.runtime.lock:
                await asyncio.to_thread(self.runtime.store.fail_run, row["room_id"], active["run_id"],
                    "assistant_interrupted" if self.closed or self.runtime.closed else "assistant_failed")
                if self.runtime.active.get(row["room_id"]) is active:
                    self.runtime.active.pop(row["room_id"], None)

    def _release(self, row):
        task = row["cancel_task"]
        if (row["hangup_confirmed"] and task is not None and task.done()
                and not task.cancelled() and task.exception() is None
                and self.calls.get(row["id"]) is row):
            self.calls.pop(row["id"], None)

    def _cancel_task(self, row):
        task = row["cancel_task"]
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = row["cancel_task"] = self._track(asyncio.create_task(self._cancel_turn(row)))
            task.add_done_callback(lambda done: self._release(row))
        return task

    async def _close(self, row):
        self._retire(row)
        # Stop the provider even when cancellation or durable run settlement is
        # slow. Both operations are owned and observed; neither replays work.
        results = await asyncio.gather(asyncio.shield(self._cancel_task(row)), self._hangup(row),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException): raise result
        # A still-opening task owns its eventual hangup; never cancel it just
        # because the initiating HTTP request ended before its answer arrived.

    async def close(self, actor, room_id, session_id):
        identifier(session_id)
        await self.runtime.authorize_room(actor, room_id)
        self._prune()
        row = self.calls.get(session_id)
        retired = self.retired.get(session_id)
        if row:
            if row["actor"]["id"] != actor["id"] or row["room_id"] != room_id:
                raise TeamError("team_voice_closed")
            await self._close(row)
        else:
            if retired and (retired["actor_id"] != actor["id"] or retired["room_id"] != room_id):
                raise TeamError("team_voice_closed")
            if not retired and len(self.retired) >= MAX_RETIRED: raise TeamError("team_limit")
            # DELETE may beat an in-flight offer to Core. Remember it so that
            # the delayed offer cannot recreate a microphone call after Off.
            self.retired[session_id] = {"actor_id": actor["id"], "room_id": room_id,
                                         "expires": self.clock() + RETIRED_SECONDS}
        return {"status": "ended"}

    async def turn(self, actor, room_id, session_id, client_message_id, message):
        identifier(session_id); identifier(client_message_id)
        text(message, 4000, bytes_max=16000)
        # Runtime resolves a durable duplicate before validating the live call.
        # A retry after Off may confirm old work, but can never start new work.
        return await self.runtime.message(actor, room_id, {
            "client_message_id": client_message_id, "text": message,
            "ask_reign": True, "speak": True, "attachment_ids": [],
            "input_source": "voice", "voice_session_id": session_id})

    def revoke_member(self, user_id):
        for row in list(self.calls.values()):
            if row["actor"]["id"] == user_id:
                self._retire(row)
                self._track(asyncio.create_task(self._close(row)))

    async def close_connection(self, actor, room_id, connection_id):
        rows = [row for row in self.calls.values() if row["actor"]["id"] == actor["id"]
                and row["room_id"] == room_id and row["connection_id"] == connection_id]
        for row in rows: self._retire(row)
        if rows: await asyncio.gather(*(self._close(row) for row in rows), return_exceptions=True)

    async def reap(self):
        self._prune()
        expired = []
        for row in list(self.calls.values()):
            try:
                self._connection(row["actor"], row["room_id"], row["connection_id"])
                if row["state"] == "closing" or self.clock() >= row["deadline"]: raise TeamError("team_voice_closed")
            except TeamError:
                self._retire(row); expired.append(row)
        if expired: await asyncio.gather(*(self._close(row) for row in expired), return_exceptions=True)
        # The provider's documented maximum is one hour. Failed hangups remain
        # reserved until then, rather than silently allowing unbounded calls.
        for key, row in list(self.calls.items()):
            if self.clock() >= row["deadline"] + 30 and row["task"].done(): self.calls.pop(key, None)

    async def _reap_loop(self):
        while not self.closed:
            await asyncio.sleep(5)
            await self.reap()

    async def shutdown(self):
        if self.shutdown_task is None:
            self.closed = True
            self.shutdown_deadline = asyncio.get_running_loop().time() + SHUTDOWN_TIMEOUT_SECONDS
            rows = list(self.calls.values())
            for row in rows:
                self._retire(row)
                row["shutdown_attempts"] = int(row["closing_task"] is not None
                                                 and not row["closing_task"].done())
            self.shutdown_task = asyncio.create_task(self._shutdown(rows))
        await asyncio.shield(self.shutdown_task)

    async def _shutdown(self, rows):
        # Render's disk-backed Core has a 30s platform window. Uvicorn drains
        # requests for 2s first; voice uses at most 18s and leaves time for the
        # remaining Core resources. This is best-effort remote cleanup, never
        # proof that an unacknowledged allocation or failed hangup ended.
        loop = asyncio.get_running_loop()
        margin = min(0.1, SHUTDOWN_TIMEOUT_SECONDS / 10)
        work_deadline = self.shutdown_deadline - margin
        allocation_cutoff = work_deadline - min(HANGUP_TIMEOUT_SECONDS, SHUTDOWN_TIMEOUT_SECONDS / 2)
        reaper, self.reaper = self.reaper, None
        if reaper: reaper.cancel()
        try:
            initial = set()
            for row in rows:
                self._cancel_task(row)
                if row["call_id"]:
                    initial.add(self._track(asyncio.create_task(self._hangup(row))))
                    if not row["task"].done(): row["task"].cancel()
                if not row["task"].done(): initial.add(row["task"])
            # Give unknown allocations a bounded chance to yield their ID,
            # reserving one complete provider-close interval afterward.
            if initial:
                await asyncio.wait(initial, timeout=max(0, allocation_cutoff - loop.time()))
            for row in rows:
                if not row["task"].done():
                    row["unknown_allocation"] = not bool(row["call_id"])
                    row["task"].cancel()
                if row["call_id"] and not row["hangup_confirmed"]:
                    self._track(asyncio.create_task(self._hangup(row)))
            # At most one retry per call and all calls share the same deadline.
            # Re-read the owned set because a cancelled offer may start cleanup.
            while loop.time() < work_deadline:
                pending = {task for task in self.jobs if not task.done()}
                if reaper and not reaper.done(): pending.add(reaper)
                if not pending: break
                await asyncio.wait(pending, timeout=max(0, work_deadline - loop.time()))
        finally:
            self.shutdown_expired = True
            pending = {task for task in self.jobs if not task.done()}
            if reaper and not reaper.done(): pending.add(reaper)
            for task in pending: task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=max(0, self.shutdown_deadline - loop.time()))


def service(request):
    value = getattr(request.app.state, "team_voice", None)
    if value is None: raise TeamError("assistant_unavailable")
    return value


def install(app):
    @app.get("/api/team/rooms/{room_id}/voice")
    async def status(room_id: str, request: Request):
        actor = await require_actor(request)
        await get_runtime(request).authorize_room(actor, room_id)
        configured = service(request)
        value = capabilities()
        return reply({**value, "configured": value["configured"] and not configured.closed,
                      "orchestration": "shared_room", "max_duration_seconds": MAX_CALL_SECONDS})

    @app.post("/api/team/rooms/{room_id}/voice/offer")
    async def offer(room_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"sdp", "connection_id", "client_session_id"},
                           {"sdp", "connection_id", "client_session_id"}, 96 * 1024)
        return reply(await service(request).offer(actor, room_id, value["connection_id"], value["client_session_id"], value["sdp"]))

    @app.delete("/api/team/rooms/{room_id}/voice/sessions/{session_id}")
    async def close(room_id: str, session_id: str, request: Request):
        actor = await require_actor(request)
        return reply(await service(request).close(actor, room_id, session_id))

    @app.post("/api/team/rooms/{room_id}/voice/sessions/{session_id}/turns")
    async def turn(room_id: str, session_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"client_message_id", "text"}, {"client_message_id", "text"}, 18000)
        return reply(await service(request).turn(actor, room_id, session_id, value["client_message_id"], value["text"]))
