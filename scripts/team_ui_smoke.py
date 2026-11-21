"""Shared-room acceptance in disposable CI containers, using real Chromium.

The two-context call uses Chromium's synthetic microphone/camera devices. It
does not establish physical-device or cross-network/TURN reliability. No model
provider credentials or employee passwords are written to evidence.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import async_playwright, expect

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
    await page.locator("#loginForm button").click()
    await expect(page).to_have_url(AX + "/team")
    await expect(page.locator("#workspace")).to_have_attribute("aria-busy", "false")
    await expect(page.locator("#accountName")).not_to_have_text("Checking sign-in…")


async def shared_browser_check(page, room_id):
    """Run after the baseline restart phase has released its private slot."""
    path = f"/api/team/rooms/{room_id}/browser"
    await page.locator("#browserPanel > summary").click()
    await page.locator("#browserUrl").fill("https://alambda-forge.onrender.com/")
    await page.locator("#openBrowser").click()
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
    await page.locator("#takeBrowser").click()
    await expect(page.locator("#returnBrowser")).to_be_visible()
    status = (await api(page, path))["data"]
    assert status["session"]["mode"] == "human"
    stale = await api(page, path + "/input", "POST", {"connection_id": status["control"]["connection_id"],
        "epoch": status["session"]["epoch"] - 1, "kind": "key", "key": "Tab"})
    assert stale["status"] == 409
    await expect(page.locator("#browserTab")).to_be_enabled()
    await page.locator("#browserTab").click()
    await expect(page.locator("#returnBrowser")).to_be_enabled()
    await page.locator("#returnBrowser").click()
    await expect(page.locator("#takeBrowser")).to_be_enabled()
    assert (await api(page, path))["data"]["session"]["mode"] == "agent"
    await page.screenshot(path=str(OUTPUT / "team-restart-shared-browser.png"))
    await page.locator("#closeBrowser").click()
    await expect(page.locator("#browserFrame")).to_be_hidden()
    assert (await api(page, path))["data"]["status"] == "closed"


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cases, errors, employee_requests = [], [], []
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
        member_page.on("dialog", lambda dialog: dialog.accept())
        page.on("dialog", lambda dialog: dialog.accept())
        password = ""
        try:
            await login(page, "owner", os.environ["AX_ADMIN_PASSWORD"])
            passed("T01 owner enters protected shared workspace through the fixed return path")
            profile = await api(page, "/api/team")
            assert profile["status"] == 200 and profile["data"]["workspace"]["durable"] is True
            if PHASE == "restart":
                saved = json.loads(PERSISTENCE.read_text())
                await page.locator(f'[data-room-id="{saved["room_id"]}"]').click()
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
                await page.locator("#logoutButton").click()
                await expect(page.locator("#loginForm")).to_be_visible()
                passed("T03 restored owner can log out")
            else:
                username = "ci-team-" + uuid4().hex[:10]
                await page.locator("#manageMembersButton").click()
                await page.locator("#memberDisplayName").fill("Synthetic CI Teammate")
                await page.locator("#memberUsername").fill(username)
                await page.locator("#createMember").click()
                await expect(page.locator("#credentialDialog")).to_be_visible()
                password = await page.locator("#credentialPassword").input_value()
                assert len(password) >= 16
                await page.locator("#closeCredential").click()
                await expect(page.locator("#credentialPassword")).to_have_value("")
                passed("T04 owner creates employee login through the UI and one-time credential is erased on close")
                room_title = "CI shared room " + uuid4().hex[:8]
                await page.locator("#newRoomButton").click()
                await page.locator("#roomTitleInput").fill(room_title)
                await page.locator("#roomCreateForm button[type=submit]").click()
                await expect(page.locator("#roomTitle")).to_have_text(room_title)
                await expect(page.locator("#roomView")).to_be_visible()
                room_id = await page.locator('[data-room-id][aria-current="page"]').get_attribute("data-room-id")
                assert room_id
                await login(member_page, username, password)
                password = ""
                await member_page.locator(f'[data-room-id="{room_id}"]').click()
                await expect(member_page.locator("#roomTitle")).to_have_text(room_title)
                await expect(member_page.locator("#privateWorkspace")).to_be_hidden()
                await expect(member_page.locator("#manageMembersButton")).to_be_hidden()
                assert await page.evaluate("() => __teamProbe.deviceRequests.length") == 0
                assert await member_page.evaluate("() => __teamProbe.deviceRequests.length") == 0
                passed("T05 employee signs in, opens shared room, and restoration requests no microphone or camera")
                message = "Synthetic shared decision retained across restart."
                await page.locator("#messageInput").fill(message)
                await page.locator("#sendMessage").click()
                await expect(member_page.locator("#eventFeed")).to_contain_text(message)
                await member_page.locator("#messageInput").fill("Synthetic teammate acknowledgement.")
                await member_page.locator("#sendMessage").click()
                await expect(page.locator("#eventFeed")).to_contain_text("Synthetic teammate acknowledgement.")
                events = await api(page, f"/api/team/rooms/{room_id}/events?after=0")
                sequences = [item["seq"] for item in events["data"]["events"]]
                assert sequences == sorted(set(sequences))
                passed("T06 two authenticated participants receive durable ordered group messages")
                await page.locator("#newDocument").click()
                await page.locator("#documentTitle").fill("Synthetic shared plan")
                await page.locator("#documentContent").fill("# Initial shared plan")
                await page.locator("#saveDocument").click()
                await expect(page.locator("#documentVersion")).to_have_text("Saved version 1")
                document_id = await page.locator('[data-document-id][aria-current="true"]').get_attribute("data-document-id")
                await expect(member_page.locator(f'[data-document-id="{document_id}"]')).to_be_visible()
                await member_page.locator(f'[data-document-id="{document_id}"]').click()
                await expect(member_page.locator("#documentVersion")).to_have_text("Saved version 1")
                draft = "# Employee draft retained after a conflict"
                await member_page.locator("#documentContent").fill(draft)
                content = "# Shared plan\n\nVersion two survives restart."
                await page.locator("#documentContent").fill(content)
                await page.locator("#saveDocument").click()
                await expect(page.locator("#documentVersion")).to_have_text("Saved version 2")
                await member_page.locator("#saveDocument").click()
                await expect(member_page.locator("#documentConflict")).to_be_visible()
                await expect(member_page.locator("#documentContent")).to_have_value(draft)
                await expect(member_page.locator("#documentVersion")).to_have_text("Saved version 1")
                async with member_page.expect_download() as download_info:
                    await member_page.locator("#downloadDocument").click()
                download = await download_info.value
                assert download.suggested_filename.endswith(".md")
                await member_page.locator("#saveDocumentCopy").click()
                await expect(member_page.locator("#documentConflict")).to_be_hidden()
                await expect(member_page.locator("#documentStatus")).to_have_text("Saved to this room.")
                passed("T07 stale document save returns conflict, retains exact draft, and supports download or separate copy")
                PERSISTENCE.write_text(json.dumps({"room_id": room_id, "room_title": room_title, "message": message,
                    "document_id": document_id, "content": content, "version": 2}))
                # All values above are synthetic, nonsecret test data. No employee
                # credential is required or retained for the restart phase.
                await page.locator("#joinCall").click()
                await member_page.locator("#joinCall").click()
                for current in (page, member_page):
                    await expect(current.locator("#toggleMicrophone")).to_have_text("Mic on")
                    await current.wait_for_function("() => __teamProbe.peers.some(pc=>pc.connectionState==='connected')", timeout=45000)
                    await current.locator("#toggleCamera").click()
                    await expect(current.locator("#toggleCamera")).to_have_text("Camera on")
                for current in (page, member_page):
                    await current.wait_for_function("""() => [...document.querySelectorAll('#callParticipants video')].filter(v=>v.videoWidth>0&&!v.paused).length >= 2""", timeout=30000)
                    await current.wait_for_function("""async () => {
                      for (const pc of __teamProbe.peers) for (const stat of (await pc.getStats()).values())
                        if (stat.type==='inbound-rtp'&&stat.bytesReceived>0) return true;
                      return false;
                    }""", timeout=30000)
                passed("T08 real WebRTC transport carries synthetic microphone/camera data between two isolated browser contexts")
                await expect(page.locator("#callRelayNote")).to_contain_text("relay isn’t configured")
                await page.locator("#toggleCamera").click()
                await expect(page.locator("#toggleCamera")).to_have_text("Camera off")
                await member_page.wait_for_function("() => [...document.querySelectorAll('#callParticipants figcaption')].filter(n=>n.textContent.includes('camera')).length===1")
                await page.locator("#leaveCall").click()
                await member_page.locator("#leaveCall").click()
                for current in (page, member_page):
                    await expect(current.locator("#joinCall")).to_be_visible()
                    await current.wait_for_function("() => __teamProbe.tracks.every(t=>t.readyState==='ended')&&__teamProbe.peers.every(p=>p.connectionState==='closed')")
                    await expect(current.locator("#presenceList")).not_to_contain_text("mic on", timeout=10000)
                    await expect(current.locator("#presenceList")).not_to_contain_text("camera", timeout=10000)
                    await expect(current.locator("#presenceList")).not_to_contain_text("screen", timeout=10000)
                passed("T09 camera disable removes stale remote tile; leaving releases tracks/peers and clears both viewers’ media indicators while preserving room")
                for width in (360, 390, 430, 1280):
                    await page.set_viewport_size({"width": width, "height": 900})
                    assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
                    for selector in ("#logoutButton", "#askReign", "#sendMessage"):
                        await page.locator(selector).scroll_into_view_if_needed()
                        assert await page.locator(selector).evaluate("el=>{const r=el.getBoundingClientRect();return r.width>=40&&r.height>=32}")
                    await page.locator(".app-header").scroll_into_view_if_needed()
                    await page.screenshot(path=str(OUTPUT / f"team-initial-{width}.png"), full_page=width == 390)
                passed("T10 shared workspace fits 360/390/430/1280 widths with usable action targets")
                assert not any(path.startswith(("/api/chat", "/api/sessions", "/api/accounts", "/api/voice")) for path in employee_requests)
                assert not any(path.endswith(".onnx") or "/avatar-assets/ort/" in path for path in employee_requests)
                passed("T11 employee shared page requests no private owner API or neural model runtime")
                if profile["data"]["capabilities"].get("assistant") is False:
                    await expect(page.locator("#askReign")).to_be_disabled()
                    passed("T12 absent model provider is shown honestly and does not block room chat/documents")
                await page.locator("#manageMembersButton").click()
                await page.locator(".managed-member").filter(has_text=username).get_by_role("button", name="Revoke access").click()
                await expect(page.locator("#memberStatus")).to_contain_text("has been revoked")
                await expect(member_page.locator("#loginForm")).to_be_visible(timeout=35000)
                assert (await employee.request.get(AX + "/api/team" )).status == 401
                passed("T13 owner revocation ends employee access on the existing page and denies subsequent API reads")
                await page.locator("#closeMembers").click()
                await page.locator("#logoutButton").click()
                await expect(page.locator("#loginForm")).to_be_visible()
                passed("T14 owner logs out without deleting saved room or shared history")
            assert not errors, "JavaScript runtime errors: " + repr(errors)
        finally:
            password = ""
            (OUTPUT / f"team-{PHASE}-results.json").write_text(json.dumps({"phase": PHASE,
                "scope": "CI containers; synthetic devices; no physical-device or public-network claim",
                "cases": cases, "page_errors": errors}, indent=2))
            await employee.close()
            await owner.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
