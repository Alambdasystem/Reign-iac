"""Real four-container acceptance. No mocked Core, Temporal or Computer transport.

Uses public Git High only. AX uses a disposable TLS certificate inside the Docker
test network. This is automated acceptance, not James's human takeover signoff.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect
from temporalio.client import Client
from temporalio.api.enums.v1 import EventType

TARGET = "https://alambda-forge.onrender.com/"
AX = "https://ax:10000"
EVIDENCE = Path("/evidence")


async def main():
    temporal = await Client.connect("temporal:7233")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page.set_default_timeout(120000)
        request = context.request
        assert (await request.get(AX + "/api/readyz")).status == 401
        ready = await request.get(AX + "/api/readyz",
                                  headers={"Authorization": "Bearer " + os.environ["CORE_TOKEN"]})
        assert ready.status == 200, "AX dependency readiness failed"
        assert (await ready.json())["components"] == {
            "core": "ready", "temporal": "ready", "computer": "ready"}
        await page.goto(AX)
        await page.locator("#password").fill(os.environ["AX_ADMIN_PASSWORD"])
        await page.locator("#loginForm button").click()
        await expect(page.locator("#console")).to_be_visible()
        cookies = await context.cookies()
        session_cookie = next(c for c in cookies if c["name"] == "ax_session")
        assert session_cookie["secure"] and session_cookie["httpOnly"]
        csrf = next(c["value"] for c in cookies if c["name"] == "ax_csrf")
        headers = {"Origin": AX, "X-CSRF-Token": csrf}

        async def api(path, method="GET", data=None, status=200):
            response = await request.fetch(AX + path, method=method, data=data,
                                           headers=headers, timeout=120000)
            assert response.status == status, (method, path, response.status, status)
            return response

        async def completed_history(sid):
            handle = temporal.get_workflow_handle("reign-open-" + sid)
            result = await handle.result()
            assert result["id"] == sid and result["url"].startswith(TARGET)
            assert 200 <= result["http_status"] < 400 and result["title"]
            history = await handle.fetch_history()
            completed = sum(e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
                            for e in history.events)
            assert completed == 1, "Open must have exactly one completed activity"
            description = await handle.describe()
            return {"workflow_id": handle.id, "run_id": description.run_id,
                    "completed_activities": completed}

        if os.environ.get("SMOKE_PHASE") == "restart":
            evidence = json.loads((EVIDENCE / "smoke.json").read_text())
            sid = evidence["restart_session"]
            await api("/api/sessions/" + sid, status=404)
            current = await completed_history(sid)
            assert current == evidence["restart_workflow"], "History changed after restart"
            # A fresh browser session works after the worker restart.
            opened = await api("/api/sessions", "POST", {"url": TARGET})
            new_sid = (await opened.json())["id"]
            assert new_sid != sid
            await api("/api/sessions/" + new_sid, "DELETE")
            evidence["restart_expiry"] = "passed"
            evidence["temporal_persistence"] = "passed"
            evidence["status"] = "passed"
            (EVIDENCE / "smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps(evidence))
        else:
            # Unauthorized actions and missing CSRF are rejected before dispatch.
            anonymous = await p.request.new_context(ignore_https_errors=True)
            assert (await anonymous.post(AX + "/api/sessions", data={})).status == 401
            await anonymous.dispose()
            assert (await request.post(AX + "/api/sessions", data={})).status == 403
            await page.locator("#open").click()
            await expect(page.locator("#session")).not_to_have_text("None")
            sid = await page.locator("#session").inner_text()
            await expect(page.locator("#controller")).to_have_text("agent")
            await page.wait_for_function("document.querySelector('#screen').naturalWidth > 0")
            prefix = "/api/sessions/" + sid
            history = await completed_history(sid)
            first = await api(prefix + "/frame")
            assert len(await first.body()) > 100
            assert first.headers["x-session-id"] == sid
            await page.locator("#take").click()
            await expect(page.locator("#controller")).to_have_text("human")
            await expect(page.locator("#epoch")).to_have_text("1")
            await api(prefix + "/navigate", "POST", {"url": TARGET, "epoch": 0}, 409)
            await api(prefix + "/input", "POST", {"kind": "key", "key": "Tab", "epoch": 1})
            await page.locator("#scroll").click()
            second = await api(prefix + "/frame")
            assert int(second.headers["x-frame-sequence"]) > int(first.headers["x-frame-sequence"])
            assert int(second.headers["x-frame-timestamp"]) > int(first.headers["x-frame-timestamp"])
            assert second.headers["cache-control"] == "no-store"
            await page.screenshot(path=str(EVIDENCE / "ax-takeover.png"))
            await page.locator("#return").click()
            await expect(page.locator("#controller")).to_have_text("agent")
            await expect(page.locator("#epoch")).to_have_text("2")
            await api(prefix + "/input", "POST", {"kind": "key", "key": "Tab", "epoch": 1}, 409)
            await api(prefix + "/navigate", "POST", {"url": TARGET, "epoch": 2})
            await page.locator("#close").click()
            await expect(page.locator("#session")).to_have_text("None")
            await api(prefix + "/frame", status=404)
            # Leave a session open to check honest expiry after Computer restarts.
            opened = await api("/api/sessions", "POST", {"url": TARGET})
            restart_sid = (await opened.json())["id"]
            evidence = {
                "status": "awaiting_restart_check", "source_sha": os.environ.get("SOURCE_SHA"),
                "workflow": history, "restart_session": restart_sid,
                "restart_workflow": await completed_history(restart_sid),
                "authenticated_ui": "passed", "live_frames": "passed",
                "takeover_handback": "passed", "stale_commands": "passed",
                "cleanup": "passed", "human_acceptance": "not_run",
                "target": TARGET,
            }
            (EVIDENCE / "smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print("PASS: real AX login, Core dispatch, Temporal history, browser frames and control")
        await browser.close()


asyncio.run(main())
