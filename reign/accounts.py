"""Secure account forms and opaque vault tools for the single-admin pilot."""
import asyncio
import json
import secrets
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from temporalio.client import Client

from reign.config import NAMESPACE, QUEUE, temporal_address
from reign.credential_transport import context, seal_credentials
from reign.vault import VaultError, canonical_origin, get_vault


OWNER = "admin"  # Derived from the authenticated single-admin service boundary.
VAULT_STATUS = {"vault_not_ready": 503, "vault_unavailable": 503,
                "invalid_account": 422, "origin_not_allowed": 422,
                "account_not_found": 404, "account_exists": 409,
                "account_use_conflict": 409, "grant_invalid": 403}


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SaveAccount(SafeModel):
    label: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=8, max_length=512)
    username: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class UseAccount(SafeModel):
    session_id: str
    thread_id: str | None = None


class RedeemGrant(SafeModel):
    grant_id: str = Field(min_length=16, max_length=128)
    session_id: str
    observation_id: str = Field(min_length=16, max_length=100)
    epoch: int = Field(ge=0)
    origin: str = Field(min_length=8, max_length=512)


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise VaultError("invalid_account") from None


def response(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


async def vault_error_response(request, error):
    return response({"code": error.code, "detail": str(error)}, VAULT_STATUS.get(error.code, 503))


async def bounded_body(request, schema):
    # FastAPI's default validation error can echo input values. Account bodies
    # are parsed here and only our fixed errors can reach clients or logs.
    body = bytearray()
    try:
        if request.query_params:
            raise ValueError()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                raise ValueError()
        return schema.model_validate(json.loads(body))
    except Exception:
        raise VaultError("invalid_account") from None
    finally:
        body.clear()


def public_account(account):
    # Tool results don't need usernames. Account selection uses labels and IDs.
    return {key: account[key] for key in ("id", "label", "origin", "created_at", "updated_at")}


async def vault_tool(name, arguments, owner=OWNER, sid=None, epoch=None):
    if owner != OWNER:
        raise VaultError("invalid_account")
    vault = get_vault()
    if name == "vault_status":
        return {"vault": await asyncio.to_thread(vault.status)}
    if name == "vault_request_account":
        origin = canonical_origin(arguments["origin"]) if arguments.get("origin") else ""
        return {"status": "secure_entry_required", "origin": origin,
                "label": arguments.get("label", "")}
    if name == "vault_list_accounts":
        accounts = await asyncio.to_thread(vault.list_accounts, owner)
        return {"accounts": [public_account(account) for account in accounts]}
    if name == "vault_prepare_signup":
        account = await asyncio.to_thread(vault.create_generated_account, owner,
            arguments["label"], arguments["origin"], arguments["username"])
        return {"status": "credential_prepared", "account": public_account(account),
                "external_account_created": False}
    if name != "vault_use_account" or sid is None or epoch is None:
        raise VaultError("invalid_account")
    from reign.core import session, workflow_http_error
    state = await session(uuid.UUID(sid))
    if state["mode"] != "agent" or state["epoch"] != epoch:
        raise HTTPException(409, "Control changed")
    from urllib.parse import urlsplit
    parts = urlsplit(state["url"])
    origin = canonical_origin(parts.scheme + "://" + parts.netloc)
    grant = await asyncio.to_thread(vault.issue_grant, owner, arguments["account_id"],
        sid, arguments["observation_id"], epoch, origin)
    try:
        client = await Client.connect(temporal_address(), namespace=NAMESPACE)
        return await client.execute_workflow("FillAccount", {
            "id": sid, "grant_id": grant["grant_id"],
            "observation_id": arguments["observation_id"], "epoch": epoch},
            id="reign-account-" + str(uuid.uuid4()), task_queue=QUEUE)
    except Exception as error:
        raise workflow_http_error(error, uncertain=True) from None


def install(app, authorize):
    app.add_exception_handler(VaultError, vault_error_response)

    @app.get("/api/accounts", dependencies=[Depends(authorize)])
    async def list_accounts(request: Request):
        if request.query_params:
            raise VaultError("invalid_account")
        vault = get_vault()
        status = await asyncio.to_thread(vault.status)
        accounts = await asyncio.to_thread(vault.list_accounts, OWNER) if status["ready"] else []
        return response({"vault": status, "accounts": accounts})

    @app.post("/api/accounts", dependencies=[Depends(authorize)])
    async def save_account(request: Request):
        data = await bounded_body(request, SaveAccount)
        account = await asyncio.to_thread(get_vault().save_account, OWNER, data.label,
            data.origin, data.username, data.password.get_secret_value())
        return response({"account": account}, 201)

    @app.delete("/api/accounts/{account_id}", dependencies=[Depends(authorize)])
    async def remove_account(account_id: str, request: Request):
        if request.query_params:
            raise VaultError("invalid_account")
        aid = identifier(account_id)
        deleted = await asyncio.to_thread(get_vault().remove_account, OWNER, aid)
        if not deleted:
            raise VaultError("account_not_found")
        return response({"status": "deleted", "id": aid})

    @app.post("/api/accounts/{account_id}/use", dependencies=[Depends(authorize)])
    async def use_account(account_id: str, request: Request):
        aid = identifier(account_id)
        data = await bounded_body(request, UseAccount)
        sid, tid = identifier(data.session_id), identifier(data.thread_id) if data.thread_id else None
        accounts = await asyncio.to_thread(get_vault().list_accounts, OWNER)
        if not any(account["id"] == aid for account in accounts):
            raise VaultError("account_not_found")
        # The button joins exactly the same graph and voice/thread lease as text.
        prompt = ("Use saved account " + aid + " to sign into the current website. "
                  "Observe the page first, fill through the vault, and verify the resulting page. "
                  "Do not create an external account, accept terms, or bypass human verification.")
        try:
            result = app.state.agent.submit(prompt, tid, sid)
        except HTTPException as error:
            if error.status_code == 409:
                return response({"code": "account_use_conflict", "detail": "Finish or resume the current conversation before using an account."}, 409)
            raise
        return response(result)

    async def authorize_worker(request: Request):
        import os
        expected = os.getenv("COMPUTER_TOKEN", "")
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if len(expected) < 32 or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "Unauthorized")

    @app.post("/api/vault/grants/redeem", dependencies=[Depends(authorize_worker)])
    async def redeem(request: Request):
        data = await bounded_body(request, RedeemGrant)
        sid = identifier(data.session_id)
        origin = canonical_origin(data.origin)
        credentials = await asyncio.to_thread(get_vault().redeem_grant,
            data.grant_id, sid, data.observation_id, data.epoch, origin)
        try:
            sealed = seal_credentials(credentials, context(data.grant_id, sid,
                data.observation_id, data.epoch, origin))
            return response(sealed)
        finally:
            credentials.clear()
