"""AX's narrow authenticated bridge to the private shared-workspace API."""
import asyncio
import json
import os
import re
import uuid

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from reign import ax_auth
from reign.team_identity import OWNER_ID, issue_actor

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
BODY_LIMIT = 900_000
TEAM_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def enabled():
    return os.environ.get("AX_TEAM_ENABLED") == "1"


def failure(status=503, detail="The team workspace is temporarily unavailable."):
    return HTTPException(status, detail, headers=NO_STORE)


def employee_identity(value):
    try:
        data = value["identity"]
        if not isinstance(data, dict) or set(data) != {
            "id", "username", "display_name", "role", "credential_revision"
        }:
            raise ValueError()
        uid = str(uuid.UUID(data["id"]))
        if (uid != data["id"] or data["role"] != "employee"
                or not isinstance(data["username"], str)
                or not ax_auth.USERNAME.fullmatch(data["username"])
                or ax_auth.reserved_username(data["username"])
                or not isinstance(data["display_name"], str)
                or not 1 <= len(data["display_name"].strip()) <= 80
                or any(ord(c) < 32 for c in data["display_name"])
                or type(data["credential_revision"]) is not int
                or not 1 <= data["credential_revision"] < 2**31):
            raise ValueError()
        return ax_auth.Identity(data["username"], data["display_name"], "employee",
                                str(data["credential_revision"]), uid)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure() from None


