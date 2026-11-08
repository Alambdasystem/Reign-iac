"""Private REIGN Core gateway for Temporal workflows and Computer sessions."""
import os
import secrets
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest

from reign.config import QUEUE, NAMESPACE, checked_url, computer_url, temporal_address


def authorize(request: Request):
    expected = os.environ.get("CORE_TOKEN", "")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if len(expected) < 32 or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=60.0, trust_env=False)
    from reign.agent import AgentRuntime
    app.state.agent = AgentRuntime()
    yield
    await app.state.agent.shutdown()
    await app.state.http.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class OpenRequest(BaseModel):
    url: str = "https://alambda-forge.onrender.com/"


def workflow_http_error(error):
    """Preserve expected worker rejections across Temporal's exception wrappers."""
    cause = error
    for _ in range(5):
        if isinstance(cause, ApplicationError) and cause.type == "ControlChanged":
            return HTTPException(409, "Control changed; refresh the session")
        cause = getattr(cause, "cause", None)
        if cause is None:
            break
    return HTTPException(503, "Computer workflow failed")


class NavigateRequest(BaseModel):
    url: str
    epoch: int = Field(ge=0)


class ControlRequest(BaseModel):
    mode: str = Field(pattern="^(agent|human)$")
    epoch: int = Field(ge=0)


class InputRequest(BaseModel):
    kind: str = Field(pattern="^(click|text|key|scroll)$")
    epoch: int = Field(ge=0)
    x: float = Field(default=0, ge=0, lt=1280)
    y: float = Field(default=0, ge=0, lt=800)
    text: str = Field(default="", max_length=2000)
    key: str = Field(default="", max_length=30)
    dy: int = Field(default=0, ge=-1000, le=1000)


def worker_headers():
    token = os.environ.get("COMPUTER_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "Computer token not configured")
    return {"Authorization": "Bearer " + token}


async def worker(method: str, path: str, payload=None):
    try:
        response = await app.state.http.request(
            method, computer_url().rstrip("/") + path,
            headers=worker_headers(), json=payload,
        )
    except httpx.RequestError:
        raise HTTPException(503, "Computer worker unreachable")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, "Computer worker request failed")
    return response


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/api/readyz", dependencies=[Depends(authorize)])
async def ready():
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        await client.workflow_service.get_system_info(GetSystemInfoRequest())
        await worker("GET", "/healthz")
    except Exception:
        raise HTTPException(503, "Dependencies not ready")
    return {"status": "ready", "components": {"core": "ready", "temporal": "ready", "computer": "ready"}}


@app.get("/api/database/readyz", dependencies=[Depends(authorize)])
async def database_ready():
    """Read-only REIGN schema permission probe, never a migration endpoint."""
    from reign.database import probe_reign_database
    try:
        permitted = await probe_reign_database()
    except Exception:
        raise HTTPException(503, "REIGN database unavailable")
    if not permitted:
        raise HTTPException(503, "REIGN database role lacks schema access")
    return {"status": "ready", "schema": "reign"}


@app.post("/api/sessions", dependencies=[Depends(authorize)])
async def open_session(data: OpenRequest):
    try:
        checked_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    sid = str(uuid.uuid4())
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        result = await client.execute_workflow("OpenBrowser", {"id": sid, "url": data.url},
                                               id="reign-open-" + sid, task_queue=QUEUE)
        return result
    except Exception:
        raise HTTPException(503, "Could not open computer session")


@app.get("/api/sessions/{sid}", dependencies=[Depends(authorize)])
async def session(sid: uuid.UUID):
    return (await worker("GET", f"/sessions/{sid}")).json()


@app.get("/api/sessions/{sid}/frame", dependencies=[Depends(authorize)])
async def frame(sid: uuid.UUID):
    result = await worker("GET", f"/sessions/{sid}/frame")
    headers = {"Cache-Control": "no-store"}
    for name in ("X-Frame-Sequence", "X-Frame-Timestamp", "X-Session-ID"):
        if name in result.headers:
            headers[name] = result.headers[name]
    return Response(result.content, media_type="image/jpeg", headers=headers)


@app.post("/api/sessions/{sid}/navigate", dependencies=[Depends(authorize)])
async def navigate(sid: uuid.UUID, data: NavigateRequest):
    try:
        checked_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        return await client.execute_workflow("NavigateBrowser",
            {"id": str(sid), "url": data.url, "epoch": data.epoch},
            id="reign-navigate-" + str(uuid.uuid4()), task_queue=QUEUE)
    except Exception as error:
        raise workflow_http_error(error) from error


@app.post("/api/sessions/{sid}/control", dependencies=[Depends(authorize)])
async def control(sid: uuid.UUID, data: ControlRequest):
    return (await worker("POST", f"/sessions/{sid}/control", data.model_dump())).json()


@app.post("/api/sessions/{sid}/input", dependencies=[Depends(authorize)])
async def input_event(sid: uuid.UUID, data: InputRequest):
    return (await worker("POST", f"/sessions/{sid}/input", data.model_dump())).json()


@app.delete("/api/sessions/{sid}", dependencies=[Depends(authorize)])
async def close(sid: uuid.UUID):
    return (await worker("DELETE", f"/sessions/{sid}")).json()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


@app.get("/api/agent", dependencies=[Depends(authorize)])
async def agent_capabilities():
    return app.state.agent.capabilities()


@app.post("/api/chat", dependencies=[Depends(authorize)])
async def chat(data: ChatRequest):
    return app.state.agent.submit(data.message, str(data.thread_id) if data.thread_id else None,
                                  str(data.session_id) if data.session_id else None)


@app.get("/api/chat/{tid}", dependencies=[Depends(authorize)])
async def chat_state(tid: uuid.UUID):
    return app.state.agent.get(str(tid)).snapshot()


@app.post("/api/chat/{tid}/resume", dependencies=[Depends(authorize)])
async def chat_resume(tid: uuid.UUID):
    return await app.state.agent.resume(str(tid))


@app.delete("/api/chat/{tid}", dependencies=[Depends(authorize)])
async def chat_delete(tid: uuid.UUID):
    return await app.state.agent.delete(str(tid))
