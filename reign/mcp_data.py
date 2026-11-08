"""Local stdio MCP tools for one authenticated REIGN owner.

This process must be spawned by the trusted Core service with a server-assigned
owner ID and scopes. Do not expose it as a public HTTP endpoint or let a model
supply its own owner identity.
"""
import os
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from reign.data_tools import DataAction, DataCommand, DataPrincipal
from reign.database import connect_reign_database


mcp = FastMCP("reign-data")


def principal() -> DataPrincipal:
    owner = os.environ.get("REIGN_MCP_OWNER_ID", "")
    scopes = frozenset(os.environ.get("REIGN_MCP_SCOPES", "").split(","))
    if not owner or not scopes.intersection({"sessions:read", "tasks:write"}):
        raise PermissionError("MCP owner identity and scopes must be supplied by Core")
    return DataPrincipal(owner, scopes)


async def dispatch(action: DataAction, arguments: dict):
    command = DataCommand(action, principal(), arguments)
    async with connect_reign_database() as gateway:
        result = await gateway.execute(command)
    if isinstance(result, list):
        return [dict(item) for item in result]
    if result is None:
        return None
    if isinstance(result, str):
        return {"status": result}
    return dict(result)


@mcp.tool()
async def get_session(session_id: str) -> dict:
    """Get one REIGN computer session owned by the authenticated user."""
    UUID(session_id)
    return await dispatch(DataAction.GET_SESSION, {"session_id": session_id}) or {}


@mcp.tool()
async def list_sessions(limit: int = 20) -> list[dict]:
    """List the authenticated user's REIGN computer sessions (max 100)."""
    return await dispatch(DataAction.LIST_SESSIONS, {"limit": limit})


@mcp.tool()
async def save_task_result(task_id: str, result: str) -> dict:
    """Save a bounded result for a task owned by the authenticated user."""
    UUID(task_id)
    return await dispatch(DataAction.SAVE_TASK_RESULT,
                          {"task_id": task_id, "result": result})


if __name__ == "__main__":
    mcp.run(transport="stdio")