class TeamBridge:
    def __init__(self, app, core_origin, configured, principal, session_csrf, assets):
        self.app, self.core_origin, self.configured = app, core_origin, configured
        self.principal, self.session_csrf, self.assets = principal, session_csrf, assets

    async def directory(self, path, data):
        if not enabled():
            raise failure()
        try:
            response = await self.app.state.http.request(
                "POST", self.core_origin() + path, json=data,
                headers={"Authorization": "Bearer " + self.configured("CORE_TOKEN")},
                timeout=10, follow_redirects=False)
        except (httpx.HTTPError, TimeoutError):
            raise failure() from None
        if response.status_code in {401, 403, 404}:
            raise ax_auth.InvalidCredentials()
        if response.status_code != 200 or len(response.content) > 8192:
            raise failure()
        try:
            return employee_identity(response.json())
        except (ValueError, UnicodeError):
            raise failure() from None

    async def console_access(self, session):
        if session.identity.role != "employee" or not session.identity.user_id:
            return False
        if not enabled():
            raise failure()
        try:
            response = await self.app.state.http.request(
                "POST", self.core_origin() + "/internal/team/console-access",
                json={"id": session.identity.user_id, "credential_revision": int(session.identity.revision)},
                headers={"Authorization": "Bearer " + self.configured("CORE_TOKEN")},
                timeout=10, follow_redirects=False)
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise failure() from None
        if response.status_code in {401, 403, 404}:
            raise failure(401, "Sign in again to use this workspace.")
        if response.status_code != 200 or len(response.content) > 2048:
            raise failure()
        try:
            value = response.json()
            if not isinstance(value, dict) or set(value) != {"console_access"} or type(value["console_access"]) is not bool:
                raise ValueError()
            return value["console_access"]
        except (ValueError, UnicodeError):
            raise failure() from None

    async def authenticate(self, username, password):
        return await self.directory("/internal/team/authenticate", {
            "username": username.strip().lower(), "password": password})

    async def current(self, request, session=None):
        session = session or self.principal(request)
        if session.identity.role == "employee":
            try:
                identity = await self.directory("/internal/team/identity", {
                    "id": session.identity.user_id,
                    "credential_revision": int(session.identity.revision)})
                if identity != session.identity:
                    raise ax_auth.InvalidCredentials()
            except ax_auth.InvalidCredentials:
                request.app.state.auth.revoke(session)
                raise failure(401, "Sign in again to use this workspace.") from None
        return session

    async def page(self, request: Request):
        try:
            session = await self.current(request)
        except HTTPException as error:
            if error.status_code == 401:
                return RedirectResponse("/?next=%2Fteam", status_code=303, headers=NO_STORE)
            raise
        if session.identity.role not in {"owner", "employee"}:
            raise failure(403, "Team workspace access is required.")
        if not enabled():
            raise failure()
        return FileResponse(self.assets / "team.html", headers={
            **NO_STORE, "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": TEAM_CSP,
            "Permissions-Policy": "camera=(self), microphone=(self), display-capture=(self)"})

    async def proxy(self, request: Request, team_path: str = ""):
        session = self.principal(request)
        if session.identity.role not in {"owner", "employee"}:
            raise failure(403, "Team workspace access is required.")
        if request.method not in {"GET", "HEAD"}:
            self.session_csrf(request, session)
        if not enabled():
            raise failure()
        # Never let URL normalization turn a team path into a legacy owner API.
        if team_path and (len(team_path) > 400 or not re.fullmatch(r"[a-zA-Z0-9_/-]*", team_path)
                or any(part in {"", ".", ".."} for part in team_path.split("/"))):
            raise failure(404, "This workspace route does not exist.")
        if len(request.url.query) > 1024:
            raise failure(422, "The workspace request is too large.")
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > BODY_LIMIT:
                        raise failure(413, "The workspace upload is too large.")
                    body.extend(chunk)
        except TimeoutError:
            raise failure(408, "The workspace upload timed out.") from None
        if body and request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise failure(415, "Send a JSON workspace request.")
        if team_path == "members" and request.method == "POST":
            try:
                value = json.loads(body)
                name = value.get("username", "")
                if isinstance(name, str) and ax_auth.reserved_username(name.strip().lower()):
                    return JSONResponse({"code": "username_reserved", "detail":
                        "That username already has a separate account. Choose another employee username."},
                        status_code=409, headers=NO_STORE)
            except (ValueError, UnicodeError, AttributeError):
                raise failure(422, "The employee request is invalid.") from None
        identity = session.identity
        actor = issue_actor(OWNER_ID if identity.role == "owner" else identity.user_id,
                            identity.role, 0 if identity.role == "owner" else int(identity.revision))
        path = "/api/team" + ("/" + team_path if team_path else "")
        if request.url.query:
            path += "?" + request.url.query
        try:
            response = await self.app.state.http.request(
                request.method, self.core_origin() + path, content=bytes(body) if body else None,
                headers={"Authorization": "Bearer " + self.configured("CORE_TOKEN"),
                         "X-Reign-Team-Actor": actor, "Content-Type": "application/json"},
                timeout=75, follow_redirects=False)
        except (httpx.HTTPError, TimeoutError):
            raise failure() from None
        if response.status_code == 401:
            request.app.state.auth.revoke(session)
            raise failure(401, "Sign in again to use this workspace.")
        if (not 200 <= response.status_code < 500 or 300 <= response.status_code < 400
                or len(response.content) > 8_000_000):
            raise failure()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type == "audio/wav" and re.fullmatch(r"rooms/[a-f0-9-]+/audio/[a-f0-9-]+", team_path):
            return Response(response.content, status_code=response.status_code, media_type="audio/wav", headers=NO_STORE)
        room_id_pattern = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        if content_type == "image/jpeg" and re.fullmatch(r"rooms/" + room_id_pattern + r"/browser/frame", team_path):
            frame_headers = dict(NO_STORE)
            for name in ("X-Session-ID", "X-Frame-Sequence", "X-Frame-Timestamp"):
                if name in response.headers:
                    frame_headers[name] = response.headers[name]
            return Response(response.content, status_code=response.status_code, media_type="image/jpeg", headers=frame_headers)
        if content_type != "application/json":
            raise failure()
        # All Core team errors have a closed, safe vocabulary; no private upstream
        # headers, redirects, cookies or credentials are forwarded.
        return Response(response.content, status_code=response.status_code,
                        media_type="application/json", headers=NO_STORE)

    def install(self):
        self.app.add_api_route("/team", self.page, methods=["GET"])
        self.app.add_api_route("/api/team", self.proxy, methods=["GET", "POST"])
        self.app.add_api_route("/api/team/{team_path:path}", self.proxy,
                               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
