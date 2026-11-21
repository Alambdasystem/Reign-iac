"""Real browser/model acceptance against the disposable AX container.

Uses the already-authenticated CI browser context and bundled fictional portrait.
No mock neural runtime, production credential, or external inference API is used.
"""
import json
from urllib.parse import urlsplit

from playwright.async_api import expect


async def portrait_ui_smoke(context, origin, evidence):
    page = await context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append({
        "method": request.method, "url": request.url}))
    page.set_default_timeout(180000)
    result = {"status": "failed", "mode": "real_browser_and_pretrained_model",
              "production_browser": False, "frames": []}
    try:
        response = await page.goto(origin + "/avatar-lab")
        assert response.status == 200
        assert "wasm-unsafe-eval" in response.headers["content-security-policy"]
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
        result.update(status="passed", requests_same_origin=True, inference_requests="none",
                      runtime_note=await page.locator("#runtimeNote").inner_text(),
                      checks=["authenticated_document", "real_model_download_and_inference",
                              "nonblank_neural_pixels", "mouth_pose_smile_change_pixels",
                              "no_photo_upload", "desktop_and_mobile_layout"])
        print("PASS: Avatar Lab loaded pretrained weights and rendered neutral, mouth, turn and smile frames")
    finally:
        (evidence / "avatar-neural-acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
        await page.close()
