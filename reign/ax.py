"""UltraView AX: authenticated human console for the private REIGN Core."""
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel

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

class Login(BaseModel):
    password: str

@app.get("/")
async def console():
    path = Path(__file__).resolve().parent.parent / "ax_static" / "index.html"
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent / "services" / "ax" / "index.html"
    return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

@app.get("/healthz")
async def health():
    return {"status": "ok"}

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


async def core(method, path, data=None):
    base = os.environ.get("CORE_URL", "").rstrip("/")
    if not base.startswith("https://") and not (os.environ.get("AX_ALLOW_INTERNAL_HTTP") == "1" and base.startswith("http://")):
        raise HTTPException(503, "Core endpoint not configured securely")
    try:
        result = await app.state.http.request(method, base + path,
            headers={"Authorization": "Bearer " + configured("CORE_TOKEN")}, json=data)
    except httpx.RequestError:
        raise HTTPException(503, "Core unavailable")
    if result.status_code >= 400:
        raise HTTPException(result.status_code, "Core request failed")
    return result

@app.get("/api/readyz")
async def ready(request: Request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied, configured("CORE_TOKEN")):
        raise HTTPException(401, "Unauthorized")
    return (await core("GET", "/api/readyz")).json()

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
