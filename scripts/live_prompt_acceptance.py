"""Live REIGN UI prompt-to-tool acceptance runner.

Run only from an approved operator environment with a real authenticated
Playwright browser. NEVER run this during public CI, commit auth state or dump
cookies, account fields, screenshot bytes or raw model outputs to logs.

Examples:
  AX_ADMIN_PASSWORD=... python scripts/live_prompt_acceptance.py --output /secure/reign-results.json
  python scripts/live_prompt_acceptance.py --storage-state /secure/ax-auth.json --output /secure/reign-results.json

Uses the REAL AX form and same-origin authenticated Core proxy. This script
cannot prove an external login or Temporal history by itself. Those require
an independent worker/Temporal log correlation and human approval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

AX_ORIGIN = "https://reign-ax-pilot-d934.onrender.com"
ALLOWED_ORIGIN = "https://alambda-forge.onrender.com"
# Safe to run unattended against the allowlisted production site; ordinary
# field changes, new accounts, clicking, and takeover remain manual.
SAFE_CASES = (
    ("L01", "Show REIGN workspace status and which components are ready.",
     ["workspace_status"]),
    ("L02", "Tell me whether I have a managed browser session open.",
     ["browser_status"]),
    ("L03", "Open the allowed Git High site in REIGN's managed browser.",
     ["open_browser"]),
    ("L04", "Navigate the managed browser to https://alambda-forge.onrender.com/.",
     ["navigate_browser"]),
    ("L05", "Observe the current Git High page and describe only visible text and buttons.",
     ["browser_observe"]),
    ("L10", "Is the encrypted saved-account vault ready and durable?",
     ["vault_status"]),
    ("L11", "List my saved account labels and origins only, not passwords.",
     ["vault_list_accounts"]),
)
ALL_CASES = (
    "L01","L02","L03","L04","L05","L06","L07","L08","L09",
    "L10","L11","L12","L13","L14","L15","L16","L17","L18",
    "L19","L20","L21",
)


def validate_origin(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != urlsplit(AX_ORIGIN).hostname
        or parsed.port is not None or parsed.username or parsed.password
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Only the authorized AX origin is supported")
    return AX_ORIGIN


async def enter_workspace(page, password: str | None) -> None:
    await page.goto(AX_ORIGIN, wait_until="domcontentloaded", timeout=30000)
    if await page.locator("#loginPanel").is_visible():
        if not password:
            raise RuntimeError("Authenticated AX storage state or AX_ADMIN_PASSWORD required")
        await page.locator("#password").fill(password)
        await page.locator("#loginForm button[type=submit]").click()
        await page.locator("#loginPanel").wait_for(state="hidden", timeout=30000)
    # Keep the browser in Work mode for every case.
    await page.locator('[data-tab="Work"]').click()
    await page.locator("#message").wait_for(state="visible")
    await page.locator("#sendCommand").wait_for(state="visible")


async def run_case(page, case_id: str, prompt: str, expected: list[str]) -> dict:
    record = dict(
        id=case_id, timestamp_utc=datetime.now(timezone.utc).isoformat(),
        exact_prompt=prompt, expected_tools=expected, selected_tools=[],
        thread_id=None, run_id=None, status="NOT_RUN", result_status=None,
        temporal_workflow_id=None, image_digest=None,
        screenshot_or_log_ref=None, reason="Not yet tested",
    )
    # UI interaction, rather than direct POST, is essential to validate
    # the actual AX text composer -> Core LangGraph workflow.
    await page.locator("#sendCommand").wait_for(state="visible")
    # AX asynchronously clears its pending-chat lock after Core completes.
    # Wait for that UI state instead of racing subsequent prompts.
    try:
        await page.locator("#sendCommand").wait_for(state="attached",timeout=15000)
        await page.wait_for_function(
            "() => !document.getElementById('sendCommand').disabled",
            timeout=15000)
    except Exception:
        record.update(status="BLOCKED",reason="AX composer unavailable")
        return record
    async with page.expect_response(
            lambda response: urlsplit(response.url).path == "/api/chat"
            and response.request.method == "POST", timeout=25000) as pending:
        await page.locator("#message").fill(prompt)
        await page.locator("#sendCommand").click()
    response = await pending.value
    if response.status != 200:
        record.update(status="FAIL",reason=f"AX chat POST returned HTTP {response.status}")
        return record
    result = await response.json()
    tid = result.get("thread_id")
    if not isinstance(tid,str) or len(tid) != 36:
        record.update(status="FAIL",reason="Chat did not return a valid thread ID")
        return record
    record.update(thread_id=tid,run_id=result.get("run_id"))
    for _ in range(90):
        # Same-origin, authenticated HTTP read through the REAL browser. Do not
        # persist raw model answers or tool message bodies in test evidence.
        state = await page.evaluate("""async tid => {
            const r=await fetch('/api/chat/'+encodeURIComponent(tid),
                {credentials:'same-origin',cache:'no-store'});
            if(!r.ok)return {http_status:r.status};
            const v=await r.json();
            return {status:v.status,run_id:v.run_id,
              events:(v.events||[]).map(e=>({kind:e.kind,tool:e.tool,ok:e.ok})),
              session_id:v.session_id};
        }""", tid)
        if state.get("http_status"):
            record.update(status="FAIL",reason="Authenticated chat polling failed")
            return record
        if state.get("status") in ("completed","failed","cancelled","paused"):
            break
        await asyncio.sleep(1)
    else:
        record.update(status="FAIL",reason="Agent timeout without terminal receipt")
        return record
    record["result_status"] = state.get("status")
    events = state.get("events",[])
    selected = [e["tool"] for e in events if e.get("kind") == "tool_started"
                and isinstance(e.get("tool"),str)]
    completed = [e for e in events if e.get("kind") == "tool_completed"]
    record["selected_tools"] = selected
    # Tool selection and actual tool success both required; an HTTP 200
    # response alone never proves a tool invocation succeeded.
    tool_ok = all(any(e.get("tool")==tool and e.get("ok") is True
                      for e in completed) for tool in expected)
    if not all(x in selected for x in expected) or not tool_ok:
        record.update(status="FAIL",reason="Expected MCP tool missing or failed")
        return record
    if state.get("status") != "completed":
        record.update(status="FAIL",reason="Run not completed")
        return record
    # Status/tool evidence alone cannot prove website effects or redaction.
    # For read-only prompts, record a provisional selection result only.
    record.update(status="BLOCKED",reason="Tool execution observed; independent effect/Temporal verification required")
    return record


async def main(args) -> None:
    validate_origin(args.origin)
    dest=Path(args.output).expanduser()
    if dest.exists():
        raise RuntimeError("Choose a new output file; existing evidence cannot be overwritten")
    if args.storage_state:
        state=Path(args.storage_state).expanduser()
        if not state.is_file():
            raise RuntimeError("Authenticated storage state file missing")
        if state.resolve()==dest.resolve():
            raise RuntimeError("Output file cannot replace the authentication file")
    if args.max_cases < 1 or args.max_cases > len(SAFE_CASES):
        raise ValueError("max-cases must be between 1 and the safe-case count")
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=not args.headful)
        try:
            context=await browser.new_context(
                storage_state=str(Path(args.storage_state).expanduser())
                if args.storage_state else None)
            page=await context.new_page()
            await enter_workspace(page,os.getenv("AX_ADMIN_PASSWORD"))
            results=[]
            for cid,prompt,tools in SAFE_CASES[:args.max_cases]:
                try:
                    receipt=await run_case(page,cid,prompt,tools)
                except Exception as error:
                    # Do not persist raw exception messages: they may contain
                    # private URLs, headers, snippets, account values, or IDs.
                    receipt={"id":cid,"status":"FAIL",
                             "reason":"Exception in live UI acceptance runner",
                             "exception_type":type(error).__name__}
                results.append(receipt)
                if receipt["status"]=="FAIL" and args.stop_on_failure:
                    break
            performed={r["id"] for r in results}
            results += [{"id":cid,"status":"NOT_RUN",
                         "reason":"Requires supervised interaction or unavailable validated preconditions"}
                        for cid in ALL_CASES if cid not in performed]
        finally:
            await browser.close()
    # Prevent accidental publication of auth-adjacent operational evidence.
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Create with restrictive mode AT CREATION, not after opening a file
    # that could temporarily have been readable by another local user.
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump({"source":"authenticated_live_ax_ui","production":True,
                   "warning":"Tool receipts are provisional until independently correlated with Temporal and screenshots",
                   "results":results},file,indent=2)
    print(f"Live AX attempts: {len(performed)}. Evidence written privately to {dest}.")
    print("No automatic PASS claims: inspect tool effect, Temporal workflow and image digest.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--origin",default=AX_ORIGIN)
    parser.add_argument("--storage-state",
                        help="Private Playwright authentication JSON; never commit")
    parser.add_argument("--output",required=True)
    parser.add_argument("--max-cases",type=int,default=5)
    parser.add_argument("--headful",action="store_true")
    parser.add_argument("--stop-on-failure",action="store_true")
    asyncio.run(main(parser.parse_args()))
