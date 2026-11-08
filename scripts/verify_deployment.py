"""Verify every expected Render image, then the application's dependency health.

Passing this script is deployment verification, not browser/human-takeover E2E.
Never print response bodies: they may contain private operational information.
"""
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from pathlib import Path

OWNER = "tea-dbdccfpsrm7s73b8rugg"


def get(url, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    # Do not forward credentials across redirects, including same-host redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def verify(services, release):
    if set(services) != {"core", "temporal", "computer", "ax"} or not release.get("provision"):
        raise ValueError("No complete four-service release is enabled")
    api_token = os.environ["RENDER_API_KEY"]
    for name, expected in services.items():
        sid = expected["id"]
        if not re.fullmatch(r"srv-[a-z0-9]+", sid):
            raise ValueError("Unexpected service identifier")
        service = get(f"https://api.render.com/v1/services/{sid}", api_token)
        if service.get("name") != f"reign-{name}-pilot" or service.get("ownerId") != OWNER:
            raise ValueError("Service name or owner mismatch")
        deployments = get(f"https://api.render.com/v1/services/{sid}/deploys?limit=1", api_token)
        latest = deployments[0].get("deploy", deployments[0])
        if latest.get("status") != "live" or latest.get("image", {}).get("sha") != release["images"][name]["digest"]:
            raise ValueError("Latest deployment is not live at the expected image digest")
        print(f"PASS {name}: {sid}, deployment {latest['id']}, expected immutable image is live")
    ax_url = services["ax"]["address"].rstrip("/")
    parsed = urlsplit(ax_url)
    if parsed.scheme != "https" or not parsed.hostname.endswith(".onrender.com") or parsed.username or parsed.password:
        raise ValueError("Unexpected AX verification URL")
    if get(ax_url + "/healthz").get("status") != "ok":
        raise ValueError("AX HTTP health failed")
    core_token = json.loads(os.environ["TF_VAR_runtime_secrets"])["core_token"]
    ready = get(ax_url + "/api/readyz", core_token)
    if ready.get("status") != "ready" or any(ready.get("components", {}).get(name) != "ready" for name in ("core", "computer", "temporal")):
        raise ValueError("Live dependency readiness failed")
    print("PASS AX health and authenticated Core/Computer/Temporal dependency readiness")
    print("Browser opening, live frames, human takeover and handback still require the E2E acceptance test.")


if __name__ == "__main__":
    services = json.loads(Path(sys.argv[1]).read_text())
    release = json.loads(Path("deploy/release.json").read_text())
    for attempt in range(12):
        try:
            verify(services, release)
            break
        except Exception:
            if attempt == 11:
                sys.exit("Deployment verification FAILED; do not report this release as verified. Inspect Render and app readiness privately.")
            time.sleep(10)
