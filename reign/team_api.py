"""Member-authorized room APIs, bounded presence, and ephemeral call signaling."""
import asyncio
from collections import deque
from contextlib import suppress
import json
import os
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from reign.team_identity import ACTOR_HEADER, OWNER_ID, TeamIdentityError, verify_actor
from reign.team_store import MAX_ROOM_PARTICIPANTS, TeamError, TeamStore, boolean, identifier, text

MAX_PRESENCE = 256
MAX_ROOM_PRESENCE = MAX_ROOM_PARTICIPANTS
MAX_USER_CONNECTIONS = 4
MAX_CALL_CONNECTIONS = 4
PRESENCE_TTL = 30
SIGNAL_TTL = 60
MAX_SIGNALS = 512
MAX_TOMBSTONES = 2048
ASSISTANT_TIMEOUT = 150


def reply(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


async def error_response(request, error):
    return reply({"code": error.code, "detail": error.detail}, error.status)


def fields(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise TeamError("invalid_team_request")
    return value


async def body(request, allowed, required=(), maximum=70000):
    data = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > maximum: raise TeamError("invalid_team_request")
                data.extend(chunk)
        value = json.loads(data)
    except (ValueError, UnicodeError, TimeoutError):
        raise TeamError("invalid_team_request") from None
    return fields(value, allowed, required)


def get_runtime(request):
    runtime = getattr(request.app.state, "team", None)
    if runtime is None: raise TeamError("team_unavailable")
    return runtime


async def require_actor(request):
    # Root-owned attachment/audio routes use this exact boundary too.
    authorize = getattr(request.app.state, "team_service_authorize", None)
    if authorize is None: raise TeamError("team_unavailable")
    authorize(request)
    if set(request.query_params) & {"actor", "actor_id", "user_id", "owner_id", "role", "workspace_id"}:
        raise TeamError("invalid_team_request")
    runtime = get_runtime(request)
    try:
        claims = verify_actor(request.headers.get(ACTOR_HEADER, ""))
    except TeamIdentityError:
        raise TeamError("team_identity_required") from None
    actor = await asyncio.to_thread(runtime.store.resolve, claims["sub"], claims["rev"])
    if actor["role"] != claims["role"]: raise TeamError("team_identity_required")
    return actor


class TeamRuntime:
    def __init__(self, store, assistant=None, *, clock=None):
        self.store, self.assistant = store, assistant
        self.attachment_validator = None
        self.clock = clock or time.time
        self.features = {"group_chat": True, "documents": True, "calls": True,
                         "shared_browser": False, "remote_desktop": False,
                         "camera_to_ai": False, "screen_to_ai": False, "voice_output": False,
                         "speech": False, "vision": False}
        self.presence = {}
        self.revoked_members = set()
        self.tombstones = {}
        self.signals = []
        self.signal_seq = {}
        self.active = {}
        self.accepting = set()
        self.lock = asyncio.Lock()
        self.login_attempts = deque()
        self.pending_auth = 0
        self.closed = False

    async def start(self):
        await asyncio.to_thread(self.store.initialize)

    def capabilities(self, actor=None):
        return {**self.features, "assistant": self.assistant is not None,
                "manage_members": bool(actor and actor["role"] == "owner"),
                "relay_configured": False, "max_call_participants": MAX_CALL_CONNECTIONS}

    async def authorize_room(self, actor, room_id):
        return await asyncio.to_thread(self.store.authorize_room, actor, room_id)

    def _prune(self):
        now = self.clock()
        self.tombstones = {cid: row for cid, row in self.tombstones.items() if row["expires"] > now}
        self.presence = {cid: value for cid, value in self.presence.items() if value["expires_at"] > now}
        self.signals = [value for value in self.signals if value["expires"] > now
                        and value["from"] in self.presence and value["to"] in self.presence]

    def presence_rows(self, room_id):
        self._prune()
        return [{key: value for key, value in row.items() if key != "room_id"}
                for row in self.presence.values() if row["room_id"] == room_id]

    async def assistant_participants(self, actor, room_id):
        """A bounded membership-checked snapshot, never a camera observation."""
        self._prune()
        user_ids = sorted({row["user_id"] for row in self.presence.values()
                           if row["room_id"] == room_id and row["user_id"] not in self.revoked_members})[:MAX_ROOM_PARTICIPANTS]
        members = await asyncio.to_thread(self.store.room_participants, actor, room_id, user_ids)
        # A leave, expiry, media toggle or revocation may have happened while
        # storage ran. Use the live rows again, not copies made before the await.
        self._prune()
        if self.closed: raise TeamError("team_unavailable")
        if actor["id"] in self.revoked_members: raise TeamError("team_identity_required")
        participants = {}
        for row in self.presence.values():
            user_id = row["user_id"]
            if row["room_id"] != room_id or user_id not in members or user_id in self.revoked_members:
                continue
            participant = participants.setdefault(user_id, {
                "user_id": user_id, "display_name": members[user_id],
                "in_call": False, "microphone": False, "camera": False, "screen": False})
            # Viewer and call connections share an identity; muted calls still
            # count as joined, and media flags describe only current call rows.
            if row["in_call"]:
                participant["in_call"] = True
                for flag in ("microphone", "camera", "screen"):
                    participant[flag] = participant[flag] or row[flag]
        return [participants[user_id] for user_id in sorted(participants)]

    async def heartbeat(self, actor, room_id, data):
        await self.authorize_room(actor, room_id)
        # Authorization may have completed in the worker thread just before a
        # concurrent revocation removed this user's connections. Membership
        # revocation is permanent in this bounded 32-member first workspace.
        if actor["id"] in self.revoked_members: raise TeamError("team_identity_required")
        cid = identifier(data["connection_id"])
        flags = {name: boolean(data.get(name, False)) for name in ("in_call", "microphone", "camera", "screen")}
        if not flags["in_call"] and any(flags[name] for name in ("microphone", "camera", "screen")):
            raise TeamError("invalid_team_request")
        self._prune()
        if cid in self.tombstones: raise TeamError("team_connection_required")
        current = self.presence.get(cid)
        if current and (current["user_id"] != actor["id"] or current["room_id"] != room_id):
            raise TeamError("team_connection_required")
        if current is None and len(self.tombstones) >= MAX_TOMBSTONES:
            raise TeamError("team_limit")
        others = [row for key, row in self.presence.items() if key != cid]
        room = [row for row in others if row["room_id"] == room_id]
        if (len(others) >= MAX_PRESENCE or len(room) >= MAX_ROOM_PRESENCE
                or sum(row["user_id"] == actor["id"] for row in room) >= MAX_USER_CONNECTIONS):
            raise TeamError("team_limit")
        if flags["in_call"] and sum(row["in_call"] for row in room) >= MAX_CALL_CONNECTIONS:
            raise TeamError("team_call_full")
        self.presence[cid] = {"room_id": room_id, "connection_id": cid, "user_id": actor["id"],
                              "display_name": actor["display_name"], **flags, "expires_at": self.clock() + PRESENCE_TTL}
        if current and current["in_call"] and not flags["in_call"]:
            self.signals = [signal for signal in self.signals if cid not in (signal["from"], signal["to"])]
        return {"presence": self.presence_rows(room_id)}

    async def leave(self, actor, room_id, connection_id):
        await self.authorize_room(actor, room_id)
        identifier(connection_id); self._prune()
        current = self.presence.get(connection_id)
        retired = self.tombstones.get(connection_id)
        if retired and (retired["user_id"] != actor["id"] or retired["room_id"] != room_id):
            raise TeamError("team_connection_required")
        if current and (current["user_id"] != actor["id"] or current["room_id"] != room_id):
            raise TeamError("team_connection_required")
        self.presence.pop(connection_id, None)
        # An already-sent heartbeat may arrive after DELETE. Never let that
        # stale request rejoin a call; each explicit join uses a fresh UUID.
        # At capacity all new presence is refused until old tombstones expire.
        if connection_id in self.tombstones or len(self.tombstones) < MAX_TOMBSTONES:
            self.tombstones[connection_id] = {"room_id": room_id, "user_id": actor["id"], "expires": self.clock() + 60}
        self.signals = [signal for signal in self.signals if connection_id not in (signal["from"], signal["to"])]
        return {"presence": self.presence_rows(room_id)}

    def _connection(self, actor, room_id, connection_id):
        identifier(connection_id); self._prune()
        row = self.presence.get(connection_id)
        if not row or row["user_id"] != actor["id"] or row["room_id"] != room_id or not row["in_call"]:
            raise TeamError("team_connection_required")
        return row

    @staticmethod
    def signal_payload(kind, payload):
        if kind in {"offer", "answer"}:
            fields(payload, {"type", "sdp"}, {"type", "sdp"})
            if payload["type"] != kind: raise TeamError("invalid_team_request")
            text(payload["sdp"], 16384, bytes_max=16384)
        elif kind == "ice":
            if payload is None: return None
            fields(payload, {"candidate", "sdpMid", "sdpMLineIndex", "usernameFragment"}, {"candidate"})
            text(payload["candidate"], 4096, bytes_max=4096, empty=True)
            for field, maximum in (("sdpMid", 64), ("usernameFragment", 256)):
                if payload.get(field) is not None: text(payload[field], maximum, empty=True)
            if payload.get("sdpMLineIndex") is not None and (type(payload["sdpMLineIndex"]) is not int or not 0 <= payload["sdpMLineIndex"] <= 8):
                raise TeamError("invalid_team_request")
        else:
            raise TeamError("invalid_team_request")
        return payload

    async def send_signal(self, actor, room_id, data):
        await self.authorize_room(actor, room_id)
        sender = self._connection(actor, room_id, data["connection_id"])
        target = self.presence.get(identifier(data["to"]))
        if not target or not target["in_call"] or target["room_id"] != room_id or target["connection_id"] == sender["connection_id"]:
            raise TeamError("team_connection_required")
        # A removed recipient cannot continue receiving signaling for the
        # remainder of its old heartbeat lease.
        target_identity = await asyncio.to_thread(self.store.overview, actor)
        if target["user_id"] not in {member["id"] for member in target_identity["members"] if member.get("active")}:
            raise TeamError("team_connection_required")
        payload = self.signal_payload(data["kind"], data["payload"])
        self._prune()
        # Recheck after the storage await; another request may have left.
        self._connection(actor, room_id, sender["connection_id"])
        target = self.presence.get(data["to"])
        if not target or not target["in_call"] or target["room_id"] != room_id: raise TeamError("team_connection_required")
        if len(self.signals) >= MAX_SIGNALS or sum(signal["to"] == data["to"] for signal in self.signals) >= 128:
            raise TeamError("team_limit")
        seq = self.signal_seq.get(room_id, 0) + 1; self.signal_seq[room_id] = seq
        self.signals.append({"room_id": room_id, "seq": seq, "from": sender["connection_id"], "to": data["to"],
                             "kind": data["kind"], "payload": payload, "expires": self.clock() + SIGNAL_TTL})
        return {"accepted": True, "seq": seq}

    async def read_signals(self, actor, room_id, connection_id, after):
        await self.authorize_room(actor, room_id)
        self._connection(actor, room_id, connection_id)
        if type(after) is not int or after < 0: raise TeamError("invalid_team_request")
        found = [{key: row[key] for key in ("seq", "from", "kind", "payload")} for row in self.signals
                 if row["room_id"] == room_id and row["to"] == connection_id and row["seq"] > after]
        return {"signals": found, "last_seq": self.signal_seq.get(room_id, 0)}

    @staticmethod
    def message_data(data):
        fields(data, {"client_message_id", "text", "ask_reign", "speak", "attachment_ids"}, {"client_message_id", "text"})
        result = {"client_message_id": identifier(data["client_message_id"]), "text": text(data["text"], 4000, bytes_max=16000),
                  "ask_reign": boolean(data.get("ask_reign", False)), "speak": boolean(data.get("speak", False)),
                  "attachment_ids": data.get("attachment_ids", [])}
        if type(result["attachment_ids"]) is not list or len(result["attachment_ids"]) > 4: raise TeamError("invalid_team_request")
        result["attachment_ids"] = [identifier(value) for value in result["attachment_ids"]]
        if len(set(result["attachment_ids"])) != len(result["attachment_ids"]) or result["speak"] and not result["ask_reign"]:
            raise TeamError("invalid_team_request")
        return result

    async def message(self, actor, room_id, data):
        data = self.message_data(data)
        if self.closed: raise TeamError("team_unavailable")
        if len(self.accepting) >= 16: raise TeamError("team_limit")
        # A client disconnect must not separate a durable run receipt from
        # dispatching its worker. Bound and retain the acceptance operation.
        task = asyncio.create_task(self._accept_message(actor, room_id, data))
        self.accepting.add(task)
        def completed(done):
            self.accepting.discard(done)
            if not done.cancelled(): done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _accept_message(self, actor, room_id, data):
        async with self.lock:
            if self.closed: raise TeamError("team_unavailable")
            found = await asyncio.to_thread(self.store.message_receipt, actor, room_id, data)
            if found: return {key: found[key] for key in ("event", "run_id")}
            if data["attachment_ids"]:
                if self.attachment_validator is None: raise TeamError("assistant_unavailable")
                await self.attachment_validator(actor, room_id, data["attachment_ids"])
            if data["ask_reign"]:
                if self.assistant is None: raise TeamError("assistant_unavailable")
                if room_id in self.active: raise TeamError("assistant_busy")
                if len(self.active) >= 2: raise TeamError("team_limit")
            receipt = await asyncio.to_thread(self.store.append_message, actor, room_id, data)
            if receipt["run_id"] and not receipt["duplicate"]:
                task = asyncio.create_task(self._run(actor, room_id, receipt["run_id"], data))
                self.active[room_id] = {"task": task, "actor": actor["id"], "run_id": receipt["run_id"]}
            return {key: receipt[key] for key in ("event", "run_id")}

    @staticmethod
    def assistant_result(value):
        fields(value, {"text", "segments", "audio_id", "documents", "speech_status"}, {"text"})
        result = {"text": text(value["text"], 12000)}
        if "speech_status" in value:
            if value["speech_status"] not in {"ready", "unavailable", "not_requested"}: raise TeamError("assistant_failed")
            result["speech_status"] = value["speech_status"]
        if "segments" in value:
            if type(value["segments"]) is not list or not 1 <= len(value["segments"]) <= 12: raise TeamError("assistant_failed")
            for segment in value["segments"]:
                fields(segment, {"text", "expression", "audio_id"}, {"text", "expression"})
                text(segment["text"], 4000)
                if segment["expression"] not in {"neutral", "warm", "thoughtful", "concerned"}: raise TeamError("assistant_failed")
                if segment.get("audio_id") is not None: TeamRuntime.audio_id(segment["audio_id"])
            result["segments"] = value["segments"]
        if value.get("audio_id") is not None: result["audio_id"] = TeamRuntime.audio_id(value["audio_id"])
        if "documents" in value:
            if type(value["documents"]) is not list or len(value["documents"]) > 4: raise TeamError("assistant_failed")
            for document in value["documents"]:
                fields(document, {"title", "content"}, {"title", "content"})
                text(document["title"], 120); text(document["content"], 65536, bytes_max=65536, empty=True)
            result["documents"] = value["documents"]
        return result

    @staticmethod
    def audio_id(value):
        if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
            raise TeamError("assistant_failed")
        return value

    async def _run(self, actor, room_id, run_id, data):
        try:
            context = await asyncio.to_thread(self.store.room_context, actor, room_id, run_id)
            async with asyncio.timeout(ASSISTANT_TIMEOUT):
                context["current_participants"] = await self.assistant_participants(actor, room_id)
                result = await self.assistant(context, actor, data["text"], data["attachment_ids"], data["speak"])
            await asyncio.to_thread(self.store.finish_run, actor, room_id, run_id, self.assistant_result(result))
        except asyncio.CancelledError:
            with suppress(Exception): await asyncio.to_thread(self.store.fail_run, room_id, run_id, "assistant_interrupted" if self.closed else "assistant_failed")
        except Exception:
            with suppress(Exception): await asyncio.to_thread(self.store.fail_run, room_id, run_id, "assistant_failed")
        finally:
            self.active.pop(room_id, None)

    def revoke_member(self, user_id):
        self.revoked_members.add(user_id)
        removed = {cid for cid, row in self.presence.items() if row["user_id"] == user_id}
        self.presence = {cid: row for cid, row in self.presence.items() if cid not in removed}
        self.signals = [row for row in self.signals if row["from"] not in removed and row["to"] not in removed]
        for active in self.active.values():
            if active["actor"] == user_id: active["task"].cancel()

    async def shutdown(self):
        self.closed = True
        # Allow commits already in progress to attach their workers before
        # cancelling those workers and recording an interrupted result.
        if self.accepting: await asyncio.gather(*tuple(self.accepting), return_exceptions=True)
        tasks = [value["task"] for value in self.active.values()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        self.presence.clear(); self.signals.clear(); self.signal_seq.clear(); self.tombstones.clear(); self.revoked_members.clear()

    def admit_login(self):
        now = self.clock()
        while self.login_attempts and self.login_attempts[0] <= now - 60: self.login_attempts.popleft()
        if self.pending_auth >= 2 or len(self.login_attempts) >= 40: raise TeamError("team_limit")
        self.login_attempts.append(now); self.pending_auth += 1


def install(app, authorize):
    raw_authorize = authorize
    def authorize(request):
        try: return raw_authorize(request)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
            raise
    app.state.team_service_authorize = authorize
    app.add_exception_handler(TeamError, error_response)

    @app.post("/internal/team/authenticate")
    async def authenticate(request: Request):
        authorize(request); runtime = get_runtime(request)
        runtime.admit_login(); reserved = True
        try:
            value = await body(request, {"username", "password"}, {"username", "password"}, 4096)
            work = asyncio.create_task(asyncio.to_thread(runtime.store.authenticate, value["username"], value["password"]))
            try: identity = await asyncio.shield(work)
            except asyncio.CancelledError:
                def release(done):
                    runtime.pending_auth -= 1
                    if not done.cancelled(): done.exception()
                work.add_done_callback(release); reserved = False
                raise
            return reply({"identity": identity})
        finally:
            if reserved: runtime.pending_auth -= 1

    @app.post("/internal/team/identity")
    async def resolve_identity(request: Request):
        authorize(request); runtime = get_runtime(request)
        value = await body(request, {"id", "credential_revision"}, {"id", "credential_revision"}, 2048)
        identifier(value["id"])
        if type(value["credential_revision"]) is not int or value["credential_revision"] < 1: raise TeamError("team_identity_required")
        return reply({"identity": await asyncio.to_thread(runtime.store.resolve, value["id"], value["credential_revision"])})

    @app.get("/api/team")
    async def overview(request: Request):
        actor = await require_actor(request); runtime = get_runtime(request)
        result = await asyncio.to_thread(runtime.store.overview, actor)
        return reply({**result, "capabilities": runtime.capabilities(actor)})

    @app.post("/api/team/members")
    async def create_member(request: Request):
        actor = await require_actor(request); runtime = get_runtime(request)
        if actor["role"] != "owner": raise TeamError("team_owner_required")
        value = await body(request, {"username", "display_name"}, {"username", "display_name"}, 2048)
        return reply(await asyncio.to_thread(runtime.store.create_member, actor, value["username"], value["display_name"]), 201)

    @app.patch("/api/team/members/{user_id}")
    async def disable_member(user_id: str, request: Request):
        actor = await require_actor(request); runtime = get_runtime(request)
        if actor["role"] != "owner" or user_id == OWNER_ID: raise TeamError("team_owner_required")
        value = await body(request, {"active"}, {"active"}, 1024)
        if value["active"] is not False: raise TeamError("invalid_team_request")
        result = await asyncio.to_thread(runtime.store.disable_member, actor, user_id)
        runtime.revoke_member(user_id)
        return reply(result)

    @app.post("/api/team/rooms")
    async def create_room(request: Request):
        actor = await require_actor(request); value = await body(request, {"title"}, {"title"}, 2048)
        return reply(await asyncio.to_thread(get_runtime(request).store.create_room, actor, value["title"]), 201)

    @app.get("/api/team/rooms/{room_id}")
    async def room(room_id: str, request: Request):
        actor = await require_actor(request); runtime = get_runtime(request)
        result = await asyncio.to_thread(runtime.store.events, actor, room_id)
        return reply({**result, "presence": runtime.presence_rows(room_id)})

    @app.get("/api/team/rooms/{room_id}/events")
    async def events(room_id: str, request: Request):
        actor = await require_actor(request); runtime = get_runtime(request)
        if set(request.query_params) - {"after", "wait"}: raise TeamError("invalid_team_request")
        try:
            after = int(request.query_params.get("after", "0")); wait = float(request.query_params.get("wait", "0"))
            if after < 0 or not 0 <= wait <= 20: raise ValueError()
        except ValueError: raise TeamError("invalid_team_request") from None
        deadline = time.monotonic() + wait
        def presence_signature():
            # Renewing the lease is not a visible state change. Joining,
            # leaving, and toggling a media source should wake the existing
            # room wait so viewers do not keep displaying stale mic/camera flags.
            return tuple(sorted((row["connection_id"], row["user_id"], row["display_name"],
                row["in_call"], row["microphone"], row["camera"], row["screen"])
                for row in runtime.presence_rows(room_id)))
        initial_presence = presence_signature()
        while True:
            result = await asyncio.to_thread(runtime.store.events, actor, room_id, after)
            if (result["events"] or presence_signature() != initial_presence
                    or time.monotonic() >= deadline or await request.is_disconnected()): break
            await asyncio.sleep(max(0, min(0.25, deadline - time.monotonic())))
        # Reload current membership immediately before returning after a wait.
        await asyncio.to_thread(runtime.store.resolve, actor["id"], actor["credential_revision"])
        return reply({key: result[key] for key in ("events", "last_seq", "assistant")} | {"presence": runtime.presence_rows(room_id)})

    @app.post("/api/team/rooms/{room_id}/messages")
    async def message(room_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"client_message_id", "text", "ask_reign", "speak", "attachment_ids"}, {"client_message_id", "text"}, 18000)
        return reply(await get_runtime(request).message(actor, room_id, value))

    @app.post("/api/team/rooms/{room_id}/presence")
    async def presence(room_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"connection_id", "in_call", "microphone", "camera", "screen"}, {"connection_id"}, 2048)
        return reply(await get_runtime(request).heartbeat(actor, room_id, value))

    @app.delete("/api/team/rooms/{room_id}/presence/{connection_id}")
    async def leave(room_id: str, connection_id: str, request: Request):
        actor = await require_actor(request)
        return reply(await get_runtime(request).leave(actor, room_id, connection_id))

    @app.get("/api/team/call-config")
    async def call_config(request: Request):
        await require_actor(request)
        # Public STUN only supplies NAT candidates; restricted networks still
        # require a separately configured TURN relay, which is not provided.
        servers = [] if os.getenv("REIGN_TEAM_STUN_DISABLED") == "1" else [{"urls": "stun:stun.cloudflare.com:3478"}]
        return reply({"ice_servers": servers, "relay_configured": False, "max_participants": MAX_CALL_CONNECTIONS})

    @app.post("/api/team/rooms/{room_id}/signals")
    async def send_signal(room_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"connection_id", "to", "kind", "payload"}, {"connection_id", "to", "kind", "payload"}, 24000)
        return reply(await get_runtime(request).send_signal(actor, room_id, value))

    @app.get("/api/team/rooms/{room_id}/signals")
    async def signals(room_id: str, request: Request):
        actor = await require_actor(request)
        if set(request.query_params) - {"connection_id", "after"}: raise TeamError("invalid_team_request")
        try: after = int(request.query_params.get("after", "0"))
        except ValueError: raise TeamError("invalid_team_request") from None
        return reply(await get_runtime(request).read_signals(actor, room_id, request.query_params.get("connection_id"), after))

    @app.post("/api/team/rooms/{room_id}/documents")
    async def create_document(room_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"title", "content", "client_document_id"}, {"title", "content", "client_document_id"})
        result = await asyncio.to_thread(get_runtime(request).store.save_document, actor, room_id, value["title"], value["content"], client_id=value["client_document_id"])
        return reply(result, 201)

    @app.put("/api/team/rooms/{room_id}/documents/{document_id}")
    async def update_document(room_id: str, document_id: str, request: Request):
        actor = await require_actor(request)
        value = await body(request, {"title", "content", "expected_version"}, {"title", "content", "expected_version"})
        result = await asyncio.to_thread(get_runtime(request).store.save_document, actor, room_id, value["title"], value["content"], document_id=document_id, expected_version=value["expected_version"])
        return reply(result)

    @app.get("/api/team/rooms/{room_id}/documents/{document_id}")
    async def document(room_id: str, document_id: str, request: Request):
        actor = await require_actor(request)
        return reply(await asyncio.to_thread(get_runtime(request).store.document, actor, room_id, document_id))
