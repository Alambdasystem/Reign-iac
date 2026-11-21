"""Room-owned managed browsing; no owner-session discovery or saved accounts.

The durable binding is metadata, not a durable Chromium process. Every effect
uses the existing destination boundary, Temporal one-attempt workflow, fresh
observation and Computer ownership epoch. Human control is a live room-connection
lease, never a model argument or a restored browser cookie.
"""
import asyncio
from contextlib import suppress
import os
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from reign.config import checked_url, temporal_address, NAMESPACE, QUEUE
from reign.owned_workflow import execute_owned_workflow
from reign.team_api import body, fields, reply, require_actor
from reign.team_store import TeamError, identifier
from reign.tool_contracts import CATALOG
from reign.session_errors import BrowserOccupied
from reign.tool_errors import BrowserActionError


TOOLS = {
    "room_browser_status": "browser_status", "room_browser_open": "open_browser",
    "room_browser_navigate": "navigate_browser", "room_browser_observe": "browser_observe",
    "room_browser_click": "browser_click", "room_browser_fill": "browser_fill",
    "room_browser_press": "browser_press", "room_browser_scroll": "browser_scroll",
}
WORKFLOW_SECONDS = 60


def tool_specs():
    result = []
    for public, original in TOOLS.items():
        description, schema = CATALOG[original]
        if original == "browser_fill":
            description = "Fill an ordinary field from the latest room-browser observation. Never supply passwords, OTPs, keys or other credentials. No saved-account tools are available."
        result.append({"type": "function", "name": public,
                       "description": "This room's shared browser only. " + description,
                       "parameters": schema.model_json_schema(), "strict": False})
    return result


def scope_for(room_id):
    return "team:rain:" + identifier(room_id)


def epoch_value(value):
    if type(value) is not int or not 0 <= value < 2**53:
        raise TeamError("invalid_team_request")
    return value


class CoreBrowserTransport:
    async def request(self, method, sid, suffix="", payload=None, *, scope):
        from reign.core import worker
        room_id = scope.removeprefix("team:rain:")
        if scope != scope_for(room_id):
            raise HTTPException(503, "The room browser scope could not be confirmed.")
        return await worker(method, f"/team-sessions/{room_id}/{sid}{suffix}", payload,
                            scope=scope, team_protocol=True)

    async def status(self, sid, scope):
        return (await self.request("GET", sid, scope=scope)).json()

    async def workflow(self, name, sid, payload, scope):
        from temporalio.client import Client
        from reign.core import workflow_http_error
        if name not in {"TeamOpenBrowser", "TeamNavigateBrowser", "TeamBrowserAction"}:
            raise HTTPException(503, "The room browser workflow protocol is unavailable.")
        try:
            # Activity start-to-close deadlines do not bound queue waiting.
            # Leave enough room inside AX's 75s request budget for the owned
            # workflow's shielded cancellation cleanup after this deadline.
            async with asyncio.timeout(WORKFLOW_SECONDS):
                client = await Client.connect(temporal_address(), namespace=NAMESPACE)
                return await execute_owned_workflow(client, name, {"id": sid, "scope": scope, **payload},
                    id="reign-team-" + str(uuid.uuid4()), task_queue=QUEUE)
        except Exception as error:
            raise workflow_http_error(error, uncertain=True) from None

    async def open(self, sid, url, scope):
        return await self.workflow("TeamOpenBrowser", sid, {"url": checked_url(url)}, scope)

    async def navigate(self, sid, url, epoch, scope):
        return await self.workflow("TeamNavigateBrowser", sid, {"url": checked_url(url), "epoch": epoch}, scope)

    async def observe(self, sid, epoch, scope):
        return (await self.request("GET", sid, f"/observe?epoch={epoch}", scope=scope)).json()

    async def action(self, sid, payload, scope):
        from reign.core import BrowserEffectRequest
        data = BrowserEffectRequest.model_validate(payload).model_dump(exclude_none=True, exclude_unset=True)
        await self.request("POST", sid, "/validate-action", {key: value for key, value in data.items() if key != "text"}, scope=scope)
        return await self.workflow("TeamBrowserAction", sid, data, scope)

    async def control(self, sid, mode, epoch, scope):
        return (await self.request("POST", sid, "/control", {"mode": mode, "epoch": epoch}, scope=scope)).json()

    async def input(self, sid, payload, scope):
        return (await self.request("POST", sid, "/input", payload, scope=scope)).json()

    async def frame(self, sid, scope):
        return await self.request("GET", sid, "/frame", scope=scope)

    async def close(self, sid, scope):
        return (await self.request("DELETE", sid, scope=scope)).json()


