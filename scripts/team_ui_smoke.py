"""Shared-room acceptance in disposable CI containers, using real Chromium.

The two-context call uses Chromium's synthetic microphone/camera devices. It
does not establish physical-device or cross-network/TURN reliability. No model
provider credentials or employee passwords are written to evidence.
"""
import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from playwright.async_api import async_playwright, expect
from team_callspace_smoke import callspace_click, callspace_control, callspace_media_roundtrip, callspace_mode, close_callspace_panel, settled_callspace_view

AX = "https://ax:10000"
OUTPUT = Path("/evidence")
PHASE = os.environ.get("SMOKE_PHASE", "initial")
PERSISTENCE = OUTPUT / "team-persistence.json"
PROBE = """(() => {
  const probe = window.__teamProbe = {deviceRequests: [], tracks: [], peers: []};
  const media = navigator.mediaDevices;
  if (media?.getUserMedia) {
    const original = media.getUserMedia.bind(media);
    media.getUserMedia = async constraints => {
      probe.deviceRequests.push(constraints);
      const stream = await original(constraints); probe.tracks.push(...stream.getTracks()); return stream;
    };
  }
  const Peer = window.RTCPeerConnection;
  if (Peer) window.RTCPeerConnection = class extends Peer {
    constructor(...args) { super(...args); probe.peers.push(this); }
  };
})();"""


async def api(page, path, method="GET", body=None):
    return await page.evaluate("""async ({path,method,body}) => {
      const token = document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('ax_csrf='))?.slice(8)||'';
      const response = await fetch(path,{method,credentials:'same-origin',headers:method==='GET'?{}:
        {'Content-Type':'application/json','X-CSRF-Token':token},body:body===null?undefined:JSON.stringify(body)});
      return {status:response.status,data:await response.json()};
    }""", {"path": path, "method": method, "body": body})


async def login(page, username, password):
    await page.goto(AX + "/team")
    await expect(page.locator("#loginForm")).to_be_visible()
    await page.locator("#username").fill(username)
    await page.locator("#password").fill(password)
    await callspace_click(page, "#loginForm button")
    await expect(page).to_have_url(AX + "/team")
    await expect(page.locator("#workspace")).to_have_attribute("aria-busy", "false")
    await expect(page.locator("#accountName")).not_to_have_text("Checking sign-in…")


async def provider_picker_check(page):
    opener = page.locator("[data-provider-picker]")
    await opener.focus()
    await opener.press("Enter")
    dialog = page.get_by_role("dialog", name="Account connections")
    await expect(dialog).to_be_visible()
    await expect(dialog.get_by_role("button", name="Close")).to_be_focused()
    for provider in ("Gmail", "Outlook"):
        card = dialog.get_by_role("listitem").filter(has_text=provider)
        await expect(card.get_by_role("heading", name=provider)).to_be_visible()
        await expect(card.get_by_text("Unavailable", exact=True)).to_be_visible()
    bounds = await dialog.bounding_box()
    assert bounds and 0 <= bounds["x"] < bounds["x"] + bounds["width"] <= page.viewport_size["width"]
    assert 0 <= bounds["y"] < bounds["y"] + bounds["height"] <= page.viewport_size["height"]
    await page.keyboard.press("Escape")
    await expect(dialog).to_be_hidden()
    await expect(opener).to_be_focused()


