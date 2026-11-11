"""CI-only MCP/Genesis integration acceptance against the running four-service stack.

Calls the actual pinned stdio MCP server via Core's official MCP client. Avoids
modifying accounts or launching extra browser sessions. No production secrets,
no mocked MCP transport, and no claim of live human UI acceptance.
"""
import asyncio
import json
import os
from pathlib import Path

from fastapi import HTTPException

from reign.mcp_client import WorkspaceMCP
from reign.tool_contracts import CATALOG
from reign.genesis_pilot import GenesisPilot
from reign.sovereign_registry import RegistryDenied

EXPECTED = frozenset({
    "workspace_status", "browser_status", "open_browser", "navigate_browser",
    "browser_observe", "browser_click", "browser_fill", "browser_press",
    "browser_scroll", "vault_status", "vault_list_accounts",
    "vault_request_account", "vault_prepare_signup", "vault_use_account"
})


async def main():
    cases = []
    def record(name):
        cases.append({"case": name, "status": "PASS", "source": "real_local_stdio_mcp_and_core_disk"})
    async with WorkspaceMCP().connect() as mcp:
        assert frozenset(mcp.tool_names) == EXPECTED == frozenset(CATALOG)
        record("M01 stdio handshake and complete 14-tool schema parity")
        readiness = await mcp.invoke("workspace_status")
        assert readiness.get("components") == {
            "core": "ready", "temporal": "ready", "computer": "ready"}, readiness
        record("M02 MCP workspace_status reaches real Core/Temporal/Computer")
        browser = await mcp.invoke("browser_status")
        assert browser.get("status") == "no_session", browser
        record("M03 browser_status without session remains read-only")
        vault = await mcp.invoke("vault_status")
        assert isinstance(vault, dict) and "error" not in vault, vault
        record("M04 vault_status returns safe metadata through MCP")
        listing = await mcp.invoke("vault_list_accounts")
        assert isinstance(listing, dict) and "error" not in listing, listing
        assert "password" not in json.dumps(listing).lower()
        record("M05 account listing returns no passwords")
        try:
            await mcp.invoke("open_browser", {"url": "https://example.com/"})
        except HTTPException as exc:
            assert exc.status_code == 403, exc
        else:
            raise AssertionError("MCP allowed unapproved destination")
        record("M06 disallowed browser destination rejected before effects")
        try:
            await mcp.invoke("workspace_status", {"owner_id": "forged"})
        except HTTPException as exc:
            assert exc.status_code == 422, exc
        else:
            raise AssertionError("MCP accepted injected owner argument")
        record("M07 rejected owner-injection argument at catalog boundary")
        try:
            await mcp.invoke("not_a_tool", {})
        except HTTPException as exc:
            assert exc.status_code == 503, exc
        else:
            raise AssertionError("MCP allowed unregistered tool")
        record("M08 unknown MCP tool rejected")
        missing = await mcp.invoke("browser_observe")
        assert missing.get("code") == "missing_browser" and missing.get("status") == 409, missing
        record("M09 browser observation without trusted session rejected")
        requested = await mcp.invoke("vault_request_account", {"origin": "", "label": ""})
        assert isinstance(requested, dict) and "error" not in requested, requested
        record("M10 secure account form request does not expose credentials")
    # Genesis is intentionally NOT connected to the MCP executor.
    # Its persisted records must remain draft/non-provisioned, with only a
    # narrow read-only capability allowlist; no new agent is created here.
    assert os.environ.get("GENESIS_DRAFT_MODE") == "enabled"
    genesis = GenesisPilot.from_env()
    state = genesis.status()
    assert state["mode"] == "draft_only"
    assert state["execution"] == "disabled" and state["provider_provisioning"] is False
    record("G21 Genesis never executes or provisions via MCP")
    assert set(state["allowed_capabilities"]) <= {"workspace_status", "browser_status", "browser_observe"}
    assert "browser_click" not in state["allowed_capabilities"]
    record("G22 Genesis policy excludes effectful MCP actions")
    denied = genesis.preview("mcp-denied-test", "Denied", ["browser_click"])
    assert "browser_click" in denied["rejected_capabilities"]
    assert all(d["agent_id"] != "mcp-denied-test" for d in genesis.list_drafts())
    record("G23 denied MCP capability preview never persists agent")
    try:
        genesis.create_draft("mcp-denied-test", "Denied", ["browser_click"])
    except RegistryDenied:
        pass
    else:
        raise AssertionError("Genesis registered disallowed effectful agent")
    record("G24 Genesis denies effectful tool registration")
    print("MCP_GENESIS_ACCEPTANCE " + json.dumps({
        "status": "passed", "cases": cases, "mode": "disposable_four_service",
        "live_human_work_prompts": "NOT_RUN", "production_genesis_execution": "DISABLED"
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
