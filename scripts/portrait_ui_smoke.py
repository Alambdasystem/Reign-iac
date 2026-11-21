"""Real browser/model acceptance against the disposable AX container.

Uses a separate named tester session and the bundled fictional portrait, while
preserving the owner's existing browser context. No mock neural runtime,
production credential, or external inference API is used.
"""
import json
import os
from base64 import b64decode
from urllib.parse import urlsplit

from playwright.async_api import expect


# Observe the real worker without replacing inference, its messages, or timing.
# Retain only two model frames so the gate can distinguish interpolated display
# pixels from actual neural endpoints and detect an accumulating render queue.
WORKER_OBSERVER = """(() => {
    const NativeWorker = window.Worker;
    const probe = window.__portraitAcceptance = {
        active: 0, maximumActive: 0, started: 0, terminated: 0,
        pendingRenders: 0, maximumPendingRenders: 0, completedRenders: 0,
        sourceFrames: 0, previousPixels: null, latestPixels: null,
        lastControls: null, motionTracking: false, motionRequests: 0, motionSteps: {},
    };
    window.Worker = class extends NativeWorker {
        constructor(url, options) {
            super(url, options);
            this.observed = options?.name === 'reign-neural-portrait';
            this.pendingPortraitRenders = new Set();
            this.portraitTerminated = false;
            if (!this.observed) return;
            probe.started++; probe.active++;
            probe.maximumActive = Math.max(probe.maximumActive, probe.active);
            this.addEventListener('message', ({data}) => {
                if (!this.pendingPortraitRenders.delete(data.id)) return;
                probe.pendingRenders--;
                if (data.result?.pixels) {
                    probe.completedRenders++; probe.sourceFrames++;
                    probe.previousPixels = probe.latestPixels;
                    probe.latestPixels = new Uint8ClampedArray(data.result.pixels);
                }
            });
        }
        postMessage(message, ...args) {
            if (this.observed && message.operation === 'encode') {
                probe.sourceFrames = 0;
                probe.previousPixels = probe.latestPixels = null;
            }
            if (this.observed && message.operation === 'render') {
                this.pendingPortraitRenders.add(message.id); probe.pendingRenders++;
                probe.maximumPendingRenders = Math.max(probe.maximumPendingRenders, probe.pendingRenders);
                if (probe.motionTracking && probe.lastControls) {
                    probe.motionRequests++;
                    for (const [name, value] of Object.entries(message.controls)) {
                        probe.motionSteps[name] = Math.max(probe.motionSteps[name] || 0,
                            Math.abs(value - probe.lastControls[name]));
                    }
                }
                probe.lastControls = {...message.controls};
            }
            return super.postMessage(message, ...args);
        }
        terminate() {
            if (this.observed && !this.portraitTerminated) {
                this.portraitTerminated = true; probe.active--; probe.terminated++;
                probe.pendingRenders -= this.pendingPortraitRenders.size;
                this.pendingPortraitRenders.clear();
                probe.previousPixels = probe.latestPixels = null;
            }
            return super.terminate();
        }
    };
})();"""


