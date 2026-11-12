"""Private REIGN Core gateway for Temporal workflows and Computer sessions."""
import os
import secrets
import uuid
from typing import Literal
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest

from reign.config import QUEUE, NAMESPACE, checked_url, computer_url, temporal_address
from reign.session_errors import BrowserOccupied, browser_occupied_response
from reign.owned_workflow import execute_owned_workflow
from reign.tool_errors import ACTION_MESSAGES, BrowserActionError, browser_action_error_response
from reign.voice import VoiceBroker, VoiceError, capabilities as voice_capabilities, read_offer, voice_error_response


def authorize(request: Request):
    expected = os.environ.get("CORE_TOKEN", "")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if len(expected) < 32 or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=60.0, trust_env=False)
    from reign.agent import AgentRuntime
    from reign.mcp_client import MCPTools, WorkspaceMCP
    # Opt-in only. A missing disk, invalid key or failed archive initialization
    # aborts startup rather than switching silently to process-local history.
    archive = None
    if os.environ.get("REIGN_HISTORY_MODE", "disabled") == "disk":
        from reign.disk_history import HistoryArchive
        archive = HistoryArchive.from_env()
    genesis = None
    if os.environ.get("GENESIS_DRAFT_MODE", "disabled") == "enabled":
        from reign.genesis_pilot import GenesisPilot
        genesis = GenesisPilot.from_env()
    try:
        async with WorkspaceMCP().connect() as connection:
            app.state.agent = AgentRuntime(adapter=MCPTools(connection), archive=archive)
            app.state.genesis = genesis
            app.state.voice = VoiceBroker(app.state.http)
            try:
                yield
            finally:
                await app.state.agent.shutdown()
    finally:
        await app.state.http.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(VoiceError, voice_error_response)
app.add_exception_handler(BrowserOccupied, browser_occupied_response)
app.add_exception_handler(BrowserActionError, browser_action_error_response)


class OpenRequest(BaseModel):
    url: str = "https://alambda-forge.onrender.com/"


def workflow_http_error(error, uncertain=False):
    """Preserve expected worker rejections across Temporal's exception wrappers."""
    cause = error
    for _ in range(5):
        if isinstance(cause, ApplicationError):
            if cause.type == "ControlChanged":
                return HTTPException(409, "Control changed; refresh the session")
            if cause.type == "WorkerOccupied":
                return BrowserOccupied()
            if cause.type in {"BrowserActionRejected", "BrowserActionUncertain"}:
                code = getattr(cause, "message", "")
                return BrowserActionError(code if code in ACTION_MESSAGES else "action_uncertain")
        cause = getattr(cause, "cause", None)
        if cause is None:
            break
    return BrowserActionError("action_uncertain") if uncertain else HTTPException(503, "Computer workflow failed")


class NavigateRequest(BaseModel):
    url: str
    epoch: int = Field(ge=0)


class BrowserEffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=0)
    observation_id: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["click", "fill", "press", "scroll"]
    ref: str | None = Field(default=None, max_length=64)
    text: str = Field(default="", max_length=2000)
    key: str = Field(default="", max_length=30)
    dy: int = Field(default=0, ge=-1000, le=1000)


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
        try:
            code = response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        if code in ACTION_MESSAGES:
            raise BrowserActionError(code)
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


@app.get("/api/history/readyz", dependencies=[Depends(authorize)])
async def history_readiness():
    """Read-only probe; distinguish durable transcript from graph checkpoints."""
    if getattr(app.state.agent, "archive", None) is not None:
        return {"status": "ready", "storage": "encrypted_core_disk",
                "activation": "durable_transcript", "graph_checkpoints": "ephemeral"}
    from reign.history_readiness import history_ready
    if not await history_ready():
        raise HTTPException(503, "Durable history is not ready")
    return {"status": "preflight_passed", "storage": "encrypted_postgres",
            "activation": "disabled"}


@app.get("/api/history/conversations", dependencies=[Depends(authorize)])
async def history_conversations():
    """Authenticated Core-only list of thread identifiers; no message contents."""
    archive = getattr(app.state.agent, "archive", None)
    if archive is None:
        raise HTTPException(503, "History archive is not enabled")
    return {"conversations": archive.list_threads()}


@app.post("/api/sessions", dependencies=[Depends(authorize)])
async def open_session(data: OpenRequest):
    try:
        checked_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    sid = str(uuid.uuid4())
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        result = await execute_owned_workflow(client, "OpenBrowser", {"id": sid, "url": data.url},
                                               id="reign-open-" + sid, task_queue=QUEUE)
        return result
    except Exception as error:
        raise workflow_http_error(error, uncertain=True) from None


@app.get("/api/sessions/active", dependencies=[Depends(authorize)])
async def active_session():
    return JSONResponse((await worker("GET", "/sessions/active")).json(),
                        headers={"Cache-Control": "no-store"})


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


