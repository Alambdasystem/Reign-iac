"""Actual MCP protocol tests with synthetic backends and one real stdio child.

These tests never contact a website, Temporal service, deployed vault, or model.
The memory transport still performs SDK initialize/list_tools/call_tool messages;
it is used to inspect trusted context and deliberately injected backend failures.
"""
import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from reign import mcp_workspace
from reign.mcp_client import WorkspaceMCP
from reign.session_errors import BrowserOccupied, BROWSER_OCCUPIED_MESSAGE
from reign.tool_contracts import CATALOG, READ_TOOLS, tool_specs
from reign.tool_errors import BrowserActionError
from reign.vault import VaultError


SID_A = "11111111-1111-4111-8111-111111111111"
SID_B = "22222222-2222-4222-8222-222222222222"
OBSERVATION = "synthetic_observation_01"
URL = "https://alambda-forge.onrender.com/"
SENSITIVE_FIXTURE = "synthetic-secret-must-not-escape-4387"
EXPECTED_TOOLS = {
    "workspace_status", "browser_status", "open_browser", "navigate_browser",
    "browser_observe", "browser_click", "browser_fill", "browser_press", "browser_scroll",
    "vault_status", "vault_list_accounts", "vault_request_account", "vault_prepare_signup", "vault_use_account",
}


def meta(sid=None, epoch=None):
    value = {"reign_owner": "admin"}
    if sid is not None:
        value["reign_session_id"] = sid
    if epoch is not None:
        value["reign_epoch"] = epoch
    return value


def fake_backend():
    return SimpleNamespace(**{name: AsyncMock(return_value={"synthetic": name})
                             for name in ("status", "session", "open", "navigate", "observe", "action", "vault")})


@asynccontextmanager
async def empty_lifespan(server):
    yield {}


@asynccontextmanager
async def protocol_session(backend):
    # No Core lifespan/network or actual credentials are needed for protocol and
    # context tests. Only the trusted implementation behind MCP is substituted.
    with patch.object(mcp_workspace, "backend", backend), \
         patch.object(mcp_workspace.server, "lifespan", empty_lifespan), \
         patch.dict(os.environ, {"REIGN_MCP_OWNER_ID": "admin", "ALLOWED_HOSTS": "alambda-forge.onrender.com"}):
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            serving = asyncio.create_task(mcp_workspace.server.run(
                *server_streams, mcp_workspace.server.create_initialization_options()))
            try:
                async with ClientSession(*client_streams, read_timeout_seconds=timedelta(seconds=5)) as session:
                    initialized = await session.initialize()
                    yield session, initialized
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)


class WorkspaceMCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    def assert_safe(self, result):
        self.assertNotIn(SENSITIVE_FIXTURE, result.model_dump_json())
        self.assertIsInstance(result.structuredContent, dict)
        return result.structuredContent

    async def test_real_stdio_initialize_exact_catalog_and_safe_calls(self):
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                       "ALLOWED_HOSTS": "alambda-forge.onrender.com"}
        connection = WorkspaceMCP()
        # The child inherits no real provider/worker/vault secrets from the test
        # environment. This is a real fixed-command MCP subprocess, not a mock.
        with patch.dict(os.environ, environment, clear=True):
            async with asyncio.timeout(25):
                async with connection.connect():
                    discovered = await connection.session.list_tools()
                    self.assertEqual({tool.name for tool in discovered.tools}, EXPECTED_TOOLS)
                    self.assertEqual(set(connection.tool_names), EXPECTED_TOOLS)
                    self.assertEqual(await connection.invoke("browser_status"), {"status": "no_session"})
                    status = await connection.invoke("vault_status")
                    self.assertFalse(status["vault"]["ready"])
                    self.assertFalse(status["vault"]["durable"])
                    rejected = await connection.session.call_tool("vault_prepare_signup", {
                        "label": "Synthetic signup", "origin": URL.rstrip("/"),
                        "username": "synthetic@example.invalid", "password": SENSITIVE_FIXTURE,
                    }, meta=meta())
                    self.assertEqual(self.assert_safe(rejected)["code"], "invalid_tool_context")
        self.assertIsNone(connection.session)
        self.assertEqual(connection.tool_names, [])

    async def test_protocol_initialization_and_langchain_schemas_match(self):
        async with protocol_session(fake_backend()) as (session, initialized):
            self.assertEqual(initialized.serverInfo.name, "reign-workspace")
            self.assertIsNotNone(initialized.capabilities.tools)
            discovered = await session.list_tools()
        self.assertEqual({tool.name for tool in discovered.tools}, EXPECTED_TOOLS)
        langchain_tools = tool_specs()
        forbidden = {"owner", "owner_id", "session_id", "sid", "epoch", "password", "token",
                     "selector", "script", "server", "command", "grant_id"}
        for tool in discovered.tools:
            with self.subTest(tool=tool.name):
                self.assertFalse(tool.inputSchema["additionalProperties"])
                self.assertFalse(forbidden & set(tool.inputSchema.get("properties", {})))
                self.assertEqual(tool.inputSchema, langchain_tools[tool.name].args_schema.model_json_schema())
                self.assertEqual(tool.annotations.readOnlyHint, tool.name in READ_TOOLS)

    async def test_missing_or_spoofed_identity_never_reaches_backend(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            for context in ({}, {"reign_owner": "other-admin"}, {"owner": "admin"}):
                result = await session.call_tool("workspace_status", {}, meta=context)
                self.assertEqual(self.assert_safe(result)["code"], "invalid_tool_context")
            with patch.dict(os.environ, {"REIGN_MCP_OWNER_ID": "untrusted"}):
                result = await session.call_tool("workspace_status", {}, meta=meta())
                self.assertEqual(self.assert_safe(result)["status"], 403)
        backend.status.assert_not_awaited()

    async def test_invalid_trusted_metadata_is_a_closed_error(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            contexts = [dict(meta(), reign_session_id=value) for value in
                        (123, {}, [], SENSITIVE_FIXTURE)]
            contexts += [dict(meta(SID_A), reign_epoch=value) for value in (True, -1, 1.5, SENSITIVE_FIXTURE)]
            for context in contexts:
                with self.subTest(keys=list(context)):
                    result = await session.call_tool("browser_status", {}, meta=context)
                    self.assertEqual(self.assert_safe(result)["code"], "invalid_tool_context")
        backend.session.assert_not_awaited()

    async def test_model_arguments_cannot_override_context_or_include_secrets_or_selectors(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            for injected in ("owner", "owner_id", "session_id", "epoch", "password", "selector", "script"):
                result = await session.call_tool("browser_click", {
                    "observation_id": OBSERVATION, "ref": "element_1", injected: SENSITIVE_FIXTURE,
                }, meta=meta(SID_A, 3))
                self.assertEqual(self.assert_safe(result)["code"], "invalid_tool_context")
        backend.action.assert_not_awaited()

    async def test_parallel_requests_keep_separate_request_bound_session_and_epoch(self):
        backend = fake_backend()
        calls = []
        both_entered = asyncio.Event()

        async def navigate(sid, url, epoch):
            calls.append((sid, epoch))
            if len(calls) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=3)
            return {"id": sid, "epoch": epoch, "url": url}

        backend.navigate.side_effect = navigate
        async with protocol_session(backend) as (session, _):
            first, second = await asyncio.gather(
                session.call_tool("navigate_browser", {"url": URL}, meta=meta(SID_A, 7)),
                session.call_tool("navigate_browser", {"url": URL}, meta=meta(SID_B, 29)))
        self.assertEqual(first.structuredContent["id"], SID_A)
        self.assertEqual(first.structuredContent["epoch"], 7)
        self.assertEqual(second.structuredContent["id"], SID_B)
        self.assertEqual(second.structuredContent["epoch"], 29)
        self.assertCountEqual(calls, [(SID_A, 7), (SID_B, 29)])

    async def test_actions_require_context_and_open_reuses_existing_browser_without_navigation(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            for context in (meta(), meta(SID_A), meta(epoch=4)):
                result = await session.call_tool("browser_observe", {}, meta=context)
                self.assertEqual(self.assert_safe(result)["code"], "missing_browser")
            result = await session.call_tool("open_browser", {"url": URL}, meta=meta(SID_A, 4))
            self.assertEqual(result.structuredContent, {"synthetic": "session"})
        backend.observe.assert_not_awaited()
        backend.session.assert_awaited_once_with(SID_A)
        backend.open.assert_not_awaited()
        backend.navigate.assert_not_awaited()

    async def test_url_policy_is_checked_before_backend_dispatch(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            for url in ("http://alambda-forge.onrender.com/", "https://elsewhere.example/",
                        "https://user:" + SENSITIVE_FIXTURE + "@alambda-forge.onrender.com/"):
                result = await session.call_tool("navigate_browser", {"url": url}, meta=meta(SID_A, 7))
                self.assertEqual(self.assert_safe(result)["code"], "invalid_tool_context")
        backend.navigate.assert_not_awaited()

    async def test_safe_backend_errors_keep_closed_codes_without_exception_details(self):
        backend = fake_backend()
        errors = [(RuntimeError(SENSITIVE_FIXTURE), "tool_unavailable", 503),
                  (HTTPException(500, SENSITIVE_FIXTURE), "tool_unavailable", 500),
                  (HTTPException(409, SENSITIVE_FIXTURE), "control_changed", 409),
                  (BrowserOccupied(), "browser_occupied", 409),
                  (BrowserActionError("stale_observation"), "stale_observation", 409),
                  (VaultError("grant_invalid"), "grant_invalid", 403),
                  (VaultError("account_not_found"), "account_not_found", 404),
                  (VaultError("account_exists"), "account_exists", 409),
                  (VaultError("invalid_account"), "invalid_account", 422)]
        async with protocol_session(backend) as (session, _):
            for error, code, status in errors:
                backend.status.side_effect = error
                result = await session.call_tool("workspace_status", {}, meta=meta())
                data = self.assert_safe(result)
                self.assertEqual((data["code"], data["status"]), (code, status))
                if code == "browser_occupied":
                    self.assertEqual(data["error"], BROWSER_OCCUPIED_MESSAGE)

    async def test_invalid_and_oversized_backend_results_fail_closed(self):
        backend = fake_backend()
        async with protocol_session(backend) as (session, _):
            for value in ([SENSITIVE_FIXTURE], {"oversized": SENSITIVE_FIXTURE * 3000}):
                backend.status.return_value = value
                result = await session.call_tool("workspace_status", {}, meta=meta())
                self.assertEqual(self.assert_safe(result)["code"], "tool_unavailable")


class WorkspaceMCPClientTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, result=None):
        connection = WorkspaceMCP()
        connection.session = SimpleNamespace(call_tool=AsyncMock(return_value=result or types.CallToolResult(
            content=[], structuredContent={"synthetic": "ok"}, isError=False)))
        return connection

    async def test_client_validates_arguments_then_stamps_trusted_metadata(self):
        connection = self.connection()
        await connection.invoke("browser_click", {"observation_id": OBSERVATION, "ref": "element_1"}, sid=SID_A, epoch=8)
        connection.session.call_tool.assert_awaited_once_with("browser_click", {
            "observation_id": OBSERVATION, "ref": "element_1"}, meta=meta(SID_A, 8))
        connection.session.call_tool.reset_mock()
        with self.assertRaises(HTTPException) as caught:
            await connection.invoke("browser_click", {
                "observation_id": OBSERVATION, "ref": "element_1", "owner": SENSITIVE_FIXTURE}, sid=SID_A, epoch=8)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertNotIn(SENSITIVE_FIXTURE, str(caught.exception))
        connection.session.call_tool.assert_not_awaited()

    async def test_client_rejects_protocol_errors_even_with_structured_content(self):
        result = types.CallToolResult(content=[types.TextContent(type="text", text=SENSITIVE_FIXTURE)],
                                      structuredContent={"unexpected": SENSITIVE_FIXTURE}, isError=True)
        connection = self.connection(result)
        with self.assertRaises(HTTPException) as caught:
            await connection.invoke("workspace_status")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn(SENSITIVE_FIXTURE, str(caught.exception))

    async def test_lost_effect_response_is_uncertain_and_is_never_replayed(self):
        cases = {"open_browser": {}, "navigate_browser": {},
                 "browser_click": {"observation_id": OBSERVATION, "ref": "element_1"},
                 "vault_prepare_signup": {"label": "Fixture", "origin": URL.rstrip("/"), "username": "fixture"},
                 "vault_use_account": {"account_id": SID_B, "observation_id": OBSERVATION}}
        for name, arguments in cases.items():
            with self.subTest(tool=name):
                connection = self.connection()
                connection.session.call_tool.side_effect = ConnectionError(SENSITIVE_FIXTURE)
                with self.assertRaises(BrowserActionError) as caught:
                    await connection.invoke(name, arguments, sid=SID_A, epoch=8)
                self.assertEqual(caught.exception.code, "action_uncertain")
                self.assertNotIn(SENSITIVE_FIXTURE, str(caught.exception))
                self.assertEqual(connection.session.call_tool.await_count, 1)

    async def test_client_preserves_occupied_and_takeover_fencing(self):
        for code, kind in (("browser_occupied", BrowserOccupied), ("control_changed", HTTPException),
                           ("stale_observation", BrowserActionError)):
            connection = self.connection(types.CallToolResult(content=[], isError=False,
                structuredContent={"error": "closed synthetic error", "code": code, "status": 409}))
            with self.subTest(code=code), self.assertRaises(kind) as caught:
                await connection.invoke("browser_status", sid=SID_A)
            self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
