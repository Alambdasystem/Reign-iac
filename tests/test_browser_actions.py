"""Browser fences and real offline DOM fixtures (no external website effects)."""
import asyncio
import json
import os
from pathlib import Path
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from playwright.async_api import async_playwright
from temporalio.exceptions import ApplicationError

from reign.browser_actions import ActionRequest, Observation
from reign.computer import BrowserSessions, app, browser_action, fill_account, frame
from reign.browser_proxy import BrowserProxy
from reign.tool_errors import BrowserActionError
from reign.workflows import BrowserAction, FillAccount


ORIGIN = "https://alambda-forge.onrender.com"
URL = ORIGIN + "/__reign_offline_fixture__"
SID = "action-test-session"
LOGIN = """<form method="post" action="/user/login" onsubmit="event.preventDefault();window.submits++">
<label>Username<input name="username" autocomplete="username"></label>
<label>Password<input name="password" type="password" autocomplete="current-password"></label>
<button type="button">Show password</button><button type="submit">Sign in</button></form>"""
SIGNUP = """<form method="post" action="/user/sign_up" onsubmit="event.preventDefault();window.submits++">
<label>Username<input name="username" autocomplete="username"></label>
<label>Email<input name="email" type="email" autocomplete="email"></label>
<label>Password<input name="password" type="password" autocomplete="new-password"></label>
<label>Confirm password<input name="confirm_password" type="password" autocomplete="new-password"></label>
<label><input type="checkbox" name="terms">I agree to the terms</label>
<button type="submit">Create account</button></form>"""


class ActionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = BrowserSessions()
        self.page = AsyncMock()
        self.page.url = URL
        self.page.title.return_value = "Public title"
        self.manager.sessions[SID] = {"page": self.page, "mode": "agent", "epoch": 0,
                                      "http_status": 200, "last_access": 0, "proxy": AsyncMock()}

    def observation(self):
        handle = AsyncMock()
        observation = Observation("observation-test-reference", 0, URL, ORIGIN, time.monotonic(), handle)
        handle.evaluate.return_value = {"ok": True}
        self.manager.sessions[SID]["observation"] = observation
        return observation

    async def test_preflight_is_authenticated_and_never_accepts_text(self):
        self.observation()
        token = "worker-test-token-" + "x" * 40
        with patch("reign.computer.manager", self.manager), patch.object(app.state, "token", token, create=True):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker.test") as client:
                path = f"/sessions/{SID}/validate-action"
                args = {"epoch": 0, "observation_id": "observation-test-reference", "action": "fill", "ref": "e1"}
                self.assertEqual((await client.post(path, json=args)).status_code, 401)
                with patch("reign.computer.validate_target", new_callable=AsyncMock) as validate:
                    response = await client.post(path, json=args, headers={"Authorization": "Bearer " + token})
                    self.assertEqual(response.json(), {"valid": True})
                    self.assertIn("observation", self.manager.sessions[SID])
                    self.page.mouse.click.assert_not_awaited()
                    validate.assert_awaited_once()
                    args["text"] = "fixture-only-must-not-be-echoed"
                    response = await client.post(path, json=args, headers={"Authorization": "Bearer " + token})
                    self.assertEqual(response.json()["code"], "invalid_action")
                    self.assertNotIn(args["text"], response.text)
                    self.assertEqual(validate.await_count, 1)

    async def test_takeover_invalidates_observation_and_stale_epoch_cannot_act(self):
        observation = self.observation()
        await self.manager.control(SID, "human", 0)
        self.assertNotIn("observation", self.manager.sessions[SID])
        observation.handle.dispose.assert_awaited_once()
        with self.assertRaises(HTTPException) as failure:
            await self.manager.act(SID, {"epoch": 0, "observation_id": observation.id, "action": "scroll", "dy": 50})
        self.assertEqual(failure.exception.status_code, 409)
        self.page.mouse.wheel.assert_not_awaited()

    async def test_expired_or_cross_session_observation_has_no_effect(self):
        for wrong_id, expired in (("another-session-reference", False), (None, True)):
            observation = self.observation()
            if expired:
                observation.created -= 61
            with self.assertRaises(BrowserActionError) as failure:
                await self.manager.act(SID, {"epoch": 0, "observation_id": wrong_id or observation.id, "action": "scroll", "dy": 50})
            self.assertEqual(failure.exception.code, "stale_observation")
            self.assertNotIn("observation", self.manager.sessions[SID])
        self.page.mouse.wheel.assert_not_awaited()

    async def test_uncertain_effect_is_consumed_and_history_error_is_closed(self):
        observation = self.observation()
        self.page.mouse.wheel.side_effect = RuntimeError("fixture-private-error-must-not-leak")
        data = {"id": SID, "epoch": 0, "observation_id": observation.id, "action": "scroll", "dy": 50}
        with patch("reign.computer.manager", self.manager):
            with self.assertRaises(ApplicationError) as failure:
                await browser_action(data)
            self.assertEqual(failure.exception.type, "BrowserActionUncertain")
            self.assertEqual(failure.exception.message, "action_uncertain")
            self.assertTrue(failure.exception.non_retryable)
            self.assertNotIn("private", str(failure.exception))
            with self.assertRaises(ApplicationError) as replay:
                await browser_action(data)
            self.assertEqual(replay.exception.message, "stale_observation")
        self.page.mouse.wheel.assert_awaited_once()

    async def test_redeem_is_not_attempted_after_control_changes(self):
        observation = self.observation()
        self.manager.sessions[SID]["mode"] = "human"
        with patch("reign.computer.manager", self.manager):
            with self.assertRaises(ApplicationError) as failure:
                await fill_account({"id": SID, "epoch": 0, "observation_id": observation.id, "grant_id": "fixture-grant"})
        self.assertEqual(failure.exception.type, "ControlChanged")
        observation.handle.evaluate.assert_not_awaited()

    async def test_all_new_effect_workflows_allow_one_attempt(self):
        for workflow, activity in ((BrowserAction, "browser_action"), (FillAccount, "fill_account")):
            with patch("reign.workflows.workflow.execute_activity", new_callable=AsyncMock, return_value={"ok": True}) as execute:
                self.assertEqual(await workflow().run({"id": SID}), {"ok": True})
                self.assertEqual(execute.await_args.args[0], activity)
                self.assertEqual(execute.await_args.kwargs["retry_policy"].maximum_attempts, 1)

    async def test_credential_context_blocks_another_otherwise_allowed_origin(self):
        context = object()
        self.manager.sessions[SID].update(context=context, credential_origin=ORIGIN)
        route = AsyncMock()
        route.request.url = "https://other.example.test/collect"
        with patch.dict(os.environ, {"ALLOWED_HOSTS": "alambda-forge.onrender.com,other.example.test"}):
            await self.manager.guard(route, context)
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()

    async def test_public_metadata_strips_auth_query_fragment_and_known_password(self):
        self.manager.sessions[SID]["redactions"] = {"fixture-password"}
        self.page.url = URL + "/fixture-password?code=auth-query#auth-fragment"
        self.page.title.return_value = "Echo fixture-password"
        result = await self.manager.info(SID)
        self.assertEqual(result["title"], "Echo [redacted]")
        self.assertNotIn("fixture-password", json.dumps(result))
        self.assertNotIn("auth-query", json.dumps(result))
        self.assertNotIn("auth-fragment", json.dumps(result))

    async def test_redaction_covers_normalized_password_echoes(self):
        private = "  synthetic\t test-password\ufeff "
        session = {"redactions": {private}}
        for echo in (private, "synthetic test-password", "synthetic%20test-password", "synthetic+test-password"):
            self.assertEqual(self.manager.redact(session, "Echo: " + echo), "Echo: [redacted]")