@app.get("/api/sessions/{sid}/observe", dependencies=[Depends(authorize)])
async def observe(sid: uuid.UUID, epoch: int):
    if epoch < 0:
        raise HTTPException(422, "Invalid control epoch")
    return (await worker("GET", f"/sessions/{sid}/observe?epoch={epoch}")).json()


@app.post("/api/sessions/{sid}/action", dependencies=[Depends(authorize)])
async def browser_effect(sid: uuid.UUID, data: BrowserEffectRequest):
    payload = data.model_dump(exclude_none=True, exclude_unset=True)
    # The no-text preflight rejects secret targets before ordinary fill data
    # can enter Temporal history. The activity rechecks the same fences.
    preflight = {key: value for key, value in payload.items() if key != "text"}
    await worker("POST", f"/sessions/{sid}/validate-action", preflight)
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        return await execute_owned_workflow(client, "BrowserAction", {"id": str(sid), **payload},
            id="reign-action-" + str(uuid.uuid4()), task_queue=QUEUE)
    except Exception as error:
        raise workflow_http_error(error, uncertain=True) from None


@app.post("/api/sessions/{sid}/navigate", dependencies=[Depends(authorize)])
async def navigate(sid: uuid.UUID, data: NavigateRequest):
    try:
        checked_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        return await execute_owned_workflow(client, "NavigateBrowser",
            {"id": str(sid), "url": data.url, "epoch": data.epoch},
            id="reign-navigate-" + str(uuid.uuid4()), task_queue=QUEUE)
    except Exception as error:
        raise workflow_http_error(error, uncertain=True) from None


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


@app.get("/api/voice", dependencies=[Depends(authorize)])
async def voice_status():
    return JSONResponse(voice_capabilities(), headers={"Cache-Control": "no-store"})


@app.post("/api/voice/offer", dependencies=[Depends(authorize)])
async def voice_offer(request: Request):
    return JSONResponse(await app.state.voice.offer(await read_offer(request)),
                        headers={"Cache-Control": "no-store"})


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




class GenesisDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=12)


def genesis_pilot():
    pilot = getattr(app.state, "genesis", None)
    if pilot is None:
        raise HTTPException(503, "Genesis draft registration unavailable")
    return pilot


@app.get("/api/genesis/status", dependencies=[Depends(authorize)])
async def genesis_status():
    return genesis_pilot().status()


@app.get("/api/genesis/agents", dependencies=[Depends(authorize)])
async def genesis_agents():
    return {"agents": genesis_pilot().list_drafts()}


@app.post("/api/genesis/preview", dependencies=[Depends(authorize)])
async def genesis_preview(data: GenesisDraftRequest):
    from reign.sovereign_registry import RegistryDenied
    try:
        return genesis_pilot().preview(data.agent_id, data.name, data.capabilities)
    except (RegistryDenied, ValueError):
        raise HTTPException(422, "Genesis request denied") from None


@app.post("/api/genesis/agents", dependencies=[Depends(authorize)])
async def genesis_register(data: GenesisDraftRequest):
    from reign.sovereign_registry import RegistryDenied
    try:
        return genesis_pilot().create_draft(data.agent_id, data.name, data.capabilities)
    except (RegistryDenied, ValueError):
        raise HTTPException(422, "Genesis registration denied") from None

class GenesisUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=12)
    expected_revision: int = Field(ge=1)


class GenesisRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class GenesisCloneRequest(GenesisRevisionRequest):
    new_agent_id: str = Field(min_length=2, max_length=63)


def genesis_lifecycle_error(error):
    from reign.genesis_pilot import DraftMissing, DraftConflict
    from reign.sovereign_registry import RegistryDenied
    if isinstance(error, DraftMissing):
        raise HTTPException(404, "Genesis draft not found") from None
    if isinstance(error, DraftConflict):
        raise HTTPException(409, "Genesis draft revision conflict") from None
    if isinstance(error, (RegistryDenied, ValueError)):
        raise HTTPException(422, "Genesis draft action denied") from None
    raise error


@app.get("/api/genesis/agents/{agent_id}", dependencies=[Depends(authorize)])
async def genesis_draft_detail(agent_id: str):
    try:
        return genesis_pilot().get_draft(agent_id)
    except Exception as error:
        genesis_lifecycle_error(error)


@app.put("/api/genesis/agents/{agent_id}", dependencies=[Depends(authorize)])
async def genesis_draft_update(agent_id: str, data: GenesisUpdateRequest):
    try:
        return genesis_pilot().update_draft(
            agent_id, data.name, data.capabilities, expected_revision=data.expected_revision)
    except Exception as error:
        genesis_lifecycle_error(error)


