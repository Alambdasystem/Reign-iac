"""UltraView AX: authenticated human console for the private REIGN Core."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadData
from reign import ax_auth
from reign import team_ax
from reign.openstl import OpenSTLBridge
from reign.config import checked_url
from reign.session_errors import BROWSER_OCCUPIED_CODE, BrowserOccupied, browser_occupied_response
from reign.voice import VOICE_ERRORS, VoiceError, read_offer, voice_error_response

ACCOUNT_ERRORS = {
    "vault_not_ready": (503, "Secure saved accounts are not ready for this workspace."),
    "vault_unavailable": (503, "Secure saved accounts are unavailable. Refresh and try again."),
    "invalid_account": (422, "The saved account request is not valid. Check the form and try again."),
    "origin_not_allowed": (422, "That website is not allowed for saved logins."),
    "account_not_found": (404, "That saved login no longer exists. Refresh Accounts."),
    "account_exists": (409, "A saved login with that label already exists. Choose a different label."),
    "account_use_conflict": (409, "The saved login cannot be used in the current browser or conversation state."),
    "grant_invalid": (403, "This saved login request expired or could not be verified. Start a new request."),
}
ACCOUNT_BODY_LIMIT = 8192
LOGIN_BODY_TIMEOUT = 10
LOGOUT_CLEANUP_TIMEOUT = 2


class AccountError(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ACCOUNT_ERRORS else "vault_unavailable"


async def account_error_response(request, error):
    status, detail = ACCOUNT_ERRORS[error.code]
    return JSONResponse({"code": error.code, "detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def account_authorize(request, write=False):
    try:
        (session_csrf(request, principal(request)) if write else None)
        owner_user(request)
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
        raise
    if request.query_params:
        raise AccountError("invalid_account")


def account_identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise AccountError("invalid_account") from None


def account_text(value, limit, *, trim=False, allow_whitespace=False):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise AccountError("invalid_account")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AccountError("invalid_account") from None
    if "\x00" in value or (not allow_whitespace and not value.strip()):
        raise AccountError("invalid_account")
    return value.strip() if trim else value


def account_origin(value):
    value = account_text(value, 512, trim=True)
    try:
        parsed = urlsplit(value)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError()
        checked_url(value)
        return "https://" + parsed.hostname
    except (ValueError, TypeError):
        raise AccountError("origin_not_allowed") from None


async def account_body(request, *, empty=False):
    # Parse only after authorization. FastAPI's default body-validation errors
    # include rejected input, which is unsuitable for a credential form.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > ACCOUNT_BODY_LIMIT:
            raise AccountError("invalid_account")
        body.extend(chunk)
    if empty and not body:
        return {}
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise AccountError("invalid_account") from None
    if not isinstance(value, dict):
        raise AccountError("invalid_account")
    return value


def account_metadata(value):
    if not isinstance(value, dict):
        raise AccountError("vault_unavailable")
    try:
        return {"id": account_identifier(value.get("id")),
                "label": account_text(value.get("label"), 80),
                "origin": account_origin(value.get("origin")),
                "username": account_text(value.get("username"), 320),
                "created_at": account_text(value.get("created_at"), 64),
                "updated_at": account_text(value.get("updated_at"), 64)}
    except AccountError:
        raise AccountError("vault_unavailable") from None

def configured(name):
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise HTTPException(503, f"{name} not configured")
    return value

def signer():
    return URLSafeTimedSerializer(configured("AX_SESSION_KEY"), salt="reign-ax")

def principal(request: Request):
    cookie = request.cookies.get("ax_session", "")
    if not cookie or len(cookie) > 2048:
        raise HTTPException(401, "Login required", headers={"Cache-Control": "no-store"})
    try:
        payload = signer().loads(cookie, max_age=ax_auth.SESSION_SECONDS)
        return request.app.state.auth.resolve(payload)
    except (BadData, ax_auth.InvalidSession):
        raise HTTPException(401, "Login required", headers={"Cache-Control": "no-store"}) from None
    except ax_auth.AuthConfigurationError:
        raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None


def user(request: Request):
    session = principal(request)
    if session.identity.role == "owner":
        return "admin"
    # Middleware checks the member's live Core grant for every protected request.
    # This flag is never sourced from cookies, query parameters or a browser claim.
    if session.identity.role == "employee" and getattr(request.state, "console_access", False):
        return session.identity.user_id
    raise HTTPException(403, "Console access required", headers={"Cache-Control": "no-store"})


def owner_user(request: Request):
    session = principal(request)
    if session.identity.role != "owner":
        raise HTTPException(403, "Owner access required", headers={"Cache-Control": "no-store"})
    return "admin"


def same_origin(request: Request):
    origin = request.headers.get("origin")
    expected = os.environ.get("AX_PUBLIC_ORIGIN", "").rstrip("/")
    if not expected or origin != expected:
        raise HTTPException(403, "Invalid origin", headers={"Cache-Control": "no-store"})


def session_csrf(request: Request, session):
    same_origin(request)
    token = request.headers.get("x-csrf-token", "")
    if not ax_auth.valid_csrf(session, token, request.cookies.get("ax_csrf", "")):
        raise HTTPException(403, "Invalid CSRF token", headers={"Cache-Control": "no-store"})


def csrf(request: Request):
    user(request)
    session_csrf(request, principal(request))

@asynccontextmanager
async def lifespan(app):
    app.state.auth = ax_auth.AuthState()
    app.state.linkedin_previews = {}
    # Browser startup/Temporal dispatch can take longer than a normal API call.
    app.state.http = httpx.AsyncClient(timeout=120, trust_env=False)
    sweeper = asyncio.create_task(openstl.sweeper())
    try:
        yield
    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        for grant in list(openstl.grants.values()):
            await openstl.cleanup(grant)
        openstl.pending.clear()
        openstl.grants.clear()
        await app.state.http.aclose()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(VoiceError, voice_error_response)
app.add_exception_handler(BrowserOccupied, browser_occupied_response)
app.add_exception_handler(AccountError, account_error_response)
ASSETS = Path(__file__).resolve().parent.parent / "ax_static"
if not ASSETS.is_dir():
    ASSETS = Path(__file__).resolve().parent.parent / "services" / "ax"
AVATAR_ASSETS = ASSETS / "avatar-assets"
AVATAR_CSP = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; connect-src 'self'; worker-src 'self'; "
    "img-src 'self' data: blob:; font-src 'self'; media-src 'self' blob:; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)


class AXStaticFiles(StaticFiles):
    """Keep the authenticated lab document outside the public static surface."""

    async def get_response(self, path, scope):
        try:
            root = Path(self.directory).resolve()
            target = (root / path).resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(404, "Not found") from None
        if (target in {root / "avatar-lab.html", root / "team.html"}
                or target.is_relative_to(root / "avatar-assets")):
            raise HTTPException(404, "Not found")
        response = await super().get_response(path, scope)
        # A module worker has its own response policy. Constrain its imports
        # and model requests to this origin just like the authenticated page.
        if target.name == "portrait-engine.js":
            response.headers["Content-Security-Policy"] = AVATAR_CSP
        if target == root / "team-avatar-assets" / "portrait-atlas.webp":
            # Only the public, content-addressed REIGN artwork is long-lived.
            # A stale or arbitrary version must never pin different bytes for
            # a year. Authenticated pages and user photos do not use this route.
            query = parse_qs(scope.get("query_string", b"").decode("ascii", "replace"),
                             keep_blank_values=True)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            immutable = query == {"v": [digest]}
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if immutable
                else "public, max-age=0, must-revalidate"
            )
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        elif target.suffix in {".js", ".css", ".mjs"}:
            response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


app.mount("/assets", AXStaticFiles(directory=ASSETS), name="assets")

@app.get("/")
async def console(request: Request):
    try:
        role = principal(request).identity.role
        if role == "avatar" or (role == "employee" and not getattr(request.state, "console_access", False)):
            return RedirectResponse("/team" if role == "employee" else "/avatar-lab", status_code=303,
                                    headers={"Cache-Control": "no-store"})
    except HTTPException as error:
        if error.status_code != 401:
            raise
    path = Path(__file__).resolve().parent.parent / "ax_static" / "index.html"
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent / "services" / "ax" / "index.html"
    return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

@app.get("/genesis")
async def genesis_console(request: Request):
    try:
        user(request)
    except HTTPException as error:
        if error.status_code == 401:
            # A direct browser link needs to reach the normal sign-in page.
            # API endpoints still return 401 and no Genesis data is exposed.
            return RedirectResponse(url="/", status_code=303,
                                    headers={"Cache-Control": "no-store"})
        raise
    path = ASSETS / "genesis.html"
    return FileResponse(path, headers={"Cache-Control": "no-store",
                                       "X-Content-Type-Options": "nosniff"})


@app.get("/avatar-lab")
async def avatar_lab(request: Request):
    """Isolated experimental avatar UX, available only after AX sign-in.

    The lab has no Core/MCP/Temporal actions and does not load credentials.
    Existing REIGN routes and the production console are unchanged.
    """
    try:
        await team.current(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse(url="/?next=%2Favatar-lab", status_code=303,
                                    headers={"Cache-Control": "no-store"})
        raise
    return FileResponse(ASSETS / "avatar-lab.html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": AVATAR_CSP,
    })


@app.get("/avatar-assets/{asset_path:path}")
async def avatar_asset(asset_path: str):
    """Serve only bundled public model/runtime/reference assets, never uploads."""
    known = {
        "models/distilled.onnx", "models/appearance.onnx",
        "sample-portrait.png", "sample-portrait.jpg", "THIRD_PARTY_NOTICES.txt",
    }
    runtime_file = re.fullmatch(r"ort/ort[a-zA-Z0-9_.-]{0,100}\.(?:mjs|js|wasm)",
                                asset_path)
    if asset_path not in known and runtime_file is None:
        raise HTTPException(404, "Not found")
    try:
        root = AVATAR_ASSETS.resolve()
        candidate = root / asset_path
        if candidate.is_symlink() or candidate.parent.is_symlink():
            raise ValueError()
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "Not found") from None
    media_types = {
        ".onnx": "application/octet-stream", ".wasm": "application/wasm",
        ".mjs": "text/javascript", ".js": "text/javascript",
        ".png": "image/png", ".jpg": "image/jpeg", ".txt": "text/plain",
    }
    return FileResponse(path, media_type=media_types[path.suffix], headers={
        "Cache-Control": "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Security-Policy": AVATAR_CSP,
    })


@app.get("/guardrails")
async def guardrails_console(request: Request):
    try:
        user(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse(url="/", status_code=303,
                                    headers={"Cache-Control": "no-store"})
        raise
    return FileResponse(ASSETS / "guardrails.html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/security/status")
async def guardrails_security_status(request: Request):
    user(request)
    result = await core("GET", "/api/security/status")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.get("/nodes")
async def node_console(request: Request):
    try:
        user(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse(url="/", status_code=303,
                                    headers={"Cache-Control": "no-store"})
        raise
    return FileResponse(ASSETS / "nodes.html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/api/me")
async def me(request: Request):
    profile = ax_auth.profile(await team.current(request))
    if profile["role"] == "employee" and getattr(request.state, "console_access", False):
        profile["landing_path"] = "/"
        profile["capabilities"].update(voice=True, ai_chat=True, genesis=True, managed_browser=False)
    return JSONResponse(profile, headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def workspace_status(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/readyz")).json(),
                        headers={"Cache-Control": "no-store"})

@app.post("/login")
async def login(request: Request):
    if request.headers.get("origin") is not None:
        same_origin(request)
    state = request.app.state.auth
    reserved = False
    try:
        state.admit_login(request.client.host if request.client else "unknown")
        # Reserve before reading a possibly slow body. No await separates
        # admission and reservation, so at most two sign-ins can run a KDF.
        state.pending_logins += 1
        reserved = True
        body = bytearray()
        async with asyncio.timeout(LOGIN_BODY_TIMEOUT):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 4096:
                    raise ax_auth.InvalidCredentials()
                body.extend(chunk)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise ax_auth.InvalidCredentials() from None
        if (not isinstance(data, dict) or set(data) - {"username", "password"}
                or "password" not in data):
            raise ax_auth.InvalidCredentials()
        remote = team_ax.enabled() and ax_auth.employee_candidate(data.get("username", ""), data["password"])
        work = asyncio.create_task(
            team.authenticate(data.get("username", ""), data["password"]) if remote else
            asyncio.to_thread(ax_auth.authenticate, data.get("username", ""), data["password"]))
        try:
            identity = await asyncio.shield(work)
        except asyncio.CancelledError:
            # Cancelling an HTTP request cannot stop its native KDF thread.
            # Hold the slot until that work actually finishes.
            def release(done):
                state.pending_logins -= 1
                if not done.cancelled():
                    done.exception()
            work.add_done_callback(release)
            reserved = False
            raise
        # Resolve again after the KDF so a concurrent verifier rotation cannot
        # create a session using a credential that is no longer configured.
        current = (await team.directory("/internal/team/identity", {
            "id": identity.user_id, "credential_revision": int(identity.revision)})
            if identity.role == "employee" else ax_auth.identity_for(identity.username, identity.role))
        if current != identity:
            raise ax_auth.InvalidCredentials()
        payload, token, session = state.create(identity)
        signed = signer().dumps(payload)
    except (ax_auth.InvalidCredentials, ax_auth.InvalidSession, TimeoutError):
        raise HTTPException(401, "Invalid credentials", headers={"Cache-Control": "no-store"}) from None
    except ax_auth.AuthConfigurationError:
        raise HTTPException(503, "Authentication unavailable", headers={"Cache-Control": "no-store"}) from None
    except ax_auth.LoginThrottled:
        raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.",
                            headers={"Cache-Control": "no-store", "Retry-After": "60"}) from None
    finally:
        if reserved:
            state.pending_logins -= 1
    profile = ax_auth.profile(session)
    if identity.role == "employee" and await team.console_access(session):
        profile["landing_path"] = "/"
        profile["capabilities"].update(voice=True, ai_chat=True, genesis=True, managed_browser=False)
    response = JSONResponse({"status": "authenticated", **profile},
                            headers={"Cache-Control": "no-store"})
    response.set_cookie("ax_session", signed, httponly=True, secure=True, samesite="strict", max_age=ax_auth.SESSION_SECONDS)
    response.set_cookie("ax_csrf", token, httponly=False, secure=True, samesite="strict", max_age=ax_auth.SESSION_SECONDS)
    return response

@app.post("/logout")
async def logout(request: Request):
    same_origin(request)
    try:
        session = principal(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        session = None
    if session is not None:
        session_csrf(request, session)
        request.app.state.auth.revoke(session)
        request.app.state.linkedin_previews.pop(session.nonce, None)
        if session.identity.role == "owner":
            openstl.pending.clear()
            for grant in list(openstl.grants.values()):
                grant.revoked = True
            try:
                await asyncio.wait_for(openstl.reap(), timeout=LOGOUT_CLEANUP_TIMEOUT)
            except Exception:
                # Revocation is already effective. Cleanup is best effort and
                # the existing sweeper retries it; logout must clear cookies
                # even while Core is unavailable or cleanup raises.
                pass
    response = JSONResponse({"status": "logged_out", "authenticated": False},
                            headers={"Cache-Control": "no-store"})
    response.delete_cookie("ax_session", secure=True, httponly=True, samesite="strict")
    response.delete_cookie("ax_csrf", secure=True, samesite="strict")
    return response

def session_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(422, "Invalid session identifier")


def core_origin():
    base = os.environ.get("CORE_URL", "").rstrip("/")
    if not base.startswith("https://") and not (os.environ.get("AX_ALLOW_INTERNAL_HTTP") == "1" and base.startswith("http://")):
        raise HTTPException(503, "Core endpoint not configured securely")
    return base


async def core(method, path, data=None, voice_errors=False, account_errors=False):
    try:
        base = core_origin()
        result = await app.state.http.request(method, base + path,
            headers={"Authorization": "Bearer " + configured("CORE_TOKEN")}, json=data,
            **({"timeout": 40} if voice_errors else {}),
            **({"follow_redirects": False} if account_errors else {}))
    except HTTPException:
        if account_errors:
            raise AccountError("vault_unavailable") from None
        if voice_errors:
            raise VoiceError("voice_core_unavailable") from None
        raise
    except httpx.RequestError:
        if account_errors:
            raise AccountError("vault_unavailable") from None
        if voice_errors:
            raise VoiceError("voice_core_unavailable") from None
        raise HTTPException(503, "Core unavailable")
    if account_errors and 300 <= result.status_code < 400:
        raise AccountError("vault_unavailable")
    if result.status_code >= 400:
        if account_errors:
            try:
                code = result.json().get("code")
            except (ValueError, AttributeError):
                code = None
            raise AccountError(code)
        if voice_errors:
            try:
                code = result.json().get("code")
            except (ValueError, AttributeError):
                code = None
            # Trust the closed error code, never a downstream error message.
            raise VoiceError(code if isinstance(code, str) and code in VOICE_ERRORS else "voice_core_unavailable")
        if method == "POST" and path == "/api/sessions" and result.status_code == 409:
            try:
                code = result.json().get("code")
            except (ValueError, AttributeError):
                code = None
            if code == BROWSER_OCCUPIED_CODE:
                raise BrowserOccupied()
        raise HTTPException(result.status_code, "Core request failed")
    return result


def account_result(response):
    try:
        result = response.json()
    except (ValueError, UnicodeError):
        raise AccountError("vault_unavailable") from None
    if not isinstance(result, dict):
        raise AccountError("vault_unavailable")
    return result


@app.get("/api/accounts")
async def accounts(request: Request):
    account_authorize(request)
    result = account_result(await core("GET", "/api/accounts", account_errors=True))
    vault, records = result.get("vault"), result.get("accounts")
    if (not isinstance(vault, dict) or type(vault.get("ready")) is not bool
            or type(vault.get("durable")) is not bool or not isinstance(records, list)
            or len(records) > 100):
        raise AccountError("vault_unavailable")
    code = vault.get("code")
    if not isinstance(code, str) or code not in ACCOUNT_ERRORS:
        code = None if vault["ready"] and vault["durable"] else "vault_not_ready"
    return JSONResponse({"vault": {"ready": vault["ready"], "durable": vault["durable"], "code": code},
                         "accounts": [account_metadata(item) for item in records]},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/accounts")
async def save_account(request: Request):
    account_authorize(request, write=True)
    value = await account_body(request)
    if set(value) != {"label", "origin", "username", "password"}:
        raise AccountError("invalid_account")
    data = {"label": account_text(value["label"], 80, trim=True),
            "origin": account_origin(value["origin"]),
            "username": account_text(value["username"], 320),
            "password": account_text(value["password"], 1024, allow_whitespace=True)}
    result = account_result(await core("POST", "/api/accounts", data, account_errors=True))
    return JSONResponse({"account": account_metadata(result.get("account"))}, status_code=201,
                        headers={"Cache-Control": "no-store"})


@app.delete("/api/accounts/{aid}")
async def remove_account(aid: str, request: Request):
    account_authorize(request, write=True)
    identifier = account_identifier(aid)
    if await account_body(request, empty=True):
        raise AccountError("invalid_account")
    result = account_result(await core("DELETE", f"/api/accounts/{identifier}", account_errors=True))
    if result.get("status") != "deleted" or result.get("id") != identifier:
        raise AccountError("vault_unavailable")
    return JSONResponse({"status": "deleted", "id": identifier}, headers={"Cache-Control": "no-store"})


@app.post("/api/accounts/{aid}/use")
async def use_account(aid: str, request: Request):
    account_authorize(request, write=True)
    identifier = account_identifier(aid)
    value = await account_body(request)
    if "session_id" not in value or set(value) - {"session_id", "thread_id"}:
        raise AccountError("invalid_account")
    data = {"session_id": account_identifier(value["session_id"])}
    if "thread_id" in value:
        data["thread_id"] = account_identifier(value["thread_id"]) if value["thread_id"] is not None else None
    result = account_result(await core("POST", f"/api/accounts/{identifier}/use", data, account_errors=True))
    # The use route returns the normal public graph snapshot, never credentials
    # or a broker grant. Keep its surface identical to the existing chat API.
    fields = {"thread_id", "run_id", "status", "answer", "error", "session_id", "events",
              "usage", "memory", "turns", "max_turns", "continuable"}
    if not isinstance(result.get("thread_id"), str) or not isinstance(result.get("status"), str):
        raise AccountError("vault_unavailable")
    return JSONResponse({key: item for key, item in result.items() if key in fields},
                        headers={"Cache-Control": "no-store"})

@app.get("/api/readyz")
async def ready(request: Request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied, configured("CORE_TOKEN")):
        raise HTTPException(401, "Unauthorized")
    return (await core("GET", "/api/readyz")).json()

@app.get("/api/sessions/active")
async def active_session(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/sessions/active")).json(),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{sid}")
async def session(sid: str, request: Request):
    user(request)
    return (await core("GET", f"/api/sessions/{session_id(sid)}")).json()

@app.get("/api/sessions/{sid}/frame")
async def frame(sid: str, request: Request):
    user(request)
    result = await core("GET", f"/api/sessions/{session_id(sid)}/frame")
    headers = {"Cache-Control": "no-store"}
    for name in ("X-Frame-Sequence", "X-Frame-Timestamp", "X-Session-ID"):
        if name in result.headers:
            headers[name] = result.headers[name]
    return Response(result.content, media_type="image/jpeg", headers=headers)

@app.post("/api/sessions")
async def open_session(request: Request):
    csrf(request)
    return (await core("POST", "/api/sessions", await request.json())).json()

@app.post("/api/sessions/{sid}/{action}")
async def action(sid: str, action: str, request: Request):
    csrf(request)
    if action not in ("navigate", "control", "input"):
        raise HTTPException(404)
    return (await core("POST", f"/api/sessions/{session_id(sid)}/{action}", await request.json())).json()

@app.delete("/api/sessions/{sid}")
async def close(sid: str, request: Request):
    csrf(request)
    return (await core("DELETE", f"/api/sessions/{session_id(sid)}")).json()


@app.get("/api/agent")
async def agent(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/agent")).json(), headers={"Cache-Control": "no-store"})


@app.get("/api/voice")
async def voice_status(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/voice", voice_errors=True)).json(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/voice/offer")
async def voice_offer(request: Request):
    csrf(request)
    sdp = await read_offer(request)
    return JSONResponse((await core("POST", "/api/voice/offer", {"sdp": sdp}, voice_errors=True)).json(),
                        headers={"Cache-Control": "no-store"})


LINKEDIN_PREVIEW_SECONDS = 300
LINKEDIN_BODY_LIMIT = 20000


async def linkedin_body(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > LINKEDIN_BODY_LIMIT:
            raise HTTPException(413, "LinkedIn request is too large")
        data.extend(chunk)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Invalid LinkedIn request") from None
    if type(value) is not dict:
        raise HTTPException(422, "Invalid LinkedIn request")
    return value


def linkedin_owner(request: Request):
    session = principal(request)
    if session.identity.role != "owner":
        raise HTTPException(403, "Owner access required", headers={"Cache-Control": "no-store"})
    session_csrf(request, session)
    return session


@app.post("/api/linkedin/preview")
async def linkedin_preview(request: Request):
    session = linkedin_owner(request)
    body = await linkedin_body(request)
    draft = body.get("text")
    if set(body) != {"text"} or type(draft) is not str or not 1 <= len(draft.strip()) <= 3000:
        raise HTTPException(422, "Post must have 1–3000 characters")
    draft = draft.strip()
    try:
        draft.encode("utf-8")
    except UnicodeError:
        raise HTTPException(422, "Invalid post text") from None
    candidate = (await core("POST", "/internal/linkedin/preview", {"text": draft})).json()
    linkedin_owner(request)
    previews = request.app.state.linkedin_previews
    now = time.monotonic()
    for nonce, (_, expiry, _) in list(previews.items()):
        if expiry <= now or nonce not in request.app.state.auth.sessions:
            del previews[nonce]
    previews.pop(session.nonce, None)
    if candidate.get("ready") is True:
        receipt = secrets.token_urlsafe(32)
        previews[session.nonce] = (receipt, now + LINKEDIN_PREVIEW_SECONDS,
                                   {"text": candidate["text"], "author": candidate["author"]})
        candidate["receipt"] = receipt
    return JSONResponse(candidate, headers={"Cache-Control": "no-store"})


@app.post("/api/linkedin/publish")
async def linkedin_publish(request: Request):
    session = linkedin_owner(request)
    body = await linkedin_body(request)
    linkedin_owner(request)
    value = body.get("receipt")
    previews = request.app.state.linkedin_previews
    pending = previews.get(session.nonce)
    if (set(body) != {"receipt"} or type(value) is not str or not ax_auth.TOKEN.fullmatch(value)
            or pending is None or pending[1] <= time.monotonic()
            or not secrets.compare_digest(value, pending[0])):
        raise HTTPException(409, "Preview expired or already used. Review the post again.")
    # Consume before any await. Concurrent submissions cannot reuse this approval.
    del previews[session.nonce]
    result = (await core("POST", "/internal/linkedin/publish", pending[2])).json()
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.post("/api/chat")
async def chat(request: Request):
    csrf(request)
    return JSONResponse((await core("POST", "/api/chat", await request.json())).json(), headers={"Cache-Control": "no-store"})


@app.get("/api/chat/{tid}")
async def chat_state(tid: str, request: Request):
    user(request)
    return JSONResponse((await core("GET", f"/api/chat/{session_id(tid)}")).json(), headers={"Cache-Control": "no-store"})


@app.post("/api/chat/{tid}/resume")
async def chat_resume(tid: str, request: Request):
    csrf(request)
    return JSONResponse((await core("POST", f"/api/chat/{session_id(tid)}/resume")).json(), headers={"Cache-Control": "no-store"})


@app.post("/api/chat/{tid}/speech")
async def chat_speech(tid: str, request: Request):
    from reign.speech import read_speech_request
    csrf(request)
    thread_id = session_id(tid)
    run_id = await read_speech_request(request)
    try:
        async with asyncio.timeout(16):
            result = await core("POST", f"/api/chat/{thread_id}/speech", {"run_id": run_id})
    except TimeoutError:
        raise HTTPException(503, "Speech expressions are unavailable") from None
    # Logout or a password revision while the optional model request was in
    # flight must also prevent delivery of the private response.
    user(request)
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/genesis/status")
async def genesis_status(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/genesis/status")).json(),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/genesis/agents")
async def genesis_agents(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/genesis/agents")).json(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/genesis/preview")
async def genesis_preview(request: Request):
    csrf(request)
    try:
        result = await core("POST", "/api/genesis/preview", await request.json())
    except HTTPException as error:
        if error.status_code == 422:
            raise HTTPException(422, "Genesis preview denied") from None
        raise
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.post("/api/genesis/agents")
async def genesis_register(request: Request):
    csrf(request)
    try:
        result = await core("POST", "/api/genesis/agents", await request.json())
    except HTTPException as error:
        if error.status_code == 422:
            raise HTTPException(422, "Genesis registration denied") from None
        raise
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


def genesis_agent_id(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", value):
        raise HTTPException(422, "Invalid Genesis agent ID")
    return value


async def genesis_mutation(method: str, path: str, body: dict):
    try:
        result = await core(method, path, body)
    except HTTPException as error:
        if error.status_code in (404, 409, 422):
            raise HTTPException(error.status_code, {
                404: "Genesis draft not found",
                409: "Genesis revision conflict; refresh and retry",
                422: "Genesis draft action denied",
            }[error.status_code]) from None
        raise
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/genesis/agents/{agent_id}")
async def genesis_detail(agent_id: str, request: Request):
    user(request)
    result = await core("GET", f"/api/genesis/agents/{genesis_agent_id(agent_id)}")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.put("/api/genesis/agents/{agent_id}")
async def genesis_update(agent_id: str, request: Request):
    csrf(request)
    return await genesis_mutation("PUT", f"/api/genesis/agents/{genesis_agent_id(agent_id)}",
                                  await request.json())


@app.delete("/api/genesis/agents/{agent_id}")
async def genesis_delete(agent_id: str, request: Request):
    csrf(request)
    return await genesis_mutation("DELETE", f"/api/genesis/agents/{genesis_agent_id(agent_id)}",
                                  await request.json())


@app.post("/api/genesis/agents/{agent_id}/clone")
async def genesis_clone(agent_id: str, request: Request):
    csrf(request)
    return await genesis_mutation("POST", f"/api/genesis/agents/{genesis_agent_id(agent_id)}/clone",
                                  await request.json())


@app.get("/api/genesis/agents/{agent_id}/audit")
async def genesis_audit(agent_id: str, request: Request):
    user(request)
    result = await core("GET", f"/api/genesis/agents/{genesis_agent_id(agent_id)}/audit")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


# Draft catalog only. These AX routes do not start agents or execute workflows.
@app.get("/api/genesis/knowledge-bases")
async def genesis_knowledge_list(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/genesis/knowledge-bases")).json(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/genesis/knowledge-bases")
async def genesis_knowledge_create(request: Request):
    csrf(request)
    return await genesis_mutation("POST", "/api/genesis/knowledge-bases", await request.json())


@app.post("/api/genesis/graphs/generate")
async def genesis_graph_generate(request: Request):
    csrf(request)
    return await genesis_mutation("POST", "/api/genesis/graphs/generate",
                                          await request.json())


@app.get("/api/genesis/graphs/{graph_id}")
async def genesis_graph_detail(graph_id: str, request: Request):
    user(request)
    result = await core("GET", f"/api/genesis/graphs/{genesis_agent_id(graph_id)}")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.put("/api/genesis/graphs/{graph_id}")
async def genesis_graph_update(graph_id: str, request: Request):
    csrf(request)
    return await genesis_mutation(
        "PUT", f"/api/genesis/graphs/{genesis_agent_id(graph_id)}",
        await request.json())


@app.get("/api/genesis/graphs")
async def genesis_graph_list(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/genesis/graphs")).json(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/genesis/graphs")
async def genesis_graph_create(request: Request):
    csrf(request)
    return await genesis_mutation("POST", "/api/genesis/graphs", await request.json())


@app.post("/api/genesis/graphs/{graph_id}/assign")
async def genesis_graph_assign(graph_id: str, request: Request):
    csrf(request)
    return await genesis_mutation(
        "POST", f"/api/genesis/graphs/{genesis_agent_id(graph_id)}/assign",
        await request.json())


@app.get("/api/genesis/agents/{agent_id}/workflows")
async def genesis_workflows_for_agent(agent_id: str, request: Request):
    user(request)
    return JSONResponse(
        (await core("GET", f"/api/genesis/agents/{genesis_agent_id(agent_id)}/workflows")).json(),
        headers={"Cache-Control": "no-store"})


# Draft-only policy control UI. Neither API nor simulation authorizes tools.
@app.get("/api/genesis/policies")
async def genesis_policy_list(request: Request):
    user(request)
    result = await core("GET", "/api/genesis/policies")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.post("/api/genesis/policies")
async def genesis_policy_create(request: Request):
    csrf(request)
    return await genesis_mutation("POST", "/api/genesis/policies", await request.json())


@app.get("/api/genesis/policies/{policy_id}")
async def genesis_policy_detail(policy_id: str, request: Request):
    user(request)
    result = await core("GET", f"/api/genesis/policies/{genesis_agent_id(policy_id)}")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.put("/api/genesis/policies/{policy_id}")
async def genesis_policy_update(policy_id: str, request: Request):
    csrf(request)
    return await genesis_mutation("PUT", f"/api/genesis/policies/{genesis_agent_id(policy_id)}",
                                  await request.json())


@app.post("/api/genesis/policies/{policy_id}/simulate")
async def genesis_policy_simulate(policy_id: str, request: Request):
    csrf(request)
    return await genesis_mutation("POST",
                                  f"/api/genesis/policies/{genesis_agent_id(policy_id)}/simulate",
                                  await request.json())


@app.get("/api/genesis/policies/{policy_id}/audit")
async def genesis_policy_audit(policy_id: str, request: Request):
    user(request)
    result = await core("GET", f"/api/genesis/policies/{genesis_agent_id(policy_id)}/audit")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})




# Only the heartbeat accepts a device's own credential. Never attach CORE_TOKEN.
@app.post("/api/nodes/{node_id}/heartbeat")
async def device_heartbeat(node_id: str, request: Request):
    from reign.distributed_nodes import ID_PATTERN, heartbeat_body
    token = request.headers.get("x-node-token", "")
    if not ID_PATTERN.fullmatch(node_id) or not re.fullmatch(r"[a-f0-9]{64}", token):
        raise HTTPException(401, "Node authentication denied")
    try:
        apps = await heartbeat_body(request)
    except ValueError:
        raise HTTPException(422, "Invalid heartbeat") from None
    try:
        upstream = await app.state.http.post(core_origin() + f"/api/nodes/{node_id}/heartbeat",
            headers={"x-node-token": token}, json={"apps": apps} if apps is not None else None,
            follow_redirects=False, timeout=8)
    except httpx.RequestError:
        raise HTTPException(503, "Node inventory unavailable") from None
    if upstream.status_code in (401, 403):
        raise HTTPException(401, "Node authentication denied")
    if upstream.status_code != 200:
        raise HTTPException(503, "Node inventory unavailable")
    try:
        value = upstream.json()
        if not isinstance(value, dict) or value.get("accepted") is not True or value.get("node_id") != node_id:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "Node inventory unavailable") from None
    return JSONResponse({"node_id": node_id, "accepted": True,
                         "inventory_updated": value.get("inventory_updated") is True,
                         "dispatch_enabled": False}, headers={"Cache-Control": "no-store"})

@app.get("/api/nodes")
async def node_inventory(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/nodes")).json(),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/nodes/enroll")
async def node_enroll(request: Request):
    csrf(request)
    result = await core("POST", "/api/nodes/enroll", await request.json())
    # This response includes a one-time node credential. Never cache it.
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.get("/api/nodes/route-preview/{capability}")
async def node_route_preview(capability: str, request: Request):
    user(request)
    if capability not in {"browser.observe", "browser.action", "model.inference",
                          "knowledge.retrieve", "workflow.execute"}:
        raise HTTPException(422, "Unsupported node capability")
    result = await core("GET", f"/api/nodes/route-preview/{capability}")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.post("/api/nodes/{node_id}/disable")
async def node_disable(node_id: str, request: Request):
    csrf(request)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", node_id):
        raise HTTPException(422, "Invalid node ID")
    result = await core("POST", f"/api/nodes/{node_id}/disable")
    return JSONResponse(result.json(), headers={"Cache-Control": "no-store"})


@app.delete("/api/chat/{tid}")
async def chat_delete(tid: str, request: Request):
    csrf(request)
    return (await core("DELETE", f"/api/chat/{session_id(tid)}")).json()


team = team_ax.TeamBridge(app, core_origin, configured, principal, session_csrf, ASSETS)
team.install()

openstl = OpenSTLBridge(core, user, csrf)
openstl.install(app)


@app.get("/connect/openstl")
async def connect_openstl():
    return FileResponse(ASSETS / "openstl.html", headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})


@app.middleware("http")
async def avatar_scope(request: Request, call_next):
    """New/unknown API routes cannot accidentally give avatar accounts owner access."""
    if request.cookies.get("ax_session"):
        try:
            session = principal(request)
        except HTTPException:
            session = None
        if session is not None and session.identity.role != "owner":
            path = request.url.path
            # Public assets and ordinary page redirects must not hit Core.
            sensitive = path in {"/", "/genesis", "/guardrails", "/nodes", "/connect/openstl", "/api/me"} or path == "/api" or path.startswith("/api/")
            if not sensitive:
                return await call_next(request)
            team_allowed = (session.identity.role == "employee"
                            and (path == "/api/team" or path.startswith("/api/team/")))
            if session.identity.role == "employee" and not team_allowed:
                try:
                    await team.current(request, session=session)
                    request.state.console_access = await team.console_access(session)
                except HTTPException as error:
                    return JSONResponse({"detail": "Console access could not be verified"},
                                        status_code=error.status_code, headers={"Cache-Control": "no-store"})
            permitted = (
                path in {"/", "/genesis", "/guardrails", "/nodes", "/api/me", "/api/status",
                         "/api/agent", "/api/voice", "/api/security/status", "/api/nodes"}
                or path.startswith("/api/genesis/")
                or path.startswith("/api/chat")
                or path.startswith("/api/voice/")
            )
            # Work surfaces are separate from owner-only vault, browser session
            # control, node administration and external computer credentials.
            if path.startswith("/api/genesis/policies") and request.method not in {"GET", "HEAD"}:
                permitted = False
            if path in {"/", "/api/me"}:
                return await call_next(request)
            if not team_allowed and not (session.identity.role == "employee" and
                                         getattr(request.state, "console_access", False) and permitted):
                return JSONResponse({"detail": "Owner access required"}, status_code=403,
                                    headers={"Cache-Control": "no-store"})
    return await call_next(request)
