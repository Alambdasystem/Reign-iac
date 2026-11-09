"""Private stdio MCP browser/vault server started only by trusted Core.

Model arguments never select an owner/session/epoch. Core adds that context in
request metadata on its private pipe. There is no public MCP HTTP endpoint,
arbitrary server registry, shell tool, password getter, or script executor.
"""
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import HTTPException
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from reign.config import checked_url
from reign.session_errors import BrowserOccupied, BROWSER_OCCUPIED_MESSAGE
from reign.tool_contracts import CATALOG, READ_TOOLS
from reign.tool_errors import BrowserActionError
from reign.workspace_backend import CoreTools


@asynccontextmanager
async def lifecycle(server):
    from reign.core import app
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        app.state.http = client
        yield {}


server = Server("reign-workspace", version="1.0.0", lifespan=lifecycle)
backend = CoreTools()


@server.list_tools()
async def list_tools():
    return [types.Tool(name=name, description=description,
            inputSchema=schema.model_json_schema(),
            annotations=types.ToolAnnotations(readOnlyHint=name in READ_TOOLS,
                destructiveHint=name not in READ_TOOLS, idempotentHint=name in READ_TOOLS,
                openWorldHint=name.startswith("browser_") or name in {"open_browser", "navigate_browser", "vault_use_account"}))
            for name, (description, schema) in CATALOG.items()]


def trusted_context(meta):
    if not isinstance(meta, dict) or os.getenv("REIGN_MCP_OWNER_ID") != "admin" or meta.get("reign_owner") != "admin":
        raise HTTPException(403, "Trusted workspace identity required")
    sid, epoch = meta.get("reign_session_id"), meta.get("reign_epoch")
    if sid is not None:
        if not isinstance(sid, str):
            raise ValueError()
        sid = str(uuid.UUID(sid))
    if epoch is not None and (type(epoch) is not int or epoch < 0):
        raise ValueError()
    return sid, epoch


def error_result(message, code="tool_unavailable", status=503):
    return {"error": message, "code": code, "status": status}


async def dispatch(name, arguments, meta):
    try:
        sid, epoch = trusted_context(meta)
        if name not in CATALOG:
            raise ValueError()
        args = CATALOG[name][1].model_validate(arguments).model_dump(mode="json")
        if "url" in args:
            checked_url(args["url"])
    except (ValueError, TypeError, HTTPException):
        return error_result("Invalid tool arguments or workspace context", "invalid_tool_context", 403)
    try:
        if name == "workspace_status":
            result = await backend.status()
        elif name == "browser_status":
            result = await backend.session(sid) if sid else {"status": "no_session"}
        elif name == "open_browser":
            result = await backend.session(sid) if sid else await backend.open(args["url"])
        elif name.startswith("vault_") and name != "vault_use_account":
            result = await backend.vault(name, args)
        else:
            if sid is None or epoch is None:
                return error_result("Open a browser and obtain its current control state first", "missing_browser", 409)
            if name == "navigate_browser":
                result = await backend.navigate(sid, args["url"], epoch)
            elif name == "browser_observe":
                result = await backend.observe(sid, epoch)
            elif name == "vault_use_account":
                result = await backend.vault(name, args, sid, epoch)
            else:
                result = await backend.action(name, sid, args, epoch)
        if not isinstance(result, dict) or len(json.dumps(result)) > 50000:
            return error_result("The tool returned an invalid or oversized result")
        return result
    except BrowserOccupied:
        return error_result(BROWSER_OCCUPIED_MESSAGE, "browser_occupied", 409)
    except BrowserActionError as error:
        return error_result(error.detail, error.code, error.status_code)
    except HTTPException as error:
        return error_result("Control changed; no action completed" if error.status_code == 409 else "Workspace tool unavailable",
                            "control_changed" if error.status_code == 409 else "tool_unavailable", error.status_code)
    except Exception as error:
        # VaultError is imported lazily so an unconfigured vault does not prevent
        # status/browser MCP startup or the real pre-deploy voice acceptance gate.
        from reign.vault import VaultError
        if isinstance(error, VaultError):
            return error_result(str(error), error.code, 403 if error.code == "grant_invalid" else
                409 if error.code == "account_exists" else
                404 if error.code == "account_not_found" else
                422 if error.code in {"invalid_account", "origin_not_allowed"} else 503)
        return error_result("Workspace tool unavailable")


# SDK validation can include rejected argument values in its error text. Validate
# against the same schemas ourselves and return only fixed messages instead.
@server.call_tool(validate_input=False)
async def call_tool(name, arguments):
    meta = server.request_context.meta
    return await dispatch(name, arguments, meta.model_dump() if meta else {})


async def main():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
