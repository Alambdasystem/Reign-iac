"""Real browser/model acceptance against the disposable AX container.

Uses a separate named tester session and the bundled fictional portrait, while
preserving the owner's existing browser context. No mock neural runtime,
production credential, or external inference API is used.
"""
import json
import os
from urllib.parse import urlsplit

from playwright.async_api import expect


async def portrait_ui_smoke(owner_context, origin, evidence):
    browser = owner_context.browser
    assert browser is not None, "Portrait acceptance requires an isolated browser context"
    context = await browser.new_context(ignore_https_errors=True)
    page = await context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append({
        "method": request.method, "url": request.url}))
    page.set_default_timeout(180000)
    result = {"status": "failed", "mode": "real_browser_and_pretrained_model",
              "production_browser": False, "frames": [],
              "authentication": {"status": "failed", "credential_fixture": "synthetic"}}
    try:
        username = os.environ["SMOKE_AVATAR_USERNAME"]
        password = os.environ["SMOKE_AVATAR_PASSWORD"]
        response = await page.goto(origin + "/")
        assert response.status == 200
        await page.locator("#username").fill(username)
        await page.locator("#password").fill(password)
        await page.locator("#loginForm button").click()
        await expect(page).to_have_url(origin + "/avatar-lab")
        await expect(page.locator("#accountName")).to_contain_text("Avatar test user")
        await expect(page.locator("#logoutButton")).to_be_visible()
        await expect(page.locator("#workspaceLink")).to_be_hidden()
        profile_response = await context.request.get(origin + "/api/me")
        assert profile_response.status == 200
        profile = await profile_response.json()
        assert profile["username"] == username and profile["role"] == "avatar"
        assert profile["landing_path"] == "/avatar-lab"
        assert profile["capabilities"]["avatar_lab"] is True
        for capability in ("managed_browser", "ai_chat", "voice", "external_computer"):
            assert profile["capabilities"][capability] is False
        assert password not in json.dumps(profile) and "password_hash" not in profile
        cookies = await context.cookies()
        session_cookie = next(cookie for cookie in cookies if cookie["name"] == "ax_session")
        assert session_cookie["secure"] and session_cookie["httpOnly"]
        assert session_cookie["sameSite"] == "Strict"
        csrf = next(cookie["value"] for cookie in cookies if cookie["name"] == "ax_csrf")
        headers = {"Origin": origin, "X-CSRF-Token": csrf}

        # The real frontend must not begin the owner's recovery flow when a
        # named tester signs in or returns to the main REIGN address.
        await page.goto(origin + "/")
        await expect(page).to_have_url(origin + "/avatar-lab")
        await expect(page.locator("#accountName")).to_contain_text("Avatar test user")
        assert not [request for request in requests
                    if urlsplit(request["url"]).path.startswith("/api/")
                    and urlsplit(request["url"]).path != "/api/me"], \
            "Tester frontend requested an owner-only API"

        denied = []
        for path in ("/api/accounts", "/api/sessions/active", "/api/agent",
                     "/api/voice", "/api/security/status", "/api/genesis/agents", "/api/nodes"):
            rejected = await context.request.get(origin + path)
            assert rejected.status == 403, (path, rejected.status)
            denied.append("GET " + path)
        # A valid session-bound CSRF value cannot elevate the tester role.
        path = "/api/openstl/approve"
        rejected = await context.request.post(origin + path, headers=headers,
                                              data={"user_code": "TEST-CODE"})
        assert rejected.status == 403, (path, rejected.status)
        denied.append("POST " + path)
        for invalid_headers in ({"Origin": origin, "X-CSRF-Token": "wrong-csrf"},
                                {"Origin": "https://foreign.example.invalid", "X-CSRF-Token": csrf}):
            assert (await context.request.post(origin + "/logout", headers=invalid_headers)).status == 403
        assert (await context.request.get(origin + "/api/me")).status == 200

        response = await page.reload()
        assert response.status == 200
        assert "wasm-unsafe-eval" in response.headers["content-security-policy"]
        # Record only the inference phase for the no-photo-upload assertion.
        # The separately checked login and logout necessarily make POSTs.
        requests.clear()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await expect(page.locator("#startButton")).to_be_enabled()
        await page.locator("#startButton").click()
        await expect(page.locator("#outputCanvas")).to_be_visible(timeout=180000)

        async def frame(previous=0):
            await page.wait_for_function(
                "n => Number(document.querySelector('#frameStat').dataset.frames || 0) > n",
                arg=previous, timeout=180000)
            await expect(page.locator("#renderingLabel")).to_be_hidden()
            value = await page.locator("#outputCanvas").evaluate("""canvas => {
                const data = canvas.getContext('2d').getImageData(0, 0, 256, 256).data;
                let sum = 0, squared = 0;
                const samples = [];
                for (let i = 0; i < data.length; i += 4) {
                    const v = (data[i] + data[i + 1] + data[i + 2]) / 3;
                    sum += v; squared += v * v;
                    if (i % 64 === 0) samples.push(data[i], data[i + 1], data[i + 2]);
                }
                const mean = sum / 65536;
                return {mean, variance: squared / 65536 - mean * mean, samples};
            }""")
            assert 10 < value["mean"] < 245, "Blank or clipped neural frame"
            assert value["variance"] > 100, "Neural output has no image detail"
            number = int(await page.locator("#frameStat").get_attribute("data-frames"))
            provider = await page.locator("#frameStat").get_attribute("data-provider")
            assert provider in ("wasm", "webgpu")
            result["frames"].append({"number": number, "provider": provider,
                                     "timing": await page.locator("#frameStat").inner_text(),
                                     "pixel_mean": value["mean"], "pixel_variance": value["variance"]})
            return number, value["samples"]

        number, neutral = await frame()
        await page.screenshot(path=str(evidence / "avatar-neural-neutral.png"), full_page=True)
        for label in ("Mouth open", "Small turn", "Soft smile"):
            await page.get_by_role("button", name=label, exact=True).click()
            number, pixels = await frame(number)
            difference = sum(abs(a - b) for a, b in zip(neutral, pixels)) / len(neutral)
            assert difference > .2, f"{label} did not change actual neural pixels"
            result["frames"][-1].update(control=label, mean_difference_from_neutral=difference)
        await expect(page.locator("#motionControls")).to_be_enabled()
        await expect(page.locator("#motionButton")).to_be_enabled()
        await page.screenshot(path=str(evidence / "avatar-neural-smile.png"), full_page=True)
        for width, height in ((390, 844), (430, 932)):
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Portrait lab horizontal overflow"
        await page.screenshot(path=str(evidence / "avatar-neural-mobile.png"), full_page=True)
        assert not errors, errors
        assert all(r["method"] == "GET" for r in requests), "Portrait lab transmitted a request body"
        assert all(urlsplit(r["url"]).netloc == urlsplit(origin).netloc for r in requests), "Portrait lab used a third-party service"
        runtime_note = await page.locator("#runtimeNote").inner_text()
        # Keep the old cookies only in memory. Replaying them after the visible
        # logout must fail, even though their original expiry is still in future.
        old_cookies = await context.cookies()
        await page.locator("#logoutButton").click()
        await expect(page.locator("#loginPanel")).to_be_visible()
        assert not any(cookie["name"] in ("ax_session", "ax_csrf")
                       for cookie in await context.cookies())
        assert (await context.request.get(origin + "/api/me")).status == 401
        replay = await browser.new_context(ignore_https_errors=True)
        try:
            await replay.add_cookies(old_cookies)
            assert (await replay.request.get(origin + "/api/me")).status == 401
            locked = await replay.request.get(origin + "/avatar-lab", max_redirects=0)
            assert locked.status == 303
        finally:
            await replay.close()
        owner = await owner_context.request.get(origin + "/api/me")
        assert owner.status == 200 and (await owner.json())["role"] == "owner", \
            "Tester logout invalidated the independent owner session"
        assert not errors, errors
        await page.screenshot(path=str(evidence / "avatar-auth-logout.png"), full_page=True)
        result.update(status="passed", requests_same_origin=True, inference_requests="none",
                      runtime_note=runtime_note,
                      checks=["named_tester_login", "tester_has_no_owner_api_access",
                              "authenticated_document", "real_model_download_and_inference",
                              "nonblank_neural_pixels", "mouth_pose_smile_change_pixels",
                              "no_photo_upload", "desktop_and_mobile_layout",
                              "visible_logout", "logged_out_cookie_replay_rejected",
                              "independent_owner_session_preserved"],
                      authentication={"status": "passed", "credential_fixture": "synthetic",
                                      "role": "avatar", "denied_owner_requests": denied,
                                      "csrf_and_origin_enforced": True,
                                      "cookie_replay_rejected": True,
                                      "owner_session_preserved": True})
        print("PASS: named Avatar Lab tester rendered real neural frames, was denied owner APIs, and logged out with cookie revocation")
    finally:
        (evidence / "avatar-neural-acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
        await context.close()
