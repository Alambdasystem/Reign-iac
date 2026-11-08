"""Policy boundary for REIGN database tools exposed to an MCP server.

The MCP transport is intentionally separate. This module is a narrow, auditable
allowlist; it never accepts SQL or arbitrary table names from an agent.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class DataAction(str, Enum):
    GET_SESSION = "get_session"
    LIST_SESSIONS = "list_sessions"
    SAVE_TASK_RESULT = "save_task_result"


@dataclass(frozen=True)
class DataPrincipal:
    owner_id: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class DataCommand:
    action: DataAction
    principal: DataPrincipal
    arguments: Mapping[str, Any]


class AccessDenied(ValueError):
    pass


def validate_command(command: DataCommand) -> dict[str, Any]:
    """Return canonical, bounded arguments for a parameterized DB operation."""
    if not command.principal.owner_id or len(command.principal.owner_id) > 128:
        raise AccessDenied("Authenticated owner required")
    if not isinstance(command.action, DataAction):
        raise AccessDenied("Unsupported operation")
    required = {
        DataAction.GET_SESSION: "sessions:read",
        DataAction.LIST_SESSIONS: "sessions:read",
        DataAction.SAVE_TASK_RESULT: "tasks:write",
    }[command.action]
    if required not in command.principal.scopes:
        raise AccessDenied("Insufficient scope")
    args = dict(command.arguments)
    if command.action == DataAction.GET_SESSION:
        if set(args) != {"session_id"}:
            raise AccessDenied("Unexpected parameters")
        return {"session_id": str(UUID(str(args["session_id"])))}
    if command.action == DataAction.LIST_SESSIONS:
        if set(args) - {"limit"}:
            raise AccessDenied("Unexpected parameters")
        limit = args.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AccessDenied("Invalid limit")
        return {"limit": limit}
    if set(args) != {"task_id", "result"}:
        raise AccessDenied("Unexpected parameters")
    result = args["result"]
    if not isinstance(result, str) or len(result) > 8192:
        raise AccessDenied("Result must be text under 8192 characters")
    return {"task_id": str(UUID(str(args["task_id"]))), "result": result}


class SessionDataGateway:
    """Use a dedicated REIGN-only DB role and parameterized driver.

    The supplied connection must expose an async fetch/fetchrow/execute API.
    Schema creation and credentials are managed outside the MCP request path.
    """
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, command: DataCommand):
        args = validate_command(command)
        owner = command.principal.owner_id
        if command.action == DataAction.GET_SESSION:
            return await self.connection.fetchrow(
                "SELECT session_id, profile_id, status FROM reign.sessions "
                "WHERE session_id=$1 AND owner_id=$2",
                UUID(args["session_id"]), owner)
        if command.action == DataAction.LIST_SESSIONS:
            return await self.connection.fetch(
                "SELECT session_id, profile_id, status FROM reign.sessions "
                "WHERE owner_id=$1 ORDER BY created_at DESC LIMIT $2",
                owner, args["limit"])
        return await self.connection.execute(
            "UPDATE reign.tasks SET result=$1, updated_at=NOW() "
            "WHERE task_id=$2 AND owner_id=$3",
            args["result"], UUID(args["task_id"]), owner)