async def shared_browser_check(page, room_id):
    """Run after the baseline restart phase has released its private slot."""
    path = f"/api/team/rooms/{room_id}/browser"
    await callspace_control(page, "#browserPanel")
    if not await page.locator("#browserPanel").evaluate("panel => panel.open"):
        await callspace_click(page, "#browserPanel > summary")
    await page.locator("#browserUrl").fill("https://alambda-forge.onrender.com/")
    await callspace_click(page, "#openBrowser")
    await expect(page.locator("#browserFrame")).to_be_visible(timeout=80000)
    await page.wait_for_function("() => document.getElementById('browserFrame').naturalWidth > 0")
    status = await api(page, path)
    assert status["status"] == 200 and status["data"]["status"] == "active"
    session_id = status["data"]["session"]["id"]
    frame = await page.context.request.get(AX + path + "/frame")
    assert frame.status == 200 and frame.headers["content-type"].startswith("image/jpeg")
    assert frame.headers["cache-control"] == "no-store"
    # The owner route still must not discover or attach to this room resource.
    private = await api(page, "/api/sessions/active")
    assert private["status"] == 200 and private["data"]["session"] is None
    assert (await api(page, "/api/sessions/" + session_id))["status"] == 404
    await expect(page.locator("#takeBrowser")).to_be_enabled()
    await callspace_click(page, "#takeBrowser")
    await expect(page.locator("#returnBrowser")).to_be_visible()
    status = (await api(page, path))["data"]
    assert status["session"]["mode"] == "human"
    stale = await api(page, path + "/input", "POST", {"connection_id": status["control"]["connection_id"],
        "epoch": status["session"]["epoch"] - 1, "kind": "key", "key": "Tab"})
    assert stale["status"] == 409
    await expect(page.locator("#browserTab")).to_be_enabled()
    await callspace_click(page, "#browserTab")
    await expect(page.locator("#returnBrowser")).to_be_enabled()
    await callspace_click(page, "#returnBrowser")
    await expect(page.locator("#takeBrowser")).to_be_enabled()
    assert (await api(page, path))["data"]["session"]["mode"] == "agent"
    await page.screenshot(path=str(OUTPUT / "team-restart-shared-browser.png"))
    await callspace_click(page, "#closeBrowser")
    await expect(page.locator("#browserFrame")).to_be_hidden()
    assert (await api(page, path))["data"]["status"] == "closed"