async def portrait_ui_smoke(owner_context, origin, evidence):
    browser = owner_context.browser
    assert browser is not None, "Portrait acceptance requires an isolated browser context"
    context = await browser.new_context(ignore_https_errors=True, reduced_motion="no-preference")
    await context.add_init_script(WORKER_OBSERVER)
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
        await expect(page.locator("#defaultAvatarCanvas")).to_have_attribute("data-ready", "true")
        await expect(page.locator("#portraitStage")).to_have_attribute("data-preview-mode", "prepared")
        assert await page.evaluate("__portraitAcceptance.started") == 0
        assert not [request for request in requests
                    if urlsplit(request["url"]).path.startswith(("/avatar-assets/models/", "/avatar-assets/ort/"))], \
            "Default REIGN preview downloaded or initialized the heavy neural runtime"
        await page.screenshot(path=str(evidence / "avatar-ready-without-setup.png"), full_page=True)

        # A real local image selection persists across navigation under this
        # verified account. It is not published or sent to a backend. The
        # isolated browser's IndexedDB is then checked with a second legitimate
        # synthetic session to prove it does not select another account's photo.
        await page.locator("#advancedDetails").evaluate("element => { element.open = true; }")
        await expect(page.locator("#startButton")).to_be_enabled()
        source_png = await page.locator("#sourceCanvas").evaluate(
            "canvas => canvas.toDataURL('image/png').split(',')[1]")
        await page.locator("#portraitFile").set_input_files({
            "name": "synthetic-local-portrait.png", "mimeType": "image/png",
            "buffer": b64decode(source_png, validate=True)})
        await expect(page.locator("#portraitPreferenceStatus")).to_have_attribute("data-state", "saved")
        await page.locator("#cropDetails").evaluate("element => { element.open = true; }")
        await page.locator("#cropZoom").evaluate("""element => {
            element.value = '1.24'; element.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        await page.locator("#applyCrop").click()
        await expect(page.locator("#portraitPreferenceStatus")).to_have_attribute("data-state", "saved")
        sample_photo = """canvas => {
            const pixels = canvas.getContext('2d').getImageData(0, 0, 256, 256).data;
            const samples = [];
            for (let y = 8; y < 256; y += 16) for (let x = 8; x < 256; x += 16) {
                const offset = (y * 256 + x) * 4;
                samples.push(pixels[offset], pixels[offset + 1], pixels[offset + 2]);
            }
            return samples;
        }"""
        before_reload = await page.locator("#customPreviewCanvas").evaluate(sample_photo)
        await page.reload()
        await expect(page.locator("#portraitStage")).to_have_attribute("data-preview-mode", "saved-custom")
        await expect(page.locator("#portraitPreferenceStatus")).to_have_attribute("data-state", "saved")
        await expect(page.locator("#cropZoom")).to_have_value("1.24")
        after_reload = await page.locator("#customPreviewCanvas").evaluate(sample_photo)
        # The bounded saved image is re-encoded, so compare appearance rather
        # than requiring lossy normalization to preserve every source byte.
        restored_error = sum(abs(a - b) for a, b in zip(before_reload, after_reload)) / len(before_reload)
        assert restored_error < 3.5, "Saved custom portrait/crop did not restore its appearance"
        assert await page.evaluate("__portraitAcceptance.started") == 0
        tester_cookies = await context.cookies()
        await context.clear_cookies()
        await context.add_cookies(await owner_context.cookies())
        await page.reload()
        await expect(page.locator("#accountName")).to_contain_text("Owner")
        await expect(page.locator("#portraitStage")).to_have_attribute("data-source", "default")
        await expect(page.locator("#defaultAvatarCanvas")).to_have_attribute("data-ready", "true")
        await context.clear_cookies()
        await context.add_cookies(tester_cookies)
        await page.reload()
        await expect(page.locator("#portraitStage")).to_have_attribute("data-preview-mode", "saved-custom")
        await page.locator("#advancedDetails").evaluate("element => { element.open = true; }")
        await page.locator("#sampleButton").click()
        await expect(page.locator("#portraitPreferenceStatus")).to_have_attribute("data-state", "default")
        await page.reload()
        await expect(page.locator("#portraitStage")).to_have_attribute("data-preview-mode", "prepared")
        await expect(page.locator("#defaultAvatarCanvas")).to_have_attribute("data-ready", "true")
        assert await page.evaluate("__portraitAcceptance.started") == 0
        result["saved_portrait"] = {
            "default_ready_without_setup": True, "no_default_neural_download": True,
            "custom_photo_and_crop_survive_reload": True, "account_selection_isolated": True,
            "restored_pixel_mean_error": restored_error,
            "reset_removes_only_current_local_preference": True,
            "scope": "account_scoped_browser_storage_not_cross_device"}
        assert all(request["method"] == "GET" or urlsplit(request["url"]).path == "/login"
                   for request in requests), "The saved local portrait was transmitted to a server"
        # Record only the inference phase for the no-photo-upload assertion.
        # The separately checked login and logout necessarily make POSTs.
        requests.clear()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.locator("#advancedDetails").evaluate("element => { element.open = true; }")
        await expect(page.locator("#startButton")).to_be_enabled()
        await page.locator("#startButton").click()
        await expect(page.locator("#outputCanvas")).to_be_visible(timeout=180000)

        async def frame(previous=0):
            await page.wait_for_function(
                """n => {
                    const data = document.querySelector('#frameStat').dataset;
                    return Number(data.frames || 0) > n && data.settled === 'true'
                        && data.transitioning === 'false';
                }""",
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
                const neural = window.__portraitAcceptance.latestPixels;
                const exactNeuralEndpoint = neural?.length === data.length && data.every((v, i) => v === neural[i]);
                return {mean, variance: squared / 65536 - mean * mean, samples, exactNeuralEndpoint};
            }""")
            assert 10 < value["mean"] < 245, "Blank or clipped neural frame"
            assert value["variance"] > 100, "Neural output has no image detail"
            assert value["exactNeuralEndpoint"], "Settled output differs from the real neural worker frame"
            number = int(await page.locator("#frameStat").get_attribute("data-frames"))
            provider = await page.locator("#frameStat").get_attribute("data-provider")
            assert provider in ("wasm", "webgpu")
            result["frames"].append({"number": number, "provider": provider,
                                     "source_revision": int(await page.locator("#frameStat").get_attribute("data-source-revision")),
                                     "timing": await page.locator("#frameStat").inner_text(),
                                     "pixel_mean": value["mean"], "pixel_variance": value["variance"],
                                     "exact_neural_endpoint": True})
            return number, value["samples"]

        number, neutral = await frame()
        await page.screenshot(path=str(evidence / "avatar-neural-neutral.png"), full_page=True)
        for label in ("Mouth open", "Small turn", "Soft smile"):
            if label == "Small turn":
                # Sample actual displayed pixels during an existing model test.
                # No artificial frames, timing delays, or extra inference calls.
                await page.evaluate("""() => {
                    const probe = window.__portraitAcceptance;
                    const stat = document.querySelector('#frameStat');
                    const canvas = document.querySelector('#outputCanvas');
                    const sample = pixels => {
                        const values = [];
                        for (let y = 8; y < 256; y += 16) for (let x = 8; x < 256; x += 16) {
                            const i = (y * 256 + x) * 4;
                            values.push(pixels[i], pixels[i + 1], pixels[i + 2]);
                        }
                        return values;
                    };
                    const difference = (a, b) => a.reduce((sum, v, i) => sum + Math.abs(v - b[i]), 0) / a.length;
                    const observation = probe.presentation = {
                        running: true, samples: [], previous: null,
                        neuralStart: probe.completedRenders,
                        displayStart: Number(stat.dataset.displayFrames || 0),
                    };
                    const tick = time => {
                        if (!observation.running) return;
                        const data = stat.dataset;
                        if (data.transitioning === 'true' && probe.previousPixels && probe.latestPixels
                            && Number(data.frames) === probe.sourceFrames && observation.samples.length < 360) {
                            const pixels = sample(canvas.getContext('2d').getImageData(0, 0, 256, 256).data);
                            const before = difference(pixels, sample(probe.previousPixels));
                            const after = difference(pixels, sample(probe.latestPixels));
                            const change = observation.previous ? difference(pixels, observation.previous) : 0;
                            if (before > .05 && after > .05 && change > .02) {
                                observation.samples.push({time, neural: probe.completedRenders,
                                    accepted: Number(data.frames), fromEarlier: before, toLater: after, change});
                                const ratio = before / (before + after);
                                if (!observation.preview && ratio > .35 && ratio < .65) {
                                    const image = document.createElement('canvas');
                                    image.width = image.height = 256;
                                    const context = image.getContext('2d');
                                    const encode = pixels => {
                                        context.putImageData(new ImageData(pixels, 256, 256), 0, 0);
                                        return image.toDataURL('image/png').split(',')[1];
                                    };
                                    observation.preview = {earlier: encode(probe.previousPixels),
                                        intermediate: canvas.toDataURL('image/png').split(',')[1],
                                        later: encode(probe.latestPixels)};
                                }
                            }
                            observation.previous = pixels;
                        }
                        observation.raf = requestAnimationFrame(tick);
                    };
                    observation.raf = requestAnimationFrame(tick);
                }""")
            await page.get_by_role("button", name=label, exact=True).click()
            number, pixels = await frame(number)
            difference = sum(abs(a - b) for a, b in zip(neutral, pixels)) / len(neutral)
            assert difference > .2, f"{label} did not change actual neural pixels"
            result["frames"][-1].update(control=label, mean_difference_from_neutral=difference)
            if label == "Small turn":
                presentation = await page.evaluate("""() => {
                    const probe = window.__portraitAcceptance, value = probe.presentation;
                    value.running = false; cancelAnimationFrame(value.raf);
                    const groups = new Map();
                    for (const sample of value.samples) {
                        const key = `${sample.neural}:${sample.accepted}`;
                        groups.set(key, (groups.get(key) || 0) + 1);
                    }
                    return {
                        intermediate_samples: value.samples.length,
                        maximum_between_neural_frames: Math.max(0, ...groups.values()),
                        neural_frames_added: probe.completedRenders - value.neuralStart,
                        display_frames_added: Number(document.querySelector('#frameStat').dataset.displayFrames) - value.displayStart,
                        samples: value.samples.slice(0, 12),
                        preview: value.preview,
                    };
                }""")
                assert presentation["maximum_between_neural_frames"] >= 2, \
                    "Canvas did not show distinct in-between pixels while neural frame count stayed fixed"
                assert presentation["display_frames_added"] > presentation["neural_frames_added"], \
                    "Display frames were not separated from neural model frames"
                presentation["mode"] = "display_interpolation_between_real_neural_endpoints"
                presentation["neural_fps_claimed"] = False
                preview = presentation.pop("preview", None)
                assert preview, "No representative real in-between image was captured"
                presentation["images"] = []
                for position, png in preview.items():
                    filename = f"avatar-interpolation-{position}.png"
                    (evidence / filename).write_bytes(b64decode(png, validate=True))
                    presentation["images"].append(filename)
                result["presentation"] = presentation
        await expect(page.locator("#motionControls")).to_be_enabled()
        await expect(page.locator("#motionButton")).to_be_enabled()
        await page.screenshot(path=str(evidence / "avatar-neural-smile.png"), full_page=True)
        motion_start = await page.evaluate("""() => {
            window.__portraitAcceptance.motionTracking = true;
            const data = document.querySelector('#frameStat').dataset;
            return {neural: Number(data.frames), interpolated: Number(data.interpolatedFrames)};
        }""")
        await page.locator("#motionButton").click()
        await expect(page.locator("#motionButton")).to_have_attribute("aria-pressed", "true")
        await page.wait_for_function(
            "n => Number(document.querySelector('#frameStat').dataset.frames) >= n + 2",
            arg=motion_start["neural"], timeout=180000)
        await page.locator("#motionButton").click()
        await expect(page.locator("#motionButton")).to_have_attribute("aria-pressed", "false")
        await page.wait_for_function(
            "() => document.querySelector('#frameStat').dataset.settled === 'true'",
            timeout=180000)
        motion_result = await page.evaluate("""() => {
            const probe = window.__portraitAcceptance, data = document.querySelector('#frameStat').dataset;
            probe.motionTracking = false;
            return {requests: probe.motionRequests, maximum_control_steps: probe.motionSteps,
                    neural: Number(data.frames), interpolated: Number(data.interpolatedFrames)};
        }""")
        assert motion_result["requests"] >= 2
        assert motion_result["interpolated"] > motion_start["interpolated"]
        for name in ("yaw", "pitch", "roll"):
            assert motion_result["maximum_control_steps"][name] <= 2, "Play motion skipped a large pose interval"
        for name in ("mouth", "blink", "smile"):
            assert motion_result["maximum_control_steps"][name] <= .15, "Play motion jumped to a distant expression"
        result["play_motion"] = {"status": "passed", "neural_frames_presented": motion_result["neural"] - motion_start["neural"],
                                 "interpolated_presentations": motion_result["interpolated"] - motion_start["interpolated"],
                                 "maximum_control_steps": motion_result["maximum_control_steps"]}
        for width, height in ((390, 844), (430, 932)):
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Portrait lab horizontal overflow"
        await page.screenshot(path=str(evidence / "avatar-neural-mobile.png"), full_page=True)
        # A changed crop is a new source; its first neural endpoint must never
        # morph from pixels retained from the previous crop.
        previous_revision = int(await page.locator("#frameStat").get_attribute("data-source-revision"))
        await page.locator("#cropDetails").evaluate("element => { element.open = true; }")
        await page.locator("#cropZoom").evaluate("""element => {
            element.value = '1.05'; element.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        await page.locator("#applyCrop").click()
        number, _ = await frame()
        assert number == 1, "A new crop did not reset its neural frame count"
        assert int(await page.locator("#frameStat").get_attribute("data-source-revision")) > previous_revision
        assert int(await page.locator("#frameStat").get_attribute("data-interpolated-frames")) == 0, \
            "A new crop was blended with the old source"
        result["frames"][-1]["control"] = "New crop"
        result["crop_resets_interpolation"] = True
        worker = await page.evaluate("""() => {
            const p = window.__portraitAcceptance;
            return {started: p.started, active: p.active, maximum_active: p.maximumActive,
                pending_renders: p.pendingRenders, maximum_pending_renders: p.maximumPendingRenders,
                completed_renders: p.completedRenders};
        }""")
        assert worker["started"] == worker["active"] == worker["maximum_active"] == 1, worker
        assert worker["maximum_pending_renders"] == 1 and worker["pending_renders"] == 0, worker
        result["worker_bounds"] = worker
        assert not errors, errors
        assert all(r["method"] == "GET" for r in requests), "Portrait lab transmitted a request body"
        assert all(urlsplit(r["url"]).netloc == urlsplit(origin).netloc for r in requests), "Portrait lab used a third-party service"
        runtime_note = await page.locator("#runtimeNote").inner_text()
        # Keep the old cookies only in memory. Replaying them after the visible
        # logout must fail, even though their original expiry is still in future.
        old_cookies = await context.cookies()
        logout_release = {}

        async def observe_logout(route):
            # Observe immediate resource release before forwarding the real
            # logout request; neither the endpoint nor its result is mocked.
            try:
                logout_release.update(await page.evaluate("""() => ({
                    active_workers: window.__portraitAcceptance.active,
                    terminated_workers: window.__portraitAcceptance.terminated,
                    pending_renders: window.__portraitAcceptance.pendingRenders,
                    source_width: document.querySelector('#sourceCanvas').width,
                    output_width: document.querySelector('#outputCanvas').width,
                    transitioning: document.querySelector('#frameStat').dataset.transitioning,
                })"""))
            finally:
                await route.continue_()

        await page.route(origin + "/logout", observe_logout)
        await page.locator("#logoutButton").click()
        await expect(page.locator("#loginPanel")).to_be_visible()
        assert logout_release == {"active_workers": 0, "terminated_workers": 1,
                                  "pending_renders": 0, "source_width": 0,
                                  "output_width": 0, "transitioning": "false"}, logout_release
        result["logout_released_presentation"] = logout_release
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
                              "default_reign_ready_without_manual_model_start",
                              "local_custom_portrait_crop_restore_and_account_isolation",
                              "reset_to_saved_reign_default",
                              "authenticated_document", "real_model_download_and_inference",
                              "nonblank_neural_pixels", "mouth_pose_smile_change_pixels",
                              "distinct_intermediate_display_pixels", "exact_neural_endpoints",
                              "play_motion_uses_small_bounded_pose_steps",
                              "crop_resets_interpolation", "bounded_real_worker_queue",
                              "logout_releases_presentation",
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