class TeamBrowser:
    tool_specs = staticmethod(tool_specs)

    def __init__(self, runtime, transport=None, *, clock=None):
        self.runtime, self.store = runtime, runtime.store
        self.transport = transport or CoreBrowserTransport()
        self.clock = clock or time.time
        self.leases, self.locks = {}, {}
        self.task = None

    async def start(self):
        self.runtime.features["shared_browser"] = True
        self.task = asyncio.create_task(self._reap())

    async def _authorize(self, actor, room_id):
        await self.runtime.authorize_room(actor, room_id)

    async def _binding(self, actor, room_id):
        return await asyncio.to_thread(self.store.browser_binding, actor, room_id)

    async def _update(self, actor, room_id, sid, status):
        return await asyncio.to_thread(self.store.update_browser, actor, room_id, sid, status)

    async def _lock(self, actor, room_id):
        await self._authorize(actor, room_id)
        return self.locks.setdefault(room_id, asyncio.Lock())

    def _connection(self, actor, room_id, connection_id):
        identifier(connection_id)
        self.runtime._prune()
        row = self.runtime.presence.get(connection_id)
        if not row or row["user_id"] != actor["id"] or row["room_id"] != room_id:
            raise HTTPException(409, "Keep this room open before taking browser control.")
        return row

    async def _valid_lease(self, room_id):
        lease = self.leases.get(room_id)
        if lease is None:
            return None
        try:
            await self._authorize(lease["actor"], room_id)
            presence = self._connection(lease["actor"], room_id, lease["connection_id"])
        except (HTTPException, TeamError):
            return None
        lease["expires_at"] = presence["expires_at"]
        return lease

    async def _state(self, actor, room_id, binding):
        if not binding or binding["status"] in {"closed", "expired"}:
            return None
        try:
            current = await self.transport.status(binding["session_id"], scope_for(room_id))
        except HTTPException as error:
            if error.status_code != 404:
                raise
            if binding["status"] == "active":
                await self._update(actor, room_id, binding["session_id"], "expired")
                binding["status"] = "expired"
            return None
        if current.get("id") != binding["session_id"]:
            raise HTTPException(503, "The room browser identity could not be confirmed.")
        return current

    async def _release_stale(self, room_id, binding, current):
        lease = await self._valid_lease(room_id)
        if current and current["mode"] == "human" and lease is None:
            # A vanished tab/revoked member or Core restart cannot leave a human
            # lease available for somebody else to impersonate. Handback only
            # fences old input; it never resumes a cancelled model action.
            current = await self.transport.control(binding["session_id"], "agent", current["epoch"], scope_for(room_id))
        if current is None or current["mode"] != "human":
            self.leases.pop(room_id, None)
        return current

    def _public(self, binding, current, room_id):
        lease = self.leases.get(room_id) if current and current["mode"] == "human" else None
        control = None if not lease else {key: lease[key] for key in
            ("user_id", "display_name", "connection_id", "epoch", "expires_at")}
        return {"status": binding["status"] if binding else "none", "binding": binding,
                "session": current, "control": control, "single_slot": True,
                "allowed_hosts": sorted(host for host in os.getenv("ALLOWED_HOSTS", "alambda-forge.onrender.com").split(",") if host)}

    async def status(self, actor, room_id):
        async with await self._lock(actor, room_id):
            binding = await self._binding(actor, room_id)
            current = await self._state(actor, room_id, binding)
            current = await self._release_stale(room_id, binding, current)
            if current and binding["status"] in {"opening", "uncertain"}:
                binding = await self._update(actor, room_id, binding["session_id"], "active")
            await self._authorize(actor, room_id)
            return self._public(binding, current, room_id)

    async def open(self, actor, room_id, url):
        try:
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError()
            checked_url(url)
        except (TypeError, ValueError):
            raise HTTPException(422, "Choose an HTTPS URL on the configured exact host allowlist.") from None
        async with await self._lock(actor, room_id):
            binding = await self._binding(actor, room_id)
            if binding and binding["status"] not in {"closed", "expired"}:
                current = await self._state(actor, room_id, binding)
                current = await self._release_stale(room_id, binding, current)
                if current:
                    if binding["status"] != "active":
                        binding = await self._update(actor, room_id, binding["session_id"], "active")
                    await self._authorize(actor, room_id)
                    return self._public(binding, current, room_id)
                if binding["status"] in {"opening", "uncertain"}:
                    raise HTTPException(409, "The earlier room browser request is unresolved. Check its status or close it before opening another.")
            sid = str(uuid.uuid4())
            binding = await asyncio.to_thread(self.store.reserve_browser, actor, room_id, sid)
            try:
                current = await self.transport.open(sid, url, scope_for(room_id))
                await self._authorize(actor, room_id)
                if current.get("id") != sid:
                    raise HTTPException(503, "The room browser identity could not be confirmed.")
                binding = await self._update(actor, room_id, sid, "active")
            except BaseException as error:
                # This ID was reserved before dispatch. Never discover/attach a
                # different active browser after an occupied or uncertain reply.
                with suppress(Exception):
                    await self._update(actor, room_id, sid, "closed" if isinstance(error, HTTPException) and error.status_code == 409 else "uncertain")
                if isinstance(error, (asyncio.CancelledError, TeamError)):
                    with suppress(Exception): await self.transport.close(sid, scope_for(room_id))
                if isinstance(error, BrowserOccupied):
                    busy = HTTPException(409, "The managed browser is in use. This room has not attached to or changed that session.")
                    busy.code = "browser_occupied"
                    raise busy from None
                raise
            return self._public(binding, current, room_id)

    async def _current(self, actor, room_id, epoch=None, *, agent=False):
        binding = await self._binding(actor, room_id)
        current = await self._state(actor, room_id, binding)
        if not current:
            raise HTTPException(404, "This room has no live browser. Open one explicitly to continue.")
        current = await self._release_stale(room_id, binding, current)
        if epoch is not None and epoch_value(epoch) != current["epoch"]:
            raise HTTPException(409, "Browser control changed. Refresh before trying again.")
        if agent and current["mode"] != "agent":
            raise HTTPException(409, "A teammate has browser control. Return control before REIGN acts.")
        return binding, current

    async def control(self, actor, room_id, connection_id, mode, epoch):
        if not isinstance(mode, str) or mode not in {"agent", "human"}:
            raise TeamError("invalid_team_request")
        epoch_value(epoch)
        async with await self._lock(actor, room_id):
            presence = self._connection(actor, room_id, connection_id)
            binding, current = await self._current(actor, room_id, epoch)
            lease = await self._valid_lease(room_id)
            if mode == "agent":
                if not lease or lease["user_id"] != actor["id"] or lease["connection_id"] != connection_id:
                    raise HTTPException(409, "Only the current controller can return this browser.")
            elif lease or current["mode"] != "agent":
                raise HTTPException(409, "Another room connection has browser control.")
            # Status/lease checks await other services. A membership revocation
            # during that wait must prevent dispatch, not just hide its result.
            await self._authorize(actor, room_id)
            presence = self._connection(actor, room_id, connection_id)
            if mode == "human":
                active = self.runtime.active.get(room_id)
                if active:
                    active["task"].cancel()
                self.leases[room_id] = {"actor": dict(actor), "user_id": actor["id"],
                    "display_name": actor["display_name"], "connection_id": connection_id,
                    "session_id": binding["session_id"], "epoch": epoch + 1, "expires_at": presence["expires_at"]}
            try:
                current = await self.transport.control(binding["session_id"], mode, epoch, scope_for(room_id))
            except HTTPException as error:
                if mode == "human" and error.status_code in {404, 409}:
                    self.leases.pop(room_id, None)
                raise
            if mode == "agent":
                self.leases.pop(room_id, None)
            else:
                self.leases[room_id]["epoch"] = current["epoch"]
            await self._authorize(actor, room_id)
            return self._public(binding, current, room_id)

    async def input(self, actor, room_id, connection_id, payload):
        from reign.core import InputRequest
        try:
            fields(payload, {"kind", "epoch", "x", "y", "text", "key", "dy"}, {"kind", "epoch"})
            data = InputRequest.model_validate(payload).model_dump()
        except (ValidationError, ValueError):
            raise TeamError("invalid_team_request") from None
        async with await self._lock(actor, room_id):
            self._connection(actor, room_id, connection_id)
            binding, current = await self._current(actor, room_id, epoch_value(payload["epoch"]))
            lease = await self._valid_lease(room_id)
            if (not lease or current["mode"] != "human" or lease["user_id"] != actor["id"]
                    or lease["connection_id"] != connection_id or lease["epoch"] != current["epoch"]):
                raise HTTPException(409, "Take control from this room connection before sending browser input.")
            await self._authorize(actor, room_id)
            self._connection(actor, room_id, connection_id)
            current = await self.transport.input(binding["session_id"], data, scope_for(room_id))
            await self._authorize(actor, room_id)
            return self._public(binding, current, room_id)

    async def navigate(self, actor, room_id, url, epoch=None):
        try:
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError()
            checked_url(url)
        except (TypeError, ValueError):
            raise HTTPException(422, "Choose an HTTPS URL on the configured exact host allowlist.") from None
        async with await self._lock(actor, room_id):
            binding, current = await self._current(actor, room_id, epoch, agent=True)
            await self._authorize(actor, room_id)
        # Never hold the room's coordination lock while waiting for Temporal.
        # A human takeover can cancel/fence this exact epoch in the meantime.
        current = await self.transport.navigate(binding["session_id"], url, current["epoch"], scope_for(room_id))
        return await self.status(actor, room_id)

    async def observe(self, actor, room_id, epoch=None):
        async with await self._lock(actor, room_id):
            binding, current = await self._current(actor, room_id, epoch, agent=True)
            await self._authorize(actor, room_id)
            result = await self.transport.observe(binding["session_id"], current["epoch"], scope_for(room_id))
            await self._authorize(actor, room_id)
            return result

    async def action(self, actor, room_id, action, arguments):
        async with await self._lock(actor, room_id):
            binding, current = await self._current(actor, room_id, agent=True)
            await self._authorize(actor, room_id)
        result = await self.transport.action(binding["session_id"], {"action": action, "epoch": current["epoch"], **arguments}, scope_for(room_id))
        await self._authorize(actor, room_id)
        return result

    async def frame(self, actor, room_id):
        async with await self._lock(actor, room_id):
            binding, _ = await self._current(actor, room_id)
            await self._authorize(actor, room_id)
        result = await self.transport.frame(binding["session_id"], scope_for(room_id))
        await self._authorize(actor, room_id)
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        for name in ("X-Session-ID", "X-Frame-Sequence", "X-Frame-Timestamp"):
            if name in result.headers:
                headers[name] = result.headers[name]
        return Response(result.content, media_type="image/jpeg", headers=headers)

    async def close(self, actor, room_id):
        async with await self._lock(actor, room_id):
            binding = await self._binding(actor, room_id)
            if binding and binding["status"] not in {"closed", "expired"}:
                lease = await self._valid_lease(room_id)
                if lease and lease["user_id"] != actor["id"]:
                    raise HTTPException(409, "Ask the current controller to return control before closing this browser.")
                await self._authorize(actor, room_id)
                active = self.runtime.active.get(room_id)
                if active:
                    active["task"].cancel()
                await self.transport.close(binding["session_id"], scope_for(room_id))
                binding = await self._update(actor, room_id, binding["session_id"], "closed")
            self.leases.pop(room_id, None)
            return self._public(binding, None, room_id)

    async def _execute(self, actor, room_id, name, arguments):
        """Trusted actor/room arguments; model arguments select no resource ID."""
        original = TOOLS.get(name)
        if original is None:
            raise TeamError("invalid_team_request")
        try:
            args = CATALOG[original][1].model_validate(arguments).model_dump()
        except (ValidationError, TypeError, ValueError):
            raise TeamError("invalid_team_request") from None
        if original == "browser_status": return await self.status(actor, room_id)
        if original == "open_browser": return await self.open(actor, room_id, args["url"])
        if original == "navigate_browser": return await self.navigate(actor, room_id, args["url"])
        if original == "browser_observe": return await self.observe(actor, room_id)
        return await self.action(actor, room_id, original.removeprefix("browser_"), args)

    async def execute(self, actor, room_id, name, arguments):
        try:
            await self._authorize(actor, room_id)
            return {"ok": True, **await self._execute(actor, room_id, name, arguments)}
        except TeamError as error:
            if error.code in {"team_identity_required", "team_owner_required", "team_not_found"}:
                raise
            return {"ok": False, "code": error.code, "detail": error.detail, "status": error.status}
        except BrowserActionError as error:
            detail = ("This field requires human control. The shared room cannot use private saved accounts."
                      if error.code in {"secret_field", "credential_unavailable"} else error.detail)
            return {"ok": False, "code": error.code, "detail": detail, "status": error.status_code}
        except HTTPException as error:
            if error.status_code in {401, 403}:
                raise
            return {"ok": False, "code": getattr(error, "code", "browser_unavailable"),
                    "detail": error.detail, "status": error.status_code}

    async def _reap_once(self):
        for room_id, old in list(self.leases.items()):
            async with self.locks.setdefault(room_id, asyncio.Lock()):
                if self.leases.get(room_id) is not old or await self._valid_lease(room_id):
                    continue
                try:
                    current = await self.transport.status(old["session_id"], scope_for(room_id))
                    if current["mode"] == "human":
                        await self.transport.control(old["session_id"], "agent", current["epoch"], scope_for(room_id))
                    self.leases.pop(room_id, None)
                except HTTPException as error:
                    if error.status_code == 404:
                        self.leases.pop(room_id, None)

    async def _reap(self):
        while True:
            await asyncio.sleep(2)
            with suppress(Exception):
                await self._reap_once()

    async def shutdown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        # Control is ephemeral. Best-effort handback does not close the room's
        # browser or replay a task; its existence is rechecked after a restart.
        for room_id, lease in list(self.leases.items()):
            with suppress(Exception):
                async with asyncio.timeout(3):
                    current = await self.transport.status(lease["session_id"], scope_for(room_id))
                    if current["mode"] == "human":
                        await self.transport.control(lease["session_id"], "agent", current["epoch"], scope_for(room_id))
        self.leases.clear()