async def camera_check(browser, room_id, room_title, passed, errors, receipt):
    """Real synthetic-camera capture and protected transport, with no AI provider.

    The capability advertisement alone is overridden for the description case.
    Upload, viewer admission, message rejection and explicit retry all use the
    actual disposable AX/Core services. This cannot prove model vision or audio.
    """
    context = await browser.new_context(ignore_https_errors=True, permissions=["camera"],
                                        viewport={"width": 390, "height": 900})
    await context.add_init_script(PROBE)
    page = await context.new_page()
    page.set_default_timeout(30000)
    page.on("pageerror", lambda error: errors.append(str(error)))
    media_requests = []
    page.on("request", lambda request: media_requests.append(request) if request.method == "POST" and
            urlsplit(request.url).path.endswith(("/attachments", "/messages", "/signals", "/transcriptions")) else None)
    logout_started, release_logout = asyncio.Event(), asyncio.Event()
    receipt.update(status="RUNNING", device_source="Chromium synthetic camera",
                   capability_override="Client advertisement only: assistant, vision, speech",
                   provider_inference="NOT EXERCISED; CI has no provider", physical_device="NOT EXERCISED")

    def matching(response, suffix):
        return response.request.method == "POST" and urlsplit(response.url).path == f"/api/team/rooms/{room_id}/{suffix}"

    async def ready():
        await expect(page.locator("#cameraDialog")).to_be_visible()
        await expect(page.locator("#cameraPreview")).to_have_attribute("data-state", "ready")
        await page.wait_for_function("""() => {
          const v = document.getElementById('cameraPreview');
          return v.srcObject?.getVideoTracks().some(t=>t.readyState==='live') &&
            !v.srcObject.getAudioTracks().length && v.videoWidth>0 && v.videoHeight>0 &&
            !v.paused && v.muted && v.playsInline;
        }""")

    async def stopped():
        await page.wait_for_function("""() => !document.getElementById('cameraPreview').srcObject &&
          __teamProbe.tracks.length>0 && __teamProbe.tracks.every(t=>t.readyState==='ended')""")
        await expect(page.locator("#describeCamera")).to_be_disabled()

    async def device_summary():
        value = await page.evaluate("""() => ({requests:__teamProbe.deviceRequests,
          kinds:__teamProbe.tracks.map(t=>t.kind), peers:__teamProbe.peers.length})""")
        assert value["peers"] == 0 and all(kind == "video" for kind in value["kinds"])
        assert all(item.get("audio") is False and item.get("video") for item in value["requests"])
        return len(value["requests"])

    try:
        # A distinct login avoids revoking the baseline owner's session. This
        # helper runs after the original call/revocation/logout acceptance.
        await login(page, "owner", os.environ["AX_ADMIN_PASSWORD"])
        await callspace_click(page, f'[data-room-id="{room_id}"]')
        await expect(page.locator("#roomTitle")).to_have_text(room_title)
        profile = (await api(page, "/api/team"))["data"]
        assert profile["capabilities"]["assistant"] is False and profile["capabilities"]["vision"] is False
        assert await device_summary() == 0
        await callspace_click(page, "#openCamera")
        await ready()
        await expect(page.locator("#describeCamera")).to_be_disabled()
        await expect(page.locator("#cameraAvailability")).to_contain_text("vision connection is unavailable")
        await page.wait_for_timeout(250)
        assert not media_requests, "Opening a private camera preview sent media or a message"
        await page.screenshot(path=str(OUTPUT / "team-camera-private-preview.png"))
        await callspace_click(page, "#stopCamera")
        await stopped()
        await expect(page.locator("#cameraDialog")).to_be_visible()
        await callspace_click(page, "#restartCamera")
        await ready()
        await callspace_click(page, "#closeCamera")
        await stopped()
        await expect(page.locator("#cameraDialog")).to_be_hidden()
        passed("T17 camera opens only by gesture with video-only live preview; no upload, microphone or peer; Stop and Close release tracks")

        other = next((room for room in profile["rooms"] if room["id"] != room_id), None)
        if other is None:
            # The only additional durable record is a synthetic lifecycle room
            # in the disposable CI disk. The saved acceptance room stays intact.
            title = "CI camera lifecycle " + uuid4().hex[:8]
            await callspace_click(page, "#newRoomButton")
            await page.locator("#roomTitleInput").fill(title)
            await callspace_click(page, "#roomCreateForm button[type=submit]")
            await expect(page.locator("#roomTitle")).to_have_text(title)
            other = {"id": await page.locator('[data-room-id][aria-current="page"]').get_attribute("data-room-id"), "title": title}
            await callspace_click(page, f'[data-room-id="{room_id}"]')
            await expect(page.locator("#roomTitle")).to_have_text(room_title)
        await callspace_click(page, "#openCamera")
        await ready()
        # A modal makes background buttons inert to pointer input. Invoke the
        # actual room-change handler to prove teardown even for an asynchronous
        # navigation while the preview is open; no application state is mocked.
        await page.locator(f'[data-room-id="{other["id"]}"]').evaluate("button=>button.click()")
        await expect(page.locator("#roomTitle")).to_have_text(other["title"])
        await stopped()
        await expect(page.locator("#cameraDialog")).to_be_hidden()
        await callspace_click(page, f'[data-room-id="{room_id}"]')
        await expect(page.locator("#roomTitle")).to_have_text(room_title)
        await expect(page.locator("#roomView")).to_be_visible()
        await expect(page.locator("#messageInput")).to_be_enabled()
        prior_device_requests = await device_summary()
        passed("T18 room transition closes the preview and stops owned camera tracks without joining a call")

        async def advertise_only(route):
            response = await route.fetch(timeout=15000)
            assert response.status == 200
            value = await response.json()
            assert value["capabilities"]["assistant"] is False
            value["capabilities"].update(assistant=True, vision=True, speech=True)
            await route.fulfill(response=response, json=value)

        await page.route(AX + "/api/team", advertise_only)
        await page.reload()
        await expect(page.locator("#workspace")).to_have_attribute("aria-busy", "false")
        await expect(page.locator("#roomTitle")).to_have_text(room_title)
        assert await device_summary() == 0, "Restoring the room reopened camera access"
        draft = "Synthetic typed draft retained while describing a separate camera still."
        await (await callspace_control(page, "#messageInput")).fill(draft)
        await callspace_click(page, "#openCamera")
        await ready()
        await expect(page.locator("#describeCamera")).to_be_enabled()
        assert not media_requests
        async with page.expect_response(lambda r: matching(r, "attachments")) as upload_info, \
                page.expect_response(lambda r: matching(r, "messages")) as question_info:
            await callspace_click(page, "#describeCamera")
        upload, question = await upload_info.value, await question_info.value
        assert upload.status == 200, "Real protected camera attachment was not accepted"
        assert question.status == 503 and await question.json() == {
            "detail": "The team workspace is temporarily unavailable."}
        await expect(page.locator("#cameraStatus")).to_have_attribute("data-error", "true")
        await ready()
        await expect(page.locator("#messageInput")).to_have_value(draft)
        body, result = upload.request.post_data_json, await upload.json()
        assert set(body) == {"data_url", "source", "connection_id", "captured_at"}
        assert body["source"] == "camera_preview" and str(UUID(body["connection_id"])) == body["connection_id"]
        captured = datetime.fromisoformat(body["captured_at"].replace("Z", "+00:00"))
        assert captured.utcoffset() is not None and abs((datetime.now(timezone.utc) - captured).total_seconds()) < 60
        assert body["data_url"].startswith("data:image/jpeg;base64,")
        jpeg = base64.b64decode(body["data_url"].split(",", 1)[1], validate=True)
        assert 0 < len(jpeg) <= 512 * 1024 and jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
        image = await page.evaluate("""async source => {
          const image = new Image(); image.src=source; await image.decode();
          const canvas=document.createElement('canvas'); canvas.width=32; canvas.height=18;
          const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0,32,18);
          const pixels=ctx.getImageData(0,0,32,18).data; let sum=0,squares=0,n=0;
          for(let i=0;i<pixels.length;i++) if(i%4!==3){sum+=pixels[i];squares+=pixels[i]*pixels[i];n++;}
          return {width:image.naturalWidth,height:image.naturalHeight,variance:squares/n-(sum/n)**2};
        }""", body["data_url"])
        assert 0 < image["width"] <= 960 and 0 < image["height"] <= 540 and image["variance"] > 20
        attachment = result["attachment"]
        assert attachment["source"] == "camera_preview" and attachment["participant_id"] == profile["me"]["id"]
        presence = (await api(page, f"/api/team/rooms/{room_id}"))["data"]["presence"]
        own = next(item for item in presence if item["connection_id"] == body["connection_id"])
        assert own["user_id"] == profile["me"]["id"] and not any(own[key] for key in ("in_call", "microphone", "camera", "screen"))
        question_body = question.request.post_data_json
        assert set(question_body) == {"client_message_id", "text", "ask_reign", "speak", "attachment_ids"}
        assert question_body["ask_reign"] is True and question_body["speak"] is True
        assert question_body["attachment_ids"] == [attachment["id"]] and question_body["text"] != draft
        assert str(UUID(question_body["client_message_id"])) == question_body["client_message_id"]
        await callspace_click(page, "#closeCamera")
        await stopped()
        async with page.expect_response(lambda r: matching(r, "messages")) as retry_info:
            await callspace_click(page, "#retryMessage")
        retry = await retry_info.value
        assert retry.status == 503 and retry.request.post_data_json == question_body
        await expect(page.locator("#messageInput")).to_have_value(draft)
        await expect(page.locator("#retryMessage")).to_be_enabled()
        await page.wait_for_timeout(400)
        assert sum(urlsplit(r.url).path.endswith("/attachments") for r in media_requests) == 1
        assert sum(urlsplit(r.url).path.endswith("/messages") for r in media_requests) == 2
        assert not any(urlsplit(r.url).path.endswith(("/signals", "/transcriptions")) for r in media_requests)
        receipt.update(attachment_status=upload.status, description_status=question.status, retry_status=retry.status,
                       jpeg_bytes=len(jpeg), jpeg_width=image["width"], jpeg_height=image["height"],
                       pixel_variance=round(image["variance"], 2), explicit_uploads=1, explicit_message_attempts=2)
        # Camera bytes and attachment/message identities stay in process memory;
        # only dimensions, status and counters enter the retained CI receipt.
        del jpeg, body, result, question_body
        await page.screenshot(path=str(OUTPUT / "team-camera-provider-unavailable.png"))
        passed("T19 explicit Describe uploads real bounded JPEG through protected viewer admission; actual provider 503 retains draft; Retry reuses the same message and image")

        async def held_logout(route):
            logout_started.set()
            await asyncio.wait_for(release_logout.wait(), timeout=10)
            await route.continue_()

        await page.route(AX + "/logout", held_logout)
        await callspace_click(page, "#openCamera")
        await ready()
        total_requests = prior_device_requests + await device_summary()
        # Exercise the real logout handler while the modal is open, and hold its
        # server request so navigation cannot hide a missing media teardown.
        await page.locator("#logoutButton").evaluate("button=>button.click()")
        await asyncio.wait_for(logout_started.wait(), timeout=5)
        await stopped()
        await expect(page.locator("#cameraDialog")).to_be_hidden()
        assert prior_device_requests + await device_summary() == total_requests
        release_logout.set()
        await expect(page.locator("#loginForm")).to_be_visible()
        receipt.update(status="PASS", camera_requests=total_requests, microphone_requests=0, peer_connections=0,
                       cleanup=["stop", "close", "room transition", "logout before server acknowledgement"])
        passed("T20 camera logout cleanup completes before the server responds; isolated flow never requests a microphone or creates a peer")
    finally:
        release_logout.set()
        await context.close()


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cases, errors, employee_requests, owner_requests = [], [], [], []
    camera_receipt, callspace_receipt = {}, {}
    def passed(name):
        cases.append({"case": name, "status": "PASS", "evidence": "Disposable CI containers / Chromium"})
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage", "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        owner = await browser.new_context(ignore_https_errors=True, permissions=["microphone", "camera"])
        employee = await browser.new_context(ignore_https_errors=True, permissions=["microphone", "camera"])
        await owner.add_init_script(PROBE)
        await employee.add_init_script(PROBE)
        page = await owner.new_page()
        member_page = await employee.new_page()
        for current in (page, member_page):
            current.set_default_timeout(30000)
            current.on("pageerror", lambda error: errors.append(str(error)))
        member_page.on("request", lambda request: employee_requests.append(urlsplit(request.url).path))
        page.on("request", lambda request: owner_requests.append(urlsplit(request.url).path))
        member_page.on("dialog", lambda dialog: dialog.accept())
        page.on("dialog", lambda dialog: dialog.accept())
        password = ""
        try:
            await login(page, "owner", os.environ["AX_ADMIN_PASSWORD"])
            passed("T01 owner enters protected shared workspace through the fixed return path")
            await provider_picker_check(page)
            desktop = page.viewport_size
            await page.set_viewport_size({"width": 320, "height": 720})
            await provider_picker_check(page)
            await page.set_viewport_size(desktop)
            passed("T17 mobile and desktop account connections support keyboard access and independent provider availability")
            profile = await api(page, "/api/team")
            assert profile["status"] == 200 and profile["data"]["workspace"]["durable"] is True
            assert profile["data"]["workspace"]["name"] == "Workspace"
            assert profile["data"]["workspace"]["id"] == "rain", "Existing saved workspace identity changed"
            await expect(page.locator("#workspaceName")).to_have_text("Workspace")
            await expect(page.locator("#reignAvatar")).to_have_attribute("data-ready", "true")
            await expect(page.locator("#reignAvatar")).to_have_attribute("data-speaking", "false")
            assert not any(path.startswith(("/avatar-assets/models/", "/avatar-assets/ort/"))
                           for path in owner_requests), "Workspace loaded the heavy neural model"
            passed("T16 Workspace name preserves saved identity and REIGN loads automatically without a neural model")
            if PHASE == "restart":
                saved = json.loads(PERSISTENCE.read_text())
                await callspace_click(page, f'[data-room-id="{saved["room_id"]}"]')
                await expect(page.locator("#roomTitle")).to_have_text(saved["room_title"])
                await expect(page.locator("#eventFeed")).to_contain_text(saved["message"])
                document = await api(page, f'/api/team/rooms/{saved["room_id"]}/documents/{saved["document_id"]}')
                assert document["status"] == 200
                assert document["data"]["document"]["content"] == saved["content"]
                assert document["data"]["document"]["version"] == saved["version"]
                assert await page.evaluate("() => __teamProbe.deviceRequests.length") == 0
                passed("T02 saved room, ordered discussion and document version survive Core/AX restart without media restart")
                await page.screenshot(path=str(OUTPUT / "team-restart-desktop.png"))
                await shared_browser_check(page, saved["room_id"])
                passed("T15 real room browser opens, streams a protected frame, fences private owner discovery and stale control, then closes explicitly")
                await callspace_click(page, "#logoutButton")
                await expect(page.locator("#loginForm")).to_be_visible()
                passed("T03 restored owner can log out")
            else:
                username = "ci-team-" + uuid4().hex[:10]
                await callspace_click(page, "#manageMembersButton")
                await page.locator("#memberDisplayName").fill("Synthetic CI Teammate")
                await page.locator("#memberUsername").fill(username)
                await callspace_click(page, "#createMember")
                await expect(page.locator("#credentialDialog")).to_be_visible()
                password = await page.locator("#credentialPassword").input_value()
                assert len(password) >= 16
                await callspace_click(page, "#closeCredential")
                await expect(page.locator("#credentialPassword")).to_have_value("")
                passed("T04 owner creates employee login through the UI and one-time credential is erased on close")
                room_title = "CI shared room " + uuid4().hex[:8]
                await callspace_click(page, "#newRoomButton")
                await page.locator("#roomTitleInput").fill(room_title)
                await callspace_click(page, "#roomCreateForm button[type=submit]")
                await expect(page.locator("#roomTitle")).to_have_text(room_title)
                await expect(page.locator("#roomView")).to_be_visible()
                room_id = await page.locator('[data-room-id][aria-current="page"]').get_attribute("data-room-id")
                assert room_id
                await login(member_page, username, password)
                password = ""
                await callspace_click(member_page, f'[data-room-id="{room_id}"]')
                await expect(member_page.locator("#roomTitle")).to_have_text(room_title)
                await expect(member_page.locator("#reignAvatar")).to_have_attribute("data-ready", "true")
                await callspace_control(member_page, "#roomList")
                await expect(member_page.locator("#privateWorkspace")).to_be_hidden()
                await expect(member_page.locator("#manageMembersButton")).to_be_hidden()
                assert await page.evaluate("() => __teamProbe.deviceRequests.length") == 0
                assert await member_page.evaluate("() => __teamProbe.deviceRequests.length") == 0
                passed("T05 employee signs in, opens shared room, and restoration requests no microphone or camera")
                message = "Synthetic shared decision retained across restart."
                await (await callspace_control(page, "#messageInput")).fill(message)
                await callspace_click(page, "#sendMessage")
                await expect(member_page.locator("#eventFeed")).to_contain_text(message)
                await (await callspace_control(member_page, "#messageInput")).fill("Synthetic teammate acknowledgement.")
                await callspace_click(member_page, "#sendMessage")
                await expect(page.locator("#eventFeed")).to_contain_text("Synthetic teammate acknowledgement.")
                events = await api(page, f"/api/team/rooms/{room_id}/events?after=0")
                sequences = [item["seq"] for item in events["data"]["events"]]
                assert sequences == sorted(set(sequences))
                passed("T06 two authenticated participants receive durable ordered group messages")
                await callspace_click(page, "#newDocument")
                await page.locator("#documentTitle").fill("Synthetic shared plan")
                await page.locator("#documentContent").fill("# Initial shared plan")
                await callspace_click(page, "#saveDocument")
                await expect(page.locator("#documentVersion")).to_have_text("Saved version 1")
                document_id = await page.locator('[data-document-id][aria-current="true"]').get_attribute("data-document-id")
                await callspace_control(member_page, f'[data-document-id="{document_id}"]')
                await expect(member_page.locator(f'[data-document-id="{document_id}"]')).to_be_visible()
                await callspace_click(member_page, f'[data-document-id="{document_id}"]')
                await expect(member_page.locator("#documentVersion")).to_have_text("Saved version 1")
                draft = "# Employee draft retained after a conflict"
                await member_page.locator("#documentContent").fill(draft)
                content = "# Shared plan\n\nVersion two survives restart."
                await page.locator("#documentContent").fill(content)
                await callspace_click(page, "#saveDocument")
                await expect(page.locator("#documentVersion")).to_have_text("Saved version 2")
                await callspace_click(member_page, "#saveDocument")
                await expect(member_page.locator("#documentConflict")).to_be_visible()
                await expect(member_page.locator("#documentContent")).to_have_value(draft)
                await expect(member_page.locator("#documentVersion")).to_have_text("Saved version 1")
                async with member_page.expect_download() as download_info:
                    await callspace_click(member_page, "#downloadDocument")
                download = await download_info.value
                assert download.suggested_filename.endswith(".md")
                await callspace_click(member_page, "#saveDocumentCopy")
                await expect(member_page.locator("#documentConflict")).to_be_hidden()
                await expect(member_page.locator("#documentStatus")).to_have_text("Saved to this room.")
                passed("T07 stale document save returns conflict, retains exact draft, and supports download or separate copy")
                PERSISTENCE.write_text(json.dumps({"room_id": room_id, "room_title": room_title, "message": message,
                    "document_id": document_id, "content": content, "version": 2}))
                # All values above are synthetic, nonsecret test data. No employee
                # credential is required or retained for the restart phase.
                await callspace_click(page, "#joinCall")
                await callspace_click(member_page, "#joinCall")
                for current in (page, member_page):
                    await expect(current.locator("#toggleMicrophone")).to_have_text("Mic on")
                    await current.wait_for_function("() => __teamProbe.peers.some(pc=>pc.connectionState==='connected')", timeout=45000)
                    await callspace_click(current, "#toggleCamera")
                    await expect(current.locator("#toggleCamera")).to_have_text("Camera on")
                for current in (page, member_page):
                    await current.wait_for_function("""() => [...document.querySelectorAll('#callParticipants video')].filter(v=>v.videoWidth>0&&!v.paused).length >= 2""", timeout=30000)
                    await current.wait_for_function("""async () => {
                      for (const pc of __teamProbe.peers) for (const stat of (await pc.getStats()).values())
                        if (stat.type==='inbound-rtp'&&stat.bytesReceived>0) return true;
                      return false;
                    }""", timeout=30000)
                passed("T08 real WebRTC transport carries synthetic microphone/camera data between two isolated browser contexts")
                await (await callspace_control(page, "#messageInput")).fill("Synthetic draft retained while changing the call view.")
                media_before = await page.evaluate("() => ({devices:__teamProbe.deviceRequests.length, peers:__teamProbe.peers.length})")
                callspace_receipt.update(await callspace_media_roundtrip(page))
                assert callspace_receipt["participantVideos"] >= 2
                assert await page.evaluate("() => ({devices:__teamProbe.deviceRequests.length, peers:__teamProbe.peers.length})") == media_before
                await page.wait_for_function("""() => [...document.querySelectorAll('#callParticipants video')].filter(v=>v.videoWidth>0&&!v.paused).length >= 2 &&
                    __teamProbe.peers.some(peer=>peer.connectionState==='connected') &&
                    __teamProbe.tracks.every(track=>track.readyState==='live')""")
                passed("T21 changing chat, split and full-face views preserves the same live group video, tracks, peers, draft and transcript")
                await expect(page.locator("#callRelayNote")).to_contain_text("relay isn’t configured")
                await callspace_click(page, "#toggleCamera")
                await expect(page.locator("#toggleCamera")).to_have_text("Camera off")
                await member_page.wait_for_function("() => [...document.querySelectorAll('#callParticipants figcaption')].filter(n=>n.textContent.includes('camera')).length===1")
                await callspace_click(page, "#leaveCall")
                await callspace_click(member_page, "#leaveCall")
                for current in (page, member_page):
                    await expect(current.locator("#joinCall")).to_be_visible()
                    await current.wait_for_function("() => __teamProbe.tracks.every(t=>t.readyState==='ended')&&__teamProbe.peers.every(p=>p.connectionState==='closed')")
                    await expect(current.locator("#presenceList")).not_to_contain_text("mic on", timeout=10000)
                    await expect(current.locator("#presenceList")).not_to_contain_text("camera", timeout=10000)
                    await expect(current.locator("#presenceList")).not_to_contain_text("screen", timeout=10000)
                passed("T09 camera disable removes stale remote tile; leaving releases tracks/peers and clears both viewers’ media indicators while preserving room")
                await page.set_viewport_size({"width": 1280, "height": 900})
                saved_draft = await page.locator("#messageInput").input_value()
                resize_draft = "Keep this unsent thought intact when a wide desktop conversation becomes a narrow phone screen."
                await (await callspace_control(page, "#messageInput")).fill(resize_draft)
                await page.locator("#callspaceFace").focus()
                await settled_callspace_view(page, "chat")
                desktop_draft_height = await page.locator("#messageInput").evaluate("input => input.getBoundingClientRect().height")
                assert desktop_draft_height < 128, "Resize fixture must fit below the textarea height cap on desktop"
                draft_sizes = []
                for width in (360, 390, 430, 1280):
                    await page.set_viewport_size({"width": width, "height": 900})
                    await settled_callspace_view(page, "chat")
                    # No input/focus event is sent after resize: wrapping itself
                    # must update the existing draft's height and reserved space.
                    await page.wait_for_function("""() => {
                        const input = document.getElementById('messageInput');
                        return Math.abs(input.getBoundingClientRect().height - Math.min(128, Math.max(49, input.scrollHeight))) <= 1;
                    }""")
                    draft_geometry = await page.evaluate("""() => {
                        const input = document.getElementById('messageInput');
                        const actions = document.querySelector('.callspace-composer .composer-actions');
                        return {height: input.getBoundingClientRect().height, scrollHeight: input.scrollHeight,
                            feedBottom: document.getElementById('eventFeed').getBoundingClientRect().bottom,
                            transcriptBottom: document.getElementById('callspaceTranscript').getBoundingClientRect().bottom,
                            actionsTop: actions.getBoundingClientRect().top,
                            actionsVisible: getComputedStyle(actions).display !== 'none'};
                    }""")
                    await expect(page.locator("#messageInput")).to_have_value(resize_draft)
                    assert draft_geometry["actionsVisible"] is True
                    assert draft_geometry["feedBottom"] <= draft_geometry["actionsTop"] + 1, draft_geometry
                    assert draft_geometry["transcriptBottom"] <= draft_geometry["actionsTop"] + 1, draft_geometry
                    if width < 600:
                        assert draft_geometry["height"] > desktop_draft_height, draft_geometry
                    else:
                        assert abs(draft_geometry["height"] - desktop_draft_height) <= 1, draft_geometry
                    draft_sizes.append({"width": width, **draft_geometry})
                    assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
                    for selector in ("#logoutButton", "#askReign", "#sendMessage"):
                        await (await callspace_control(page, selector)).scroll_into_view_if_needed()
                        assert await page.locator(selector).evaluate("el=>{const r=el.getBoundingClientRect();return r.width>=40&&r.height>=32}")
                    await close_callspace_panel(page)
                    await page.locator("#callspaceShell").scroll_into_view_if_needed()
                    await settled_callspace_view(page, "chat")
                    await page.screenshot(path=str(OUTPUT / f"team-initial-{width}.png"), full_page=width == 390)
                    if width == 390:
                        for mode in ("split", "call"):
                            await callspace_mode(page, mode)
                            await page.screenshot(path=str(OUTPUT / f"team-callspace-{mode}-390.png"))
                        await callspace_mode(page, "chat")
                passed("T10 shared workspace fits 360/390/430/1280 widths with usable action targets")
                callspace_receipt["responsiveDraft"] = draft_sizes
                passed("T22 an existing long draft grows and shrinks with the viewport while the transcript stays above its visible actions")
                await (await callspace_control(page, "#messageInput")).fill(saved_draft)
                assert not any(path.startswith(("/api/chat", "/api/sessions", "/api/accounts", "/api/voice")) for path in employee_requests)
                assert not any(path.endswith(".onnx") or "/avatar-assets/ort/" in path for path in employee_requests)
                passed("T11 employee shared page requests no private owner API or neural model runtime")
                if profile["data"]["capabilities"].get("assistant") is False:
                    await expect(page.locator("#askReign")).to_be_disabled()
                    passed("T12 absent model provider is shown honestly and does not block room chat/documents")
                await callspace_click(page, "#manageMembersButton")
                await page.locator(".managed-member").filter(has_text=username).get_by_role("button", name="Revoke access").click()
                await expect(page.locator("#memberStatus")).to_contain_text("has been revoked")
                await expect(member_page.locator("#loginForm")).to_be_visible(timeout=35000)
                assert (await employee.request.get(AX + "/api/team" )).status == 401
                passed("T13 owner revocation ends employee access on the existing page and denies subsequent API reads")
                await callspace_click(page, "#closeMembers")
                await callspace_click(page, "#logoutButton")
                await expect(page.locator("#loginForm")).to_be_visible()
                passed("T14 owner logs out without deleting saved room or shared history")
                await camera_check(browser, room_id, room_title, passed, errors, camera_receipt)
            assert not errors, "JavaScript runtime errors: " + repr(errors)
        finally:
            password = ""
            (OUTPUT / f"team-{PHASE}-results.json").write_text(json.dumps({"phase": PHASE,
                "scope": "CI containers; synthetic devices; no physical-device or public-network claim",
                "cases": cases, "page_errors": errors, "camera": camera_receipt, "callspace": callspace_receipt}, indent=2))
            await employee.close()
            await owner.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
