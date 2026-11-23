"""Read-only live worker-protocol gate for trusted Core pre-deploy.

Uses the existing private Computer credential in process. No browser allocation,
input, navigation, observation, frame, close, archive write or Temporal dispatch
occurs. Receipts contain only fixed labels, HTTP statuses and protocol results.
"""
import asyncio
import json
import logging
import sys

import httpx

from reign.config import computer_url, secret

PROTOCOL = 1
PROBE_ROOM = "00000000-0000-4000-8000-000000000001"
# Real browser session IDs are server-generated UUIDs. This cannot select one.
PROBE_SESSION = "rollout-probe"
PROBE_SECONDS = 35
HEALTH_BYTES = 4096


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code


async def run_acceptance(*, transport=None):
    receipt = {"verification": "reign_team_browser_protocol", "status": "failed",
               "stage": "configuration", "protocol_required": PROTOCOL,
               "browser_mutations": 0, "probes": []}
    try:
        # Never include the credential or the configured private URL in output.
        origin = computer_url().rstrip("/")
        authorization = "Bearer " + secret("COMPUTER_TOKEN")
        async with asyncio.timeout(PROBE_SECONDS):
            async with httpx.AsyncClient(transport=transport, trust_env=False,
                                         follow_redirects=False, timeout=10) as http:
                receipt["stage"] = "health"
                async with http.stream("GET", origin + "/healthz",
                                       headers={"Authorization": authorization}) as response:
                    observed = {"name": "health", "http_status": response.status_code, "protocol": None}
                    receipt["probes"].append(observed)
                    if response.status_code != 200:
                        raise ProbeFailure("team_browser_health_failed")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > HEALTH_BYTES:
                            raise ProbeFailure("team_browser_health_failed")
                        content.extend(chunk)
                    try:
                        value = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise ProbeFailure("team_browser_health_failed") from None
                    if (not isinstance(value, dict) or value.get("status") != "ready"
                            or value.get("worker") != "reign-computer"):
                        raise ProbeFailure("team_browser_health_failed")
                    protocol = value.get("team_browser_protocol")
                    if type(protocol) is not int or protocol != PROTOCOL:
                        raise ProbeFailure("team_browser_protocol_mismatch")
                    observed["protocol"] = PROTOCOL

                path = f"/team-sessions/{PROBE_ROOM}/{PROBE_SESSION}"
                for name, scope, expected in (
                    ("scoped_missing_session", "team:rain:" + PROBE_ROOM, 404),
                    ("owner_scope_rejected", "owner", 403),
                ):
                    receipt["stage"] = name
                    async with http.stream("GET", origin + path,
                        headers={"Authorization": authorization, "X-Reign-Browser-Scope": scope}) as response:
                        acknowledged = response.headers.get("X-Reign-Team-Browser-Protocol") == str(PROTOCOL)
                        receipt["probes"].append({"name": name, "http_status": response.status_code,
                                                  "protocol": PROTOCOL if acknowledged else None})
                        # Do not parse or emit error bodies: only the known
                        # status and acknowledgement establish this boundary.
                        if not acknowledged:
                            raise ProbeFailure("team_browser_protocol_mismatch")
                        if response.status_code != expected:
                            raise ProbeFailure("team_browser_scope_probe_failed")
                receipt.update(status="passed", stage="complete")
    except asyncio.CancelledError:
        raise
    except ProbeFailure as error:
        receipt["error_code"] = error.code
    except Exception:
        receipt["error_code"] = "team_browser_protocol_probe_failed"
    return receipt


def main():
    logging.disable(logging.CRITICAL)
    receipt = asyncio.run(run_acceptance())
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
