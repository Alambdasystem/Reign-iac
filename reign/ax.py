"""UltraView AX: authenticated human console for the private REIGN Core."""
import json
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel
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


class AccountError(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ACCOUNT_ERRORS else "vault_unavailable"


async def account_error_response(request, error):
    status, detail = ACCOUNT_ERRORS[error.code]
    return JSONResponse({"code": error.code, "detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def account_authorize(request, write=False):
    try:
        csrf(request) if write else user(request)
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

def user(request: Request):
    cookie = request.cookies.get("ax_session", "")
    try:
        identity = signer().loads(cookie, max_age=3600)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Login required")
    if identity != "admin":
        raise HTTPException(401, "Login required")
    return identity

def csrf(request: Request):
    user(request)
    origin = request.headers.get("origin")
    expected = os.environ.get("AX_PUBLIC_ORIGIN", "").rstrip("/")
    if not expected or origin != expected:
        raise HTTPException(403, "Invalid origin")
    token = request.headers.get("x-csrf-token", "")
    if not token or not secrets.compare_digest(token, request.cookies.get("ax_csrf", "")):
        raise HTTPException(403, "Invalid CSRF token")

@asynccontextmanager
async def lifespan(app):
    # Browser startup/Temporal dispatch can take longer than a normal API call.
    app.state.http = httpx.AsyncClient(timeout=120, trust_env=False)
    yield
    await app.state.http.aclose()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(VoiceError, voice_error_response)
app.add_exception_handler(BrowserOccupied, browser_occupied_response)
app.add_exception_handler(AccountError, account_error_response)
ASSETS = Path(__file__).resolve().parent.parent / "ax_static"
if not ASSETS.is_dir():
    ASSETS = Path(__file__).resolve().parent.parent / "services" / "ax"
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

class Login(BaseModel):
    password: str

@app.get("/")
async def console():
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


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/api/me")
async def me(request: Request):
    user(request)
    return JSONResponse({"authenticated": True, "capabilities": {
        "managed_browser": True, "external_computer": False,
        "voice": True, "ai_chat": True}}, headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def workspace_status(request: Request):
    user(request)
    return JSONResponse((await core("GET", "/api/readyz")).json(),
                        headers={"Cache-Control": "no-store"})

@app.post("/login")
async def login(data: Login):
    password = configured("AX_ADMIN_PASSWORD")
    if not secrets.compare_digest(data.password, password):
        raise HTTPException(401, "Invalid credentials")
    response = JSONResponse({"status": "authenticated"})
    response.set_cookie("ax_session", signer().dumps("admin"), httponly=True, secure=True, samesite="strict", max_age=3600)
    response.set_cookie("ax_csrf", secrets.token_urlsafe(32), httponly=False, secure=True, samesite="strict", max_age=3600)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/logout")
async def logout(request: Request):
    csrf(request)
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("ax_session")
    response.delete_cookie("ax_csrf")
    return response

def session_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(422, "Invalid session identifier")


async def core(method, path, data=None, voice_errors=False, account_errors=False):
    base = os.environ.get("CORE_URL", "").rstrip("/")
    if not base.startswith("https://") and not (os.environ.get("AX_ALLOW_INTERNAL_HTTP") == "1" and base.startswith("http://")):
        if account_errors:
            raise AccountError("vault_unavailable")
        if voice_errors:
            raise VoiceError("voice_core_unavailable")
        raise HTTPException(503, "Core endpoint not configured securely")
    try:
        result = await app.state.http.request(method, base + path,
            headers={"Authorization": "Bearer " + configured("CORE_TOKEN")}, json=data,
            **({"timeout": 40} if voice_errors else {}),
            **({"follow_redirects": False} if account_errors else {}))
    except HTTPException:
        if account_errors:
            raise AccountError("vault_unavailable") from None
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


@app.delete("/api/chat/{tid}")
async def chat_delete(tid: str, request: Request):
    csrf(request)
    return (await core("DELETE", f"/api/chat/{session_id(tid)}")).json()