@app.delete("/api/genesis/agents/{agent_id}", dependencies=[Depends(authorize)])
async def genesis_draft_delete(agent_id: str, data: GenesisRevisionRequest):
    try:
        return genesis_pilot().delete_draft(agent_id, expected_revision=data.expected_revision)
    except Exception as error:
        genesis_lifecycle_error(error)


@app.post("/api/genesis/agents/{agent_id}/clone", dependencies=[Depends(authorize)])
async def genesis_draft_clone(agent_id: str, data: GenesisCloneRequest):
    try:
        return genesis_pilot().clone_draft(
            agent_id, data.new_agent_id, expected_revision=data.expected_revision)
    except Exception as error:
        genesis_lifecycle_error(error)


@app.get("/api/genesis/agents/{agent_id}/audit", dependencies=[Depends(authorize)])
async def genesis_draft_audit(agent_id: str):
    return {"events": genesis_pilot().audit_events(agent_id)}


# Genesis Studio control-plane definitions. No graph run, KB retrieval or
# agent activation endpoints are exposed by this release.
class GenesisKBDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kb_id: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=120)


class GenesisGraphDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_id: str = Field(min_length=2, max_length=63)
    name: str = Field(min_length=1, max_length=120)
    spec: dict


class GenesisGraphAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=2, max_length=63)


def genesis_catalog():
    from reign.genesis_catalog import GenesisCatalog
    # Catalog uses the same already-verified private Core disk and pilot identity.
    return GenesisCatalog(genesis_pilot())


def genesis_catalog_error(error):
    from reign.genesis_catalog import CatalogMissing, CatalogConflict
    from reign.genesis_pilot import DraftMissing
    if isinstance(error, (CatalogMissing, DraftMissing)):
        raise HTTPException(404, "Genesis catalog reference missing") from None
    if isinstance(error, CatalogConflict):
        raise HTTPException(409, "Genesis catalog definition already exists") from None
    if isinstance(error, ValueError):
        raise HTTPException(422, "Genesis catalog definition rejected") from None
    raise error


@app.get("/api/genesis/knowledge-bases", dependencies=[Depends(authorize)])
async def genesis_kb_list():
    return {"knowledge_bases": genesis_catalog().list_kbs()}


@app.post("/api/genesis/knowledge-bases", dependencies=[Depends(authorize)])
async def genesis_kb_create(data: GenesisKBDefinition):
    try:
        return genesis_catalog().create_kb(data.kb_id, data.name)
    except Exception as error:
        genesis_catalog_error(error)


class GenesisGraphTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["knowledge_review", "approval_action"]
    agent_id: str = Field(min_length=2, max_length=63)
    kb_id: str = Field(min_length=2, max_length=63)


class GenesisGraphUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    spec: dict
    expected_revision: int = Field(ge=1)


@app.post("/api/genesis/graphs/generate", dependencies=[Depends(authorize)])
async def genesis_generate_graph(data: GenesisGraphTemplateRequest):
    try:
        return genesis_catalog().generate_graph(
            data.template, data.agent_id, data.kb_id)
    except Exception as error:
        genesis_catalog_error(error)


@app.get("/api/genesis/graphs/{graph_id}", dependencies=[Depends(authorize)])
async def genesis_graph_detail(graph_id: str):
    try:
        return genesis_catalog().get_graph(graph_id)
    except Exception as error:
        genesis_catalog_error(error)


@app.put("/api/genesis/graphs/{graph_id}", dependencies=[Depends(authorize)])
async def genesis_graph_update(graph_id: str, data: GenesisGraphUpdateRequest):
    try:
        return genesis_catalog().update_graph(
            graph_id, data.name, data.spec, data.expected_revision)
    except Exception as error:
        genesis_catalog_error(error)


@app.get("/api/genesis/graphs", dependencies=[Depends(authorize)])
async def genesis_graph_list():
    return {"graphs": genesis_catalog().list_graphs()}


@app.post("/api/genesis/graphs", dependencies=[Depends(authorize)])
async def genesis_graph_create(data: GenesisGraphDefinition):
    try:
        return genesis_catalog().create_graph(data.graph_id, data.name, data.spec)
    except Exception as error:
        genesis_catalog_error(error)


@app.post("/api/genesis/graphs/{graph_id}/assign", dependencies=[Depends(authorize)])
async def genesis_graph_assign(graph_id: str, data: GenesisGraphAssignment):
    try:
        return genesis_catalog().assign_draft(graph_id, data.agent_id)
    except Exception as error:
        genesis_catalog_error(error)


@app.get("/api/genesis/agents/{agent_id}/workflows", dependencies=[Depends(authorize)])
async def genesis_assigned_workflows(agent_id: str):
    try:
        return {"workflows": genesis_catalog().workflows_for_agent(agent_id),
                "runnable": False}
    except Exception as error:
        genesis_catalog_error(error)


from reign.accounts import install as install_accounts, vault_tool
install_accounts(app, authorize)
