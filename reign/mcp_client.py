"""One private MCP stdio connection shared by the Core's LangGraph runs.

Only this fixed local server can be started. The model cannot choose servers,
commands, environment values, owners, sessions, epochs, or credential secrets.
"""
import asyncio
import contextlib
import os
import sys
from datetime import timedelta
from pathlib import Path

import anyio
from fastapi import HTTPException
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from reign.session_errors import BrowserOccupied
from reign.tool_contracts import CATALOG, EFFECT_TOOLS
from reign.tool_errors import ACTION_MESSAGES, BrowserActionError


class CancellableClientSession(ClientSession):
    """Forward local tools/call cancellation through the pinned MCP1.30 SDK.

    BaseSession.send_request allocates _request_id before its first await but
    does not notify the server when its waiter is cancelled. Capture that ID at
    this narrow, covered boundary; ordinary SDK requests remain unchanged.
    """
    async def send_request(self, request, result_type, *args, **kwargs):
        tool_call = isinstance(request.root, types.CallToolRequest)
        request_id = self._request_id if tool_call else None
        try:
            return await super().send_request(request, result_type, *args, **kwargs)
        except asyncio.CancelledError:
            if request_id is not None:
                await self._cancel_tool_request(request_id)
            raise
        except McpError as error:
            if request_id is not None and error.error.code == 408:
                await self._cancel_tool_request(request_id)
            raise

    async def _cancel_tool_request(self, request_id):
        with anyio.move_on_after(1.0, shield=True):
            try:
                await self.send_notification(types.ClientNotification(types.CancelledNotification(
                    params=types.CancelledNotificationParams(requestId=request_id,
                        reason="REIGN caller cancelled; completed actions are not undone"))))
            except (Exception, asyncio.CancelledError):
                pass


class WorkspaceMCP:
    def __init__(self):
        self.session = None
        self.tool_names = []
        self._lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def connect(self):
        names = {"PATH", "LANG", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
                 "TEMPORAL_ADDRESS", "TEMPORAL_HOST", "TEMPORAL_NAMESPACE",
                 "TEMPORAL_TASK_QUEUE", "COMPUTER_URL", "COMPUTER_HOST",
                 "COMPUTER_TOKEN", "ALLOWED_HOSTS", "REIGN_VAULT_DIR", "REIGN_VAULT_KEY"}
        env = {name: value for name, value in os.environ.items() if name in names}
        env["REIGN_MCP_OWNER_ID"] = "admin"
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "reign.mcp_workspace"], env=env,
            cwd=str(Path(__file__).resolve().parent.parent))
        # The MCP SDK can log exception details to stderr. Keep this child's
        # stderr private; expose only our closed public error result instead.
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as (reader, writer):
                async with CancellableClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=110)) as session:
                    await session.initialize()
                    discovered = await session.list_tools()
                    actual = {tool.name: tool.inputSchema for tool in discovered.tools}
                    expected = {name: schema.model_json_schema() for name, (_, schema) in CATALOG.items()}
                    if actual != expected:
                        raise RuntimeError("Workspace MCP tool catalog does not match this release")
                    self.session, self.tool_names = session, list(actual)
                    try:
                        yield self
                    finally:
                        self.session, self.tool_names = None, []

    async def invoke(self, name, arguments=None, *, sid=None, epoch=None):
        if self.session is None or name not in CATALOG:
            raise HTTPException(503, "Workspace MCP is unavailable")
        schema = CATALOG[name][1]
        try:
            args = schema.model_validate(arguments or {}).model_dump(mode="json")
        except Exception:
            raise HTTPException(422, "Invalid tool arguments") from None
        meta = {"reign_owner": "admin"}
        if sid is not None:
            meta["reign_session_id"] = sid
        if epoch is not None:
            meta["reign_epoch"] = epoch
        try:
            # Keep account effects and browser actions serialized at this boundary.
            async with self._lock:
                result = await self.session.call_tool(name, args, meta=meta)
            data = result.structuredContent
            if result.isError or not isinstance(data, dict):
                raise ValueError()
        except Exception:
            if name in EFFECT_TOOLS or name == "vault_prepare_signup":
                raise BrowserActionError("action_uncertain") from None
            raise HTTPException(503, "Workspace MCP is unavailable") from None
        if "error" in data:
            code = data.get("code")
            if code == "browser_occupied":
                raise BrowserOccupied()
            if code in ACTION_MESSAGES:
                raise BrowserActionError(code)
            if code == "control_changed":
                raise HTTPException(409, "Control changed")
            # Preserve only safe, closed error metadata from the private server.
            return {key: data[key] for key in ("error", "code", "status") if key in data}
        return data


class MCPTools:
    def __init__(self, connection):
        self.connection = connection

    async def status(self):
        return await self.connection.invoke("workspace_status")

    async def session(self, sid):
        return await self.connection.invoke("browser_status", sid=sid)

    async def open(self, url):
        return await self.connection.invoke("open_browser", {"url": url})

    async def navigate(self, sid, url, epoch):
        return await self.connection.invoke("navigate_browser", {"url": url}, sid=sid, epoch=epoch)

    async def observe(self, sid, epoch):
        return await self.connection.invoke("browser_observe", sid=sid, epoch=epoch)

    async def action(self, name, sid, arguments, epoch):
        return await self.connection.invoke(name, arguments, sid=sid, epoch=epoch)

    async def vault(self, name, arguments=None, sid=None, epoch=None):
        return await self.connection.invoke(name, arguments, sid=sid, epoch=epoch)
