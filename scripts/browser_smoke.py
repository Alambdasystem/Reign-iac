"""Real four-container acceptance. No mocked Core, Temporal or Computer transport.

Uses public Git High only. AX uses a disposable TLS certificate inside the Docker
test network. This is automated acceptance, not James's human takeover signoff.
The containers have no voice-provider API key: voice assertions cover honest
configuration/authentication behavior, not a live microphone or audible call.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from temporalio.client import Client
from temporalio.api.enums.v1 import EventType

TARGET = "https://alambda-forge.onrender.com/"
AX = "https://ax:10000"
EVIDENCE = Path("/evidence")
VOICE_OFFER = {
    "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=REIGN CI no-provider offer\r\n"
           "t=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n"
}

# This fixture is intentionally separate from real four-service acceptance. It
# drives the assembled DOM/controller with synthetic provider and graph events;
# no API key, physical microphone, audible output or workspace action is used.
SIMULATED_VOICE_BOOTSTRAP = r"""
(() => {
  const fixture = window.__reignVoiceSimulation = {
    denied: false, mediaRequests: 0, tracks: [], sources: [], peers: [],
    sent: [], outputs: [], narrations: [], sequence: 0
  };
  const media = navigator.mediaDevices || {};
  if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', {value: media});
  Object.defineProperty(media, 'getUserMedia', {value: async () => {
    fixture.mediaRequests++;
    if (fixture.denied) throw new DOMException('Simulated microphone denial', 'NotAllowedError');
    const context = new AudioContext(), source = context.createConstantSource();
    const destination = context.createMediaStreamDestination();
    source.offset.value = 0; source.connect(destination); source.start();
    fixture.sources.push({context, source});
    fixture.tracks.push(...destination.stream.getTracks());
    return destination.stream;
  }});
  class Channel {
    constructor() { this.readyState = 'connecting'; }
    emit(value) { this.onmessage?.({data: JSON.stringify(value)}); }
    delegate(message, metadata) {
      const id = 'simulated-response-' + ++fixture.sequence;
      const call = {type:'function_call',call_id:'simulated-call-' + fixture.sequence,
        name:'delegate_to_reign',arguments:JSON.stringify({message})};
      this.emit({type:'response.created',response:{id,metadata}});
      this.emit({type:'response.function_call_arguments.done',response_id:id,
        call_id:call.call_id,name:call.name,arguments:call.arguments});
      this.emit({type:'response.done',response:{id,status:'completed',output:[call]}});
    }
    send(raw) {
      const value = JSON.parse(raw); fixture.sent.push(value);
      if (value.type === 'conversation.item.create' && value.item?.type === 'function_call_output') {
        fixture.outputs.push(value.item); return;
      }
      if (value.type !== 'response.create') return;
      if (value.response.tool_choice?.name === 'delegate_to_reign') {
        queueMicrotask(() => this.delegate('Synthetic voice connection check. Do not run workspace actions.', value.response.metadata));
      } else if (value.response.tool_choice === 'none') {
        const result = JSON.parse(fixture.outputs.at(-1).output);
        fixture.narrations.push(result);
        queueMicrotask(() => {
          const id = 'simulated-narration-' + ++fixture.sequence;
          this.emit({type:'response.created',response:{id,metadata:value.response.metadata}});
          this.emit({type:'output_audio_buffer.started',response_id:id});
          this.emit({type:'response.output_audio_transcript.delta',response_id:id,delta:result.answer});
          this.emit({type:'response.output_audio_transcript.done',response_id:id,transcript:result.answer});
          this.emit({type:'response.done',response:{id,status:'completed'}});
          setTimeout(() => this.emit({type:'output_audio_buffer.stopped',response_id:id}), 30);
        });
      }
    }
    close() { this.readyState = 'closed'; }
  }
  class Peer extends EventTarget {
    constructor() {
      super(); this.connectionState = 'new'; this.iceGatheringState = 'complete';
      this.tracks = []; fixture.peers.push(this);
    }
    addTrack(track) { this.tracks.push(track); }
    createDataChannel() { return this.channel = new Channel(); }
    async createOffer() { return {type:'offer',sdp:'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'}; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription(value) {
      this.remoteDescription = value;
      setTimeout(() => {
        if (this.connectionState === 'closed') return;
        this.connectionState = 'connected'; this.onconnectionstatechange?.();
        this.channel.readyState = 'open'; this.channel.onopen?.();
        this.channel.emit({type:'session.created',session:{id:'simulated-session'}});
      }, 0);
    }
    async getStats() { return new Map(); }
    close() { this.connectionState = 'closed'; }
  }
  Object.defineProperty(window, 'RTCPeerConnection', {value: Peer});
  fixture.speak = text => {
    const channel = fixture.peers.at(-1).channel;
    channel.emit({type:'input_audio_buffer.speech_started',item_id:'simulated-user-audio'});
    channel.emit({type:'input_audio_buffer.speech_stopped',item_id:'simulated-user-audio'});
    channel.emit({type:'conversation.item.input_audio_transcription.completed',transcript:text});
    channel.delegate(text);
  };
})();
"""


async def simulated_voice_ui(browser, storage_state, origin=AX):
    """Exercise real AX scripts with isolated, clearly synthetic media/API data."""
    thread = "00000000-0000-4000-8000-000000000001"

    async def scenario(denied=False, expire=False):
        context = await browser.new_context(ignore_https_errors=True, storage_state=storage_state)
        await context.add_init_script(SIMULATED_VOICE_BOOTSTRAP +
                                      ("\nwindow.__reignVoiceSimulation.denied = true;" if denied else ""))
        state = {"posts": [], "released": 0, "offers": 0, "deletes": 0, "expired": False}
        posted = asyncio.Queue()

        def graph_state():
            turn = len(state["posts"])
            completed = state["released"] >= turn
            answer = "Synthetic verified greeting." if turn == 1 else "Synthetic verified workspace answer."
            return {"thread_id": thread, "run_id": "simulated-run-" + str(turn),
                    "status": "completed" if completed else "running", "session_id": None,
                    "events": [{"sequence": 1, "kind": "completed"}] if completed else [],
                    "answer": answer if completed else "", "error": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "turns": turn, "max_turns": 24, "continuable": True}

        async def fixture_route(route):
            request = route.request
            path = urlsplit(request.url).path
            if state["expired"] and path == "/api/voice":
                await route.fulfill(status=401, json={"detail": "Simulated session expiration"})
                return
            if path == "/api/voice":
                result = {"configured": True, "model": "simulated-realtime-model",
                          "voice": "simulated-voice", "max_duration_seconds": 900}
            elif path == "/api/voice/offer":
                assert request.method == "POST"
                assert list(request.post_data_json) == ["sdp"]
                state["offers"] += 1
                result = {"sdp": VOICE_OFFER["sdp"], "type": "answer",
                          "model": "simulated-realtime-model", "max_duration_seconds": 900}
            elif path == "/api/agent":
                result = {"configured": True, "model": "simulated-langgraph-model", "max_turns": 24}
            elif path == "/api/chat" and request.method == "POST":
                payload = request.post_data_json
                assert payload["thread_id"] == (thread if state["posts"] else None)
                assert payload["session_id"] is None
                state["posts"].append(payload)
                posted.put_nowait(payload)
                result = graph_state()
            elif path == "/api/chat/" + thread and request.method == "GET":
                result = graph_state()
            elif path == "/api/chat/" + thread and request.method == "DELETE":
                state["deletes"] += 1
                result = {"status": "cancelled"}
            else:
                raise AssertionError("Unexpected simulated voice API request: " + request.method + " " + path)
            await route.fulfill(status=200, json=result)

        for pattern in ("/api/voice**", "/api/agent", "/api/chat**"):
            await context.route(origin + pattern, fixture_route)
        page = await context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(origin)
            await expect(page.locator("#voiceStart")).to_be_enabled()
            await expect(page.locator("#voiceStatus")).to_contain_text("Start voice")
            state["expired"] = expire
            await page.locator("#voiceStart").click()
            if expire:
                await expect(page.locator("#loginPanel")).to_be_visible()
                await expect(page.locator("#console")).to_be_hidden()
                assert await page.evaluate("window.__reignVoiceSimulation.mediaRequests") == 0
                assert state["offers"] == 0 and not state["posts"]
            elif denied:
                await expect(page.locator("#voiceStatus")).to_contain_text("permission was denied")
                await expect(page.locator("#voiceStart")).to_be_enabled()
                await expect(page.locator("#voiceEnd")).to_be_hidden()
                assert state["offers"] == 0 and not state["posts"]
            else:
                await expect(page.locator("#voiceEnd")).to_be_visible()
                await asyncio.wait_for(posted.get(), 15)
                await expect(page.locator("#feed")).to_contain_text("VOICE CHECK")
                await expect(page.locator("#sendCommand")).to_be_disabled()
                await page.evaluate("""() => {
                    document.querySelector('#message').value = 'Blocked simultaneous typed request';
                    document.querySelector('#form').dispatchEvent(new Event('submit', {cancelable:true}));
                }""")
                assert len(state["posts"]) == 1, "typed input must not overtake a pending voice graph run"
                assert await page.evaluate("window.__reignVoiceSimulation.narrations.length") == 0
                state["released"] = 1
                await expect(page.locator("#voiceCaption")).to_contain_text("Synthetic verified greeting.")
                await expect(page.locator("#sendCommand")).to_be_enabled()
                await page.locator("#mute").click()
                await expect(page.locator("#mute")).to_have_attribute("aria-pressed", "true")
                assert await page.evaluate("window.__reignVoiceSimulation.tracks.every(track => !track.enabled)")
                await page.locator("#mute").click()
                assert await page.evaluate("window.__reignVoiceSimulation.tracks.every(track => track.enabled)")
                await page.evaluate("window.__reignVoiceSimulation.speak('Synthetic workspace question')")
                await asyncio.wait_for(posted.get(), 15)
                await expect(page.locator("#feed")).to_contain_text("Synthetic workspace question")
                await expect(page.locator("#sendCommand")).to_be_disabled()
                assert len(state["posts"]) == 2
                assert state["posts"][1]["thread_id"] == thread, "voice turns must share the existing graph thread"
                state["released"] = 2
                await expect(page.locator("#voiceCaption")).to_contain_text("Synthetic verified workspace answer.")
                await expect(page.locator("#sendCommand")).to_be_enabled()
                await page.locator("#voiceEnd").click()
                await expect(page.locator("#voiceStart")).to_be_enabled()
                await expect(page.locator("#voiceEnd")).to_be_hidden()
                await expect(page.locator("#mute")).to_be_hidden()
                assert await page.evaluate("""() => {
                    const fixture = window.__reignVoiceSimulation;
                    return fixture.tracks.length > 0 && fixture.tracks.every(track => track.readyState === 'ended') &&
                        fixture.peers.every(peer => peer.connectionState === 'closed' && peer.channel.readyState === 'closed') &&
                        document.querySelector('#voiceAudio').srcObject === null;
                }"""), "End call must release the actual synthetic tracks and transport"
                assert state["offers"] == 1 and state["deletes"] == 0
                await page.reload()
                await expect(page.locator("#voiceStart")).to_be_enabled()
                await expect(page.locator("#sendCommand")).to_be_enabled()
                assert await page.evaluate("window.__reignVoiceSimulation.mediaRequests") == 0
                # The completed conversation survives End. A later active run
                # belongs to its call and must be cancelled once when End wins.
                await page.locator("#voiceStart").click()
                await asyncio.wait_for(posted.get(), 15)
                await expect(page.locator("#sendCommand")).to_be_disabled()
                await page.locator("#voiceEnd").click()
                await expect(page.locator("#voiceStart")).to_be_enabled()
                await expect(page.locator("#sendCommand")).to_be_enabled()
                assert state["deletes"] == 1, "End must cancel its unfinished graph request exactly once"
                assert await page.evaluate("window.__reignVoiceSimulation.tracks.every(track => track.readyState === 'ended')")
                assert await page.evaluate("window.__reignVoiceSimulation.narrations.length") == 0
            assert not errors, errors
        finally:
            await context.close()

    await scenario(False)
    await scenario(True)
    await scenario(expire=True)
    return {"status": "passed", "mode": "simulated_provider_graph_and_microphone",
            "checks": ["graph_completion_before_narration", "shared_graph_lock_and_thread",
                       "start_mute_end_and_track_cleanup", "configured_reload", "owned_graph_cancellation",
                       "microphone_denial", "expired_session_returns_to_sign_in"],
            "live_provider_or_audible_audio": "not_tested"}


async def main():
    temporal = await Client.connect("temporal:7233")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.set_default_timeout(120000)
        request = context.request
        assert (await request.get(AX + "/api/readyz")).status == 401
        ready = await request.get(AX + "/api/readyz",
                                  headers={"Authorization": "Bearer " + os.environ["CORE_TOKEN"]})
        assert ready.status == 200, "AX dependency readiness failed"
        assert (await ready.json())["components"] == {
            "core": "ready", "temporal": "ready", "computer": "ready"}
        await page.goto(AX)
        await expect(page.locator('main[data-design="ultraview-ax-v1-live"]')).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(EVIDENCE / "ultraview-mobile-login.png"))
        await page.locator("#password").fill(os.environ["AX_ADMIN_PASSWORD"])
        await page.locator("#loginForm button").click()
        await expect(page.locator("#open")).to_be_visible()
        me = await request.get(AX + "/api/me")
        assert me.status == 200
        assert (await me.json())["capabilities"]["voice"] is True
        assert (await request.get(AX + "/api/status")).status == 200
        voice_config = await request.get(AX + "/api/voice")
        assert voice_config.status == 200
        assert (await voice_config.json())["configured"] is False, "CI must not use a real voice-provider key"
        await expect(page.locator("#voiceStatus")).to_contain_text("not configured")
        await expect(page.locator("#voiceStart")).to_be_disabled()
        await expect(page.locator("#mute")).to_be_disabled()
        for width, height in [(390, 844), (430, 932), (1280, 900)]:
            await page.set_viewport_size({"width": width, "height": height})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
            await page.screenshot(path=str(EVIDENCE / f"ultraview-{width}.png"))
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
            assert (await anonymous.get(AX + "/api/voice")).status == 401
            assert (await anonymous.post(AX + "/api/voice/offer", data=VOICE_OFFER)).status == 401
            await anonymous.dispose()
            assert (await request.post(AX + "/api/sessions", data={})).status == 403
            assert (await request.post(AX + "/api/voice/offer", data=VOICE_OFFER)).status == 403
            voice_unconfigured = await api("/api/voice/offer", "POST", VOICE_OFFER, status=503)
            assert (await voice_unconfigured.json())["code"] == "voice_not_configured"
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
            # Refresh keeps the authenticated session and its existing computer.
            await page.reload()
            await expect(page.locator("#session")).to_have_text(sid)
            await expect(page.locator("#expandRemote")).to_be_enabled()
            await page.locator("#take").click()
            await expect(page.locator("#controller")).to_have_text("human")
            await expect(page.locator("#epoch")).to_have_text("1")
            await api(prefix + "/navigate", "POST", {"url": TARGET, "epoch": 0}, 409)
            await api(prefix + "/input", "POST", {"kind": "key", "key": "Tab", "epoch": 1})
            await page.locator("#expandRemote").click()
            await expect(page.locator("#remoteOverlay")).to_be_visible()
            await page.set_viewport_size({"width": 390, "height": 844})
            await expect(page.locator("#fullScreen")).to_be_visible()
            await page.locator("#scroll").click()
            await page.screenshot(path=str(EVIDENCE / "ultraview-mobile-takeover.png"))
            second = await api(prefix + "/frame")
            assert int(second.headers["x-frame-sequence"]) > int(first.headers["x-frame-sequence"])
            assert int(second.headers["x-frame-timestamp"]) > int(first.headers["x-frame-timestamp"])
            assert second.headers["cache-control"] == "no-store"
            await page.screenshot(path=str(EVIDENCE / "ax-takeover.png"))
            await page.locator("#closeRemote").click()
            await page.locator("#return").click()
            await expect(page.locator("#controller")).to_have_text("agent")
            await expect(page.locator("#epoch")).to_have_text("2")
            await api(prefix + "/input", "POST", {"kind": "key", "key": "Tab", "epoch": 1}, 409)
            await api(prefix + "/navigate", "POST", {"url": TARGET, "epoch": 2})
            # Inject a frame transport failure: stale images and input must stop.
            frame_route = "**" + prefix + "/frame"
            await page.route(frame_route, lambda route: route.fulfill(status=503, body="unavailable"))
            await expect(page.locator("#reconnect")).to_be_visible()
            await expect(page.locator("#take")).to_be_disabled()
            await expect(page.locator("#screen")).to_be_hidden()
            await page.unroute(frame_route)
            await page.locator("#reconnect").click()
            await expect(page.locator("#take")).to_be_enabled()
            await page.locator("#close").click()
            await expect(page.locator("#session")).to_have_text("None")
            await api(prefix + "/frame", status=404)
            # Leave a session open to check honest expiry after Computer restarts.
            opened = await api("/api/sessions", "POST", {"url": TARGET})
            restart_sid = (await opened.json())["id"]
            # Separate authenticated context; only this additional UI scenario
            # uses synthetic media, voice-provider and graph responses.
            simulated_voice = await simulated_voice_ui(browser, await context.storage_state())
            evidence = {
                "status": "awaiting_restart_check", "source_sha": os.environ.get("SOURCE_SHA"),
                "workflow": history, "restart_session": restart_sid,
                "restart_workflow": await completed_history(restart_sid),
                "authenticated_ui": "passed", "live_frames": "passed",
                "takeover_handback": "passed", "stale_commands": "passed",
                "cleanup": "passed", "human_acceptance": "not_run",
                "approved_design": "UltraView AX v1", "responsive_layout": "passed",
                "reload_reconnect": "passed", "frame_failure_fail_closed": "passed",
                "voice_config_unavailable_ui": "passed", "voice_auth_and_csrf": "passed",
                "live_voice_audio": "not_run_no_provider_key",
                "simulated_voice_ui": simulated_voice,
                "target": TARGET,
            }
            (EVIDENCE / "smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print("PASS: real AX login, Core dispatch, Temporal history, browser frames and control")
        assert not page_errors, page_errors
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
