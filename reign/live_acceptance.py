"""One-shot Render acceptance using existing AX runtime credentials.

Run explicitly during the approved frontend rollout, then remove the temporary
startup invocation. No credentials, cookies, page content or exception text are
logged. Creates and deletes only its own disposable browser session. This checks
the deployed API chain; real UI acceptance is separately exercised by CI and
human acceptance remains separate.
"""
import json
import os
import sys
import time
from urllib.parse import urlsplit

import httpx


def main(ci_local=False):
    base = os.environ["AX_PUBLIC_ORIGIN"].rstrip("/")
    parsed = urlsplit(base)
    test_origin = ci_local and base == "https://ax:10000"
    if not test_origin and (parsed.scheme != "https" or not parsed.hostname or
            not parsed.hostname.endswith(".onrender.com") or parsed.username or
            parsed.password or parsed.port not in (None, 443) or parsed.path or
            parsed.query or parsed.fragment):
        raise ValueError("Unexpected acceptance origin")
    sid = None
    tid = None
    stage = "wait_for_new_frontend"
    result = {"verification": "ultraview-live-api", "status": "failed",
              "human_acceptance": "not_run", "live_ui_browser": "not_run"}
    with httpx.Client(base_url=base, timeout=120, trust_env=False, verify=not test_origin,
                      follow_redirects=False) as client:
        try:
            # The public router may still serve the previous instance briefly.
            for _ in range(90):
                try:
                    page = client.get("/", timeout=5)
                    if page.status_code == 200 and 'data-design="ultraview-ax-v1-live"' in page.text:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(2)
            else:
                raise AssertionError("Updated frontend unavailable")
            stage = "dependencies_and_auth"
            assert client.get("/healthz").json()["status"] == "ok"
            assert client.get("/api/me").status_code == 401
            assert client.post("/api/sessions", json={}).status_code == 401
            ready = client.get("/api/readyz", headers={
                "Authorization": "Bearer " + os.environ["CORE_TOKEN"]})
            assert ready.status_code == 200
            assert ready.json()["components"] == {"core": "ready", "computer": "ready", "temporal": "ready"}
            assert client.post("/login", json={"password": os.environ["AX_ADMIN_PASSWORD"]}).status_code == 200
            session_cookie = next(c for c in client.cookies.jar if c.name == "ax_session")
            assert session_cookie.secure and "HttpOnly" in session_cookie._rest
            assert client.get("/api/me").json()["authenticated"] is True
            assert client.get("/api/status").status_code == 200
            assert client.post("/api/sessions", json={}).status_code == 403
            client.headers.update({"Origin": base, "X-CSRF-Token": client.cookies["ax_csrf"]})

            def call(path, method="GET", body=None, expected=200):
                response = client.request(method, path, json=body)
                assert response.status_code == expected
                return response

            stage = "open_and_frames"
            opened = call("/api/sessions", "POST", {"url": "https://alambda-forge.onrender.com/"}).json()
            sid = opened["id"]
            result["session_id"] = sid
            result["workflow_id"] = "reign-open-" + sid
            assert opened["url"].startswith("https://alambda-forge.onrender.com/")
            assert 200 <= opened["http_status"] < 400 and opened["title"]
            prefix = "/api/sessions/" + sid
            first = call(prefix + "/frame")
            assert len(first.content) > 100 and first.headers["x-session-id"] == sid
            stage = "takeover_and_stale_input"
            assert call(prefix + "/control", "POST", {"mode": "human", "epoch": 0}).json()["epoch"] == 1
            call(prefix + "/navigate", "POST", {"url": "https://alambda-forge.onrender.com/", "epoch": 0}, 409)
            call(prefix + "/input", "POST", {"kind": "scroll", "dy": 450, "epoch": 1})
            second = call(prefix + "/frame")
            assert int(second.headers["x-frame-sequence"]) > int(first.headers["x-frame-sequence"])
            assert int(second.headers["x-frame-timestamp"]) > int(first.headers["x-frame-timestamp"])
            assert second.headers["cache-control"] == "no-store"
            assert call(prefix + "/control", "POST", {"mode": "agent", "epoch": 1}).json()["epoch"] == 2
            call(prefix + "/input", "POST", {"kind": "key", "key": "Tab", "epoch": 1}, 409)
            call(prefix + "/navigate", "POST", {"url": "https://alambda-forge.onrender.com/", "epoch": 2})
            if not ci_local:
                stage = "openai_langgraph_tools"
                capability = call("/api/agent").json()
                assert capability["configured"] and capability["orchestration"] == "langgraph"
                result["model"] = capability["model"]
                run = call("/api/chat", "POST", {"message": "Call workspace_status, then browser_status. Report the actual results in one sentence.", "session_id": sid}).json()
                tid = run["thread_id"]

                def wait_run(expected="completed"):
                    for _ in range(150):
                        state = call("/api/chat/" + tid).json()
                        if state["status"] != "running":
                            assert state["status"] == expected
                            return state
                        time.sleep(2)
                    raise AssertionError("Agent timeout")

                state = wait_run()
                used = {e.get("tool") for e in state["events"] if e["kind"] == "tool_completed" and e.get("ok")}
                assert {"workspace_status", "browser_status"} <= used and state["answer"]
                result["agent_tools"] = sorted(used)
                result["agent_usage"] = state["usage"]
                stage = "langgraph_takeover_resume"
                call(prefix + "/control", "POST", {"mode": "human", "epoch": 2})
                call("/api/chat", "POST", {"message": "Use navigate_browser to navigate the current browser to https://alambda-forge.onrender.com/ now.", "session_id": sid, "thread_id": tid})
                wait_run("paused")
                call("/api/chat/" + tid + "/resume", "POST", expected=409)
                call(prefix + "/control", "POST", {"mode": "agent", "epoch": 3})
                call("/api/chat/" + tid + "/resume", "POST")
                resumed = wait_run()
                assert any(e.get("tool") == "navigate_browser" and e.get("ok") for e in resumed["events"])
                call("/api/chat/" + tid, "DELETE")
                tid = None
                result["live_llm"] = "passed"
                result["langgraph_pause_resume"] = "passed"
            stage = "cleanup"
            call(prefix, "DELETE")
            call(prefix + "/frame", expected=404)
            sid = None
            call("/logout", "POST")
            result.update(status="passed", checks=["public_frontend", "dependencies", "authentication",
                "csrf", "temporal_browser_open", "fresh_frames", "takeover", "handback", "stale_input", "cleanup"])
        except Exception:
            result["failed_stage"] = stage
        finally:
            if tid:
                try:
                    client.delete("/api/chat/" + tid)
                except Exception:
                    pass
            if sid:
                try:
                    cleanup = client.delete("/api/sessions/" + sid)
                    result["failure_cleanup"] = cleanup.status_code in (200, 404)
                except Exception:
                    result["failure_cleanup"] = False
            print("REIGN_ACCEPTANCE " + json.dumps(result), flush=True)
    return result["status"] == "passed"


if __name__ == "__main__":
    try:
        sys.exit(0 if main(ci_local=sys.argv[1:] == ["--ci-local"]) else 1)
    except Exception:
        print('REIGN_ACCEPTANCE {"status":"failed","failed_stage":"configuration"}', flush=True)
        sys.exit(1)