class OfflineBrowserActionTests(unittest.IsolatedAsyncioTestCase):
    """Runs real Chromium entirely against route-fulfilled in-memory fixtures."""

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        except Exception:
            await self.playwright.stop()
            if os.getenv("REIGN_REQUIRE_BROWSER_TESTS") == "1":
                self.fail("Required Chromium browser fixture is unavailable")
            self.skipTest("Chromium is not installed; CI must run with REIGN_REQUIRE_BROWSER_TESTS=1")
        self.proxy = BrowserProxy()
        await self.proxy.start()
        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800},
                                                     proxy=self.proxy.playwright_settings)
        self.html = ""
        await self.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=self.html))
        self.page = await self.context.new_page()
        self.manager = BrowserSessions()
        self.manager.sessions[SID] = {"page": self.page, "context": self.context, "proxy": self.proxy, "mode": "agent", "epoch": 0,
                                      "http_status": 200, "last_access": 0}
        await self.load('<label>Note<input id="note" name="note" value="ordinary-input-value"></label><button id="increment" onclick="window.count++">Increment</button><div style="height:2400px">Scrollable page</div>')

    async def asyncTearDown(self):
        if hasattr(self, "manager"):
            await self.manager.close(SID)
        if hasattr(self, "browser"):
            await self.browser.close()
        if hasattr(self, "playwright"):
            await self.playwright.stop()

    async def load(self, body):
        self.html = '<!doctype html><html><head><title>Offline action fixture</title></head><body>' + body + '<script>window.count=0;window.submits=0;window.keys=[];document.addEventListener("keydown",e=>window.keys.push(e.key));</script></body></html>'
        await self.page.goto(URL, wait_until="domcontentloaded")

    async def observed(self):
        return await self.manager.observe(SID, self.manager.sessions[SID]["epoch"])

    async def action(self, name, action, **kwargs):
        observation = await self.observed()
        ref = next(element["ref"] for element in observation["elements"] if element["name"] == name)
        return await self.manager.act(SID, {"epoch": observation["epoch"], "observation_id": observation["observation_id"],
                                            "action": action, "ref": ref, **kwargs})

    async def test_real_observation_excludes_values_and_is_bounded(self):
        await self.load(LOGIN + '<input type="hidden" value="hidden-value"><input type="file"><input aria-label="Search" value="ordinary-value">' +
                        ''.join(f'<button>Button {i}</button>' for i in range(90)) + '<p>' + 'visible ' * 2000 + '</p>')
        await self.page.locator('[name="password"]').fill("fixture-input-password")
        result = await self.observed()
        serialized = json.dumps(result)
        self.assertLessEqual(len(result["elements"]), 80)
        self.assertLessEqual(len(result["text"]), 6000)
        self.assertTrue(result["truncated"])
        for private in ("hidden-value", "ordinary-value", "fixture-input-password"):
            self.assertNotIn(private, serialized)
        fields = {el["name"]: el for el in result["elements"]}
        self.assertTrue(fields["Username"]["secret"])
        self.assertTrue(fields["Password"]["secret"])
        self.assertFalse(fields["Search"]["secret"])
        self.assertTrue(fields["Search"]["fillable"])
        self.assertFalse(any(el["type"] in {"hidden", "file"} for el in result["elements"]))

    async def test_real_click_fill_press_scroll_and_no_replay(self):
        observation = await self.observed()
        ref = next(el["ref"] for el in observation["elements"] if el["name"] == "Increment")
        args = {"epoch": 0, "observation_id": observation["observation_id"], "action": "click", "ref": ref}
        self.assertEqual(await self.manager.validate_action(SID, args), {"valid": True})
        self.assertTrue((await self.manager.act(SID, args))["acted"])
        self.assertEqual(await self.page.evaluate("window.count"), 1)
        with self.assertRaises(BrowserActionError):
            await self.manager.act(SID, args)
        self.assertEqual(await self.page.evaluate("window.count"), 1)
        await self.action("Note", "fill", text="ordinary new text")
        self.assertEqual(await self.page.locator("#note").input_value(), "ordinary new text")
        await self.action("Note", "press", key="Enter")
        self.assertIn("Enter", await self.page.evaluate("window.keys"))
        observation = await self.observed()
        await self.manager.act(SID, {"epoch": 0, "observation_id": observation["observation_id"], "action": "scroll", "dy": 500})
        await self.page.wait_for_function("window.scrollY > 0")

    async def test_real_dom_change_and_navigation_invalidate_refs(self):
        observation = await self.observed()
        ref = next(el["ref"] for el in observation["elements"] if el["name"] == "Increment")
        await self.page.locator("#increment").evaluate("el => el.replaceWith(el.cloneNode(true))")
        with self.assertRaises(BrowserActionError) as failure:
            await self.manager.act(SID, {"epoch": 0, "observation_id": observation["observation_id"], "action": "click", "ref": ref})
        self.assertEqual(failure.exception.code, "stale_observation")
        self.assertEqual(await self.page.evaluate("window.count"), 0)
        observation = await self.observed()
        await self.page.reload()
        with self.assertRaises(BrowserActionError):
            await self.manager.act(SID, {"epoch": 0, "observation_id": observation["observation_id"], "action": "scroll", "dy": 100})

    async def test_pending_dom_mutation_stays_stale_across_read_only_checks(self):
        await self.observed()
        observation = self.manager.sessions[SID]["observation"]
        checks = await observation.handle.evaluate("s => {document.body.append(document.createElement('p'));return [s.check(null),s.check(null)];}")
        self.assertEqual([result.get("error") for result in checks], ["stale_observation", "stale_observation"])

    async def test_native_wait_cannot_click_a_target_changed_after_dispatch(self):
        await self.load('<button id="increment" onclick="window.count++">Increment</button><div id="cover" style="position:fixed;inset:0;background:white"></div>')
        observation = await self.observed()
        ref = next(el["ref"] for el in observation["elements"] if el["name"] == "Increment")
        task = asyncio.create_task(self.manager.act(SID, {"epoch": 0, "observation_id": observation["observation_id"], "action": "click", "ref": ref}))
        await asyncio.sleep(0.15)
        await self.page.evaluate("() => {document.querySelector('#increment').textContent='Changed target';document.querySelector('#cover').remove();}")
        with self.assertRaises(BrowserActionError) as failure:
            await task
        self.assertIn(failure.exception.code, {"stale_observation", "action_uncertain"})
        self.assertEqual(await self.page.evaluate("window.count"), 0)

    async def test_signup_navigation_link_is_allowed_but_not_final_submission(self):
        await self.load('<a href="/user/sign_up">Register</a>')
        result = await self.action("Register", "click")
        self.assertTrue(result["acted"])
        self.assertEqual(result["url"], ORIGIN + "/user/sign_up")

    async def test_real_secret_reveal_and_human_verification_are_blocked(self):
        await self.load(LOGIN + '<button>Verify CAPTCHA</button><label><input type="checkbox">Accept terms</label>')
        for name, action, extra, expected in (("Password", "fill", {"text": "do-not-fill"}, "secret_field"),
                                             ("Username", "fill", {"text": "do-not-fill"}, "secret_field"),
                                             ("Show password", "click", {}, "secret_field"),
                                             ("Verify CAPTCHA", "click", {}, "human_verification_required"),
                                             ("Accept terms", "click", {}, "human_verification_required")):
            with self.assertRaises(BrowserActionError) as failure:
                await self.action(name, action, **extra)
            self.assertEqual(failure.exception.code, expected)
        self.assertEqual(await self.page.locator('[name="password"]').input_value(), "")

    async def test_private_fill_and_redaction_do_not_submit_or_expose_password(self):
        await self.load(LOGIN)
        observation = await self.observed()
        redeem = AsyncMock(return_value={"username": "fixture-user", "password": "fixture-only-password"})
        with patch.dict("sys.modules", {"reign.credential_transport": types.SimpleNamespace(redeem_credentials=redeem)}):
            result = await self.manager.fill_account(SID, "fixture-grant", observation["observation_id"], 0)
        self.assertTrue(result["filled"])
        self.assertNotIn("fixture-only-password", json.dumps(result))
        self.assertEqual(await self.page.evaluate("window.submits"), 0)
        self.assertEqual(await self.page.locator('[name="password"]').input_value(), "fixture-only-password")
        redeem.assert_awaited_once_with("fixture-grant", SID, observation["observation_id"], 0, ORIGIN)
        await self.page.evaluate("() => {const value=document.querySelector('[name=password]').value;document.title=value;const p=document.createElement('p');p.textContent=value;document.body.append(p);history.replaceState(null,'','?code='+encodeURIComponent(value)+'#private');document.querySelector('[name=password]').type='text';}")
        result = await self.observed()
        self.assertNotIn("fixture-only-password", json.dumps(result))
        self.assertNotIn("?code=", result["url"])
        self.assertTrue(next(el for el in result["elements"] if el["name"] == "Password")["secret"])
        with self.assertRaises(BrowserActionError) as failure:
            await self.action("Password", "fill", text="ordinary overwrite")
        self.assertEqual(failure.exception.code, "secret_field")

    async def test_password_echo_is_redacted_before_observation_truncation(self):
        await self.load(LOGIN)
        observation = await self.observed()
        private = "fixture-" + "abcd" * 100
        redeem = AsyncMock(return_value={"username": "fixture-user", "password": private})
        with patch.dict("sys.modules", {"reign.credential_transport": types.SimpleNamespace(redeem_credentials=redeem)}):
            await self.manager.fill_account(SID, "fixture-grant", observation["observation_id"], 0)
        await self.page.evaluate("value => {const button=document.createElement('button');button.textContent=value;document.body.prepend(button);const p=document.createElement('p');p.textContent='padding '.repeat(700)+value;document.body.append(p);}", private)
        result = await self.observed()
        self.assertNotIn("abcd" * 15, json.dumps(result))
        self.assertIn("[redacted]", json.dumps(result))

    async def test_signup_pair_fills_privately_but_finalization_and_consent_remain_human(self):
        await self.load(SIGNUP)
        await self.action("Email", "fill", text="fixture@example.test")
        observation = await self.observed()
        redeem = AsyncMock(return_value={"username": "fixture-user", "password": "fixture-generated-password"})
        with patch.dict("sys.modules", {"reign.credential_transport": types.SimpleNamespace(redeem_credentials=redeem)}):
            self.assertTrue((await self.manager.fill_account(SID, "fixture-grant", observation["observation_id"], 0))["filled"])
        for name in ("password", "confirm_password"):
            self.assertEqual(await self.page.locator(f'[name="{name}"]').input_value(), "fixture-generated-password")
        self.assertEqual(await self.page.locator('[name="email"]').input_value(), "fixture@example.test")
        for name in ("Create account", "I agree to the terms"):
            with self.assertRaises(BrowserActionError) as failure:
                await self.action(name, "click")
            self.assertEqual(failure.exception.code, "human_verification_required")
        with self.assertRaises(BrowserActionError) as failure:
            await self.action("Email", "press", key="Enter")
        self.assertEqual(failure.exception.code, "human_verification_required")
        self.assertEqual(await self.page.evaluate("window.submits"), 0)

    async def test_ambiguous_or_cross_origin_form_never_redeems(self):
        redeem = AsyncMock(return_value={"username": "fixture-user", "password": "fixture-password"})
        for body in (LOGIN + LOGIN, LOGIN.replace('action="/user/login"', 'action="https://outside.example.test/login"')):
            await self.load(body)
            observation = await self.observed()
            with patch.dict("sys.modules", {"reign.credential_transport": types.SimpleNamespace(redeem_credentials=redeem)}):
                with self.assertRaises(BrowserActionError):
                    await self.manager.fill_account(SID, "fixture-grant", observation["observation_id"], 0)
            redeem.assert_not_awaited()

    async def test_page_change_during_grant_redemption_does_not_fill(self):
        await self.load(LOGIN)
        observation = await self.observed()
        async def redeem(*args):
            await self.page.locator("form").evaluate("el => el.action='/changed'")
            return {"username": "fixture-user", "password": "fixture-password"}
        with patch.dict("sys.modules", {"reign.credential_transport": types.SimpleNamespace(redeem_credentials=redeem)}):
            with self.assertRaises(BrowserActionError) as failure:
                await self.manager.fill_account(SID, "fixture-grant", observation["observation_id"], 0)
        self.assertEqual(failure.exception.code, "stale_observation")
        self.assertEqual(await self.page.locator('[name="username"]').input_value(), "")
        self.assertEqual(await self.page.locator('[name="password"]').input_value(), "")

    async def test_frames_mask_fields_after_type_change_without_staling_observation(self):
        await self.load(LOGIN + '<button id="increment" onclick="window.count++">Increment</button>')
        await self.page.locator('[name="password"]').fill("fixture-one-password")
        first_unmasked = await self.page.screenshot()
        with patch("reign.computer.manager", self.manager):
            first_masked = (await frame(SID)).body
        await self.page.locator('[name="password"]').evaluate("el => {el.type='text';el.value='fixture-two-password';}")
        second_unmasked = await self.page.screenshot()
        with patch("reign.computer.manager", self.manager):
            second_masked = (await frame(SID)).body
            observation = await self.observed()
            await frame(SID)
        self.assertNotEqual(first_unmasked, second_unmasked)
        self.assertEqual(first_masked, second_masked)
        ref = next(el["ref"] for el in observation["elements"] if el["name"] == "Increment")
        await self.manager.act(SID, {"epoch": 0, "observation_id": observation["observation_id"], "action": "click", "ref": ref})
        self.assertEqual(await self.page.evaluate("window.count"), 1)


if __name__ == "__main__":
    unittest.main()
