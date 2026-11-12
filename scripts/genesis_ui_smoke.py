"""Real AX/Genesis browser acceptance against disposable four-service CI stack.

This is not live Render/human acceptance. No mocks, external providers, or
production secrets. Saves screenshots and sanitized structured case results.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect

AX = "https://ax:10000"
OUTPUT = Path("/evidence")
PHASE = os.environ.get("SMOKE_PHASE", "initial")


async def main():
    cases = []
    def passed(name):
        cases.append({"case": name, "status": "PASS", "evidence": "CI four-service Chromium"})
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page.set_default_timeout(20000)
        errors = []
        console_errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        try:
            response = await page.goto(AX + "/genesis")
            assert response.status == 200
            assert page.url.rstrip("/") == AX
            await expect(page.locator("#loginForm")).to_be_visible()
            passed("G01 anonymous Genesis page redirects to login without exposing console")
            assert (await context.request.get(AX + "/api/genesis/agents")).status == 401
            passed("G02 anonymous draft listing denied")
            assert (await context.request.post(AX + "/api/genesis/preview", data={})).status == 401
            passed("G03 anonymous preview denied")
            await page.goto(AX)
            await page.locator("#password").fill(os.environ["AX_ADMIN_PASSWORD"])
            await page.locator("#loginForm button").click()
            await expect(page.locator("#open")).to_be_visible()
            passed("G04 authenticated AX login")
            await page.goto(AX + "/genesis")
            await expect(page.get_by_role("heading", name="Genesis")).to_be_visible()
            await expect(page.get_by_role("heading", name="Guardrail Manager")).to_be_visible()
            passed("G05 protected Genesis console renders")
            try:
                await expect(page.locator("#formStatus")).to_contain_text("Draft registration ready", timeout=10000)
            except AssertionError:
                raise AssertionError("Genesis init failed: page_errors=" + repr(errors) +
                    " console_errors=" + repr(console_errors) +
                    " status=" + repr(await page.locator("#formStatus").inner_text()) +
                    " script_count=" + str(await page.locator("script").count())) from None
            await expect(page.locator("#guardrails")).to_contain_text("draft_only")
            await expect(page.locator("#guardrails")).to_contain_text("disabled")
            passed("G06 policy readiness and disabled execution shown")
            checks = page.locator('input[name="cap"]')
            assert await checks.count() == 3
            assert await checks.nth(0).get_attribute("value") == "workspace_status"
            passed("G07 read-only capability selection")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path=str(OUTPUT / f"genesis-{PHASE}-mobile.png"))
            passed("G08 mobile width and screenshot")
            await page.set_viewport_size({"width": 1280, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await page.screenshot(path=str(OUTPUT / f"genesis-{PHASE}-desktop.png"))
            passed("G09 desktop width and screenshot")
            agent_id = "ci-genesis-" + PHASE
            await page.locator("#agentId").fill(agent_id)
            await page.locator("#agentName").fill("Disposable CI Agent " + PHASE)
            await page.locator("#preview").click()
            await expect(page.locator("#formStatus")).to_contain_text("Allowed: workspace_status")
            passed("G10 typed form preview with allowed capability")
            await page.locator("#register").click()
            await expect(page.locator("#agents")).to_contain_text(agent_id)
            await expect(page.locator("#agents")).to_contain_text("DRAFT")
            passed("G11 real AX to Core draft registration")
            await page.locator("#refresh").click()
            await expect(page.locator("#agents")).to_contain_text(agent_id)
            passed("G12 refresh preserves registered draft")
            await page.locator("#preview").click()
            await expect(page.locator("#formStatus")).to_contain_text("Genesis preview denied")
            passed("G13 persisted duplicate preview fail-closed")
            await page.locator("#register").click()
            await expect(page.locator("#formStatus")).to_contain_text("Genesis registration denied")
            passed("G14 duplicate registration rejected")
            await page.locator("#agents li").filter(has_text="("+agent_id+")").get_by_role("button", name="Manage").click()
            await expect(page.locator("#detailPanel")).to_be_visible()
            await expect(page.locator("#auditHistory")).to_contain_text("created")
            passed("G25 agent detail and audit visible")
            await page.locator("#editName").fill("Renamed disposable agent")
            await page.locator("#saveDraft").click()
            await expect(page.locator("#detailMeta")).to_contain_text("Revision 2")
            await expect(page.locator("#agents")).to_contain_text("Renamed disposable agent")
            passed("G26 edit draft with optimistic revision")
            clone_id = agent_id + "-copy"
            await page.locator("#cloneId").fill(clone_id)
            await page.locator("#cloneDraft").click()
            await expect(page.locator("#agents")).to_contain_text(clone_id)
            await expect(page.locator("#detailHeading")).to_contain_text(clone_id)
            passed("G27 clone as a new unprovisioned draft")
            await page.locator("#deleteDraft").click()
            await expect(page.locator("#detailStatus")).to_contain_text("Confirm draft deletion")
            passed("G28 delete requires explicit confirmation")
            await page.locator("#confirmDelete").check()
            await page.locator("#deleteDraft").click()
            await expect(page.locator("#detailPanel")).to_be_hidden()
            await expect(page.locator("#agents")).not_to_contain_text(clone_id)
            await expect(page.locator("#agents")).to_contain_text(agent_id)
            passed("G29 confirmed draft deletion preserves source")
            kb_id = "ci-kb-" + PHASE
            graph_id = "ci-graph-" + PHASE
            await page.locator("#kbId").fill(kb_id)
            await page.locator("#kbName").fill("Disposable CI Knowledge " + PHASE)
            await page.locator("#kbForm button").click()
            await expect(page.locator("#kbList")).to_contain_text(kb_id)
            await expect(page.locator("#kbStatus")).to_contain_text("No documents ingested")
            passed("G30 real AX/Core knowledge definition creation, nonqueryable")
            await page.locator("#refresh").click()
            await page.locator("#graphKB").select_option(kb_id)
            await expect(page.locator("#graphKB")).to_have_value(kb_id)
            await page.locator("#graphAgent").select_option(agent_id)
            await page.locator("#graphId").fill(graph_id)
            await page.locator("#graphName").fill("CI Knowledge Review " + PHASE)
            await page.locator("#generateGraph").click()
            await expect(page.locator("#graphPreview")).to_contain_text("kb_search")
            await expect(page.locator("#graphStatus")).to_contain_text("cannot execute")
            passed("G33 template generation creates a safe nonexecuting graph preview")
            await page.locator("#graphForm button[type=submit]").click()
            await expect(page.locator("#graphList")).to_contain_text(graph_id)
            await expect(page.locator("#graphStatus")).to_contain_text("Execution remains disabled")
            passed("G31 real typed graph creation and draft assignment")
            assigned = await context.request.get(
                AX + "/api/genesis/agents/" + agent_id + "/workflows")
            assert assigned.status == 200
            data = await assigned.json()
            assert data["runnable"] is False
            assert [item["graph_id"] for item in data["workflows"]] == [graph_id]
            passed("G32 dropdown contract excludes unassigned and disabled workflows")
            policy_id = "ci-policy-" + PHASE
            await page.locator("#policyId").fill(policy_id)
            await page.locator("#policyName").fill("CI Read-only Guardrail")
            await page.locator("#policyForm button[type=submit]").click()
            await expect(page.locator("#policyList")).to_contain_text(policy_id)
            await expect(page.locator("#policyStatus")).to_contain_text("NOT enforced")
            passed("G34 create policy draft through authenticated AX/Core")
            await page.locator("#policyTestTool").select_option("browser_click")
            await page.locator("#policySimulate").click()
            await expect(page.locator("#policyDecision")).to_contain_text("SIMULATED DENY")
            await expect(page.locator("#policyDecision")).to_contain_text("NOT an execution authorization")
            passed("G35 simulate denied action without executing MCP tool")
            await page.locator("#policyName").fill("Revised Read-only Guardrail")
            await page.locator("#policyForm button[type=submit]").click()
            await expect(page.locator("#policyList")).to_contain_text("draft v2")
            passed("G36 edit policy with revision, preserving draft status")
            await page.locator("#policyTestTool").select_option("workspace_status")
            await page.locator("#policyTestUrl").fill("https://alambda-forge.onrender.com.evil.net/")
            await page.locator("#policySimulate").click()
            await expect(page.locator("#policyDecision")).to_contain_text("SIMULATED DENY")
            await expect(page.locator("#policyDecision")).to_contain_text("draft_destination_denied")
            passed("G37 block spoofed destination by exact host")
            cookies = await context.cookies()
            csrf = next((c["value"] for c in cookies if c["name"] == "ax_csrf"), "")
            assert csrf, "Expected CSRF cookie"
            response = await context.request.post(
                AX + "/api/genesis/agents",
                data=json.dumps({"agent_id": "ci-forged", "name": "No", "capabilities": []}),
                headers={"Content-Type": "application/json"})
            assert response.status == 403
            passed("G15 missing CSRF rejected")
            response = await context.request.post(
                AX + "/api/genesis/agents",
                data=json.dumps({"agent_id": "ci-forged", "name": "No", "capabilities": []}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf, "Origin": "https://evil.test"})
            assert response.status == 403
            passed("G16 wrong-origin mutation rejected")
            payload = {"agent_id": "ci-forged", "name": "No",
                       "capabilities": ["browser_click"]}
            response = await context.request.post(
                AX + "/api/genesis/agents", data=json.dumps(payload),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf, "Origin": AX})
            assert response.status == 422
            passed("G17 disallowed tool rejected")
            payload = {"agent_id": "ci-owner-injected", "name": "No",
                       "capabilities": ["workspace_status"], "owner_id": "other-tenant"}
            response = await context.request.post(
                AX + "/api/genesis/agents", data=json.dumps(payload),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf, "Origin": AX})
            assert response.status == 422
            passed("G18 owner injection rejected")
            response = await context.request.get(AX + "/api/genesis/status")
            status = await response.json()
            assert response.status == 200 and status["execution"] == "disabled"
            assert status["provider_provisioning"] is False
            passed("G19 no execution or provisioning")
            if PHASE == "restart":
                await expect(page.locator("#agents")).to_contain_text("ci-genesis-initial")
                passed("G20 disk record survives restarted Core")
            assert not errors, errors
        finally:
            OUTPUT.mkdir(exist_ok=True, parents=True)
            (OUTPUT / f"genesis-ui-{PHASE}.json").write_text(
                json.dumps({"phase": PHASE, "mode": "disposable_real_four_service",
                            "cases": cases, "live_render_human_acceptance": "NOT_RUN"},
                           indent=2))
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