def install(app):
    def runtime(request):
        value = getattr(request.app.state, "team_browser", None)
        if value is None:
            raise TeamError("team_unavailable")
        return value

    @app.get("/api/team/rooms/{room_id}/browser")
    async def status(room_id: str, request: Request):
        return reply(await runtime(request).status(await require_actor(request), room_id))

    @app.post("/api/team/rooms/{room_id}/browser")
    async def open_browser(room_id: str, request: Request):
        actor = await require_actor(request)
        data = await body(request, {"url"}, {"url"}, 3000)
        return reply(await runtime(request).open(actor, room_id, data["url"]))

    @app.delete("/api/team/rooms/{room_id}/browser")
    async def close_browser(room_id: str, request: Request):
        actor = await require_actor(request)
        return reply(await runtime(request).close(actor, room_id))

    @app.get("/api/team/rooms/{room_id}/browser/frame")
    async def frame(room_id: str, request: Request):
        return await runtime(request).frame(await require_actor(request), room_id)

    @app.get("/api/team/rooms/{room_id}/browser/observe")
    async def observe(room_id: str, request: Request, epoch: int):
        return reply(await runtime(request).observe(await require_actor(request), room_id, epoch_value(epoch)))

    @app.post("/api/team/rooms/{room_id}/browser/navigate")
    async def navigate(room_id: str, request: Request):
        actor = await require_actor(request)
        data = await body(request, {"url", "epoch"}, {"url", "epoch"}, 3000)
        return reply(await runtime(request).navigate(actor, room_id, data["url"], epoch_value(data["epoch"])))

    @app.post("/api/team/rooms/{room_id}/browser/control")
    async def control(room_id: str, request: Request):
        actor = await require_actor(request)
        data = await body(request, {"connection_id", "mode", "epoch"}, {"connection_id", "mode", "epoch"}, 1024)
        return reply(await runtime(request).control(actor, room_id, data["connection_id"], data["mode"], data["epoch"]))

    @app.post("/api/team/rooms/{room_id}/browser/input")
    async def input_event(room_id: str, request: Request):
        actor = await require_actor(request)
        data = await body(request, {"connection_id", "epoch", "kind", "x", "y", "text", "key", "dy"}, {"connection_id", "epoch", "kind"}, 10000)
        cid = data.pop("connection_id")
        return reply(await runtime(request).input(actor, room_id, cid, data))
