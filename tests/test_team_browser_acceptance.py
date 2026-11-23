"""Synthetic HTTP checks for the live, non-mutating deployment gate."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
import unittest
from unittest.mock import patch

import httpx

from reign.team_browser_acceptance import (HEALTH_BYTES, PROBE_ROOM, PROBE_SESSION,
                                           main, run_acceptance)

TOKEN = "synthetic-computer-acceptance-secret-not-production"
ORIGIN = "http://private-computer.test:10000"
HEALTH = {"status": "ready", "worker": "reign-computer", "team_browser_protocol": 1}
ACK = {"X-Reign-Team-Browser-Protocol": "1"}


class BrowserProtocolAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, responses=None, *, handler=None):
        calls = []
        sequence = list(responses or [httpx.Response(200, json=HEALTH),
                                     httpx.Response(404, headers=ACK), httpx.Response(403, headers=ACK)])
        async def dispatch(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.headers["authorization"], "Bearer " + TOKEN)
            if handler:
                return await handler(request)
            return sequence.pop(0)
        with patch.dict(os.environ, {"COMPUTER_TOKEN": TOKEN, "COMPUTER_URL": ORIGIN}):
            receipt = await run_acceptance(transport=httpx.MockTransport(dispatch))
        encoded = json.dumps(receipt)
        self.assertNotIn(TOKEN, encoded); self.assertNotIn(ORIGIN, encoded)
        return receipt, calls

    async def test_scoped_worker_passes_exactly_three_read_only_probes(self):
        receipt, calls = await self.probe()
        self.assertEqual(receipt["status"], "passed"); self.assertEqual(receipt["stage"], "complete")
        self.assertEqual(receipt["browser_mutations"], 0)
        path = f"/team-sessions/{PROBE_ROOM}/{PROBE_SESSION}"
        self.assertEqual([call.url.path for call in calls], ["/healthz", path, path])
        self.assertNotIn("X-Reign-Browser-Scope", calls[0].headers)
        self.assertEqual(calls[1].headers["X-Reign-Browser-Scope"], "team:rain:" + PROBE_ROOM)
        self.assertEqual(calls[2].headers["X-Reign-Browser-Scope"], "owner")
        self.assertTrue(all(not call.content for call in calls))
        self.assertEqual([probe["http_status"] for probe in receipt["probes"]], [200, 404, 403])
        self.assertTrue(all(probe["protocol"] == 1 for probe in receipt["probes"]))

    async def test_legacy_missing_mismatched_or_boolean_health_protocol_stops_before_scope_probes(self):
        for protocol in (None, 0, 2, "1", True):
            health = {**HEALTH, "team_browser_protocol": protocol, "private": TOKEN}
            with self.subTest(protocol=protocol):
                receipt, calls = await self.probe([httpx.Response(200, json=health)])
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["error_code"], "team_browser_protocol_mismatch")
                self.assertEqual(len(calls), 1)

    async def test_health_requires_ready_computer_and_bounded_valid_json(self):
        for response in (httpx.Response(401, text=TOKEN), httpx.Response(503, json=HEALTH),
                         httpx.Response(200, json={**HEALTH, "status": "starting"}),
                         httpx.Response(200, json={**HEALTH, "worker": "unrelated"}),
                         httpx.Response(200, text=TOKEN), httpx.Response(200, text="x" * (HEALTH_BYTES + 1))):
            receipt, calls = await self.probe([response])
            self.assertEqual(receipt["status"], "failed"); self.assertEqual(len(calls), 1)
            self.assertEqual(receipt["error_code"], "team_browser_health_failed")

    async def test_scope_probe_requires_acknowledged404_and_denied_owner_requires_acknowledged403(self):
        for stage, expected in ((1, 404), (2, 403)):
            for status, headers in ((200, ACK), (401, ACK), (503, ACK), (expected, {}),
                                    (expected, {"X-Reign-Team-Browser-Protocol": TOKEN}),
                                    (expected, {"X-Reign-Team-Browser-Protocol": "2"})):
                with self.subTest(stage=stage, status=status, ack=bool(headers)):
                    responses = [httpx.Response(200, json=HEALTH)]
                    if stage == 2: responses.append(httpx.Response(404, headers=ACK))
                    responses.append(httpx.Response(status, headers=headers, text=TOKEN))
                    receipt, calls = await self.probe(responses)
                    self.assertEqual(receipt["status"], "failed"); self.assertEqual(len(calls), stage + 1)
                    self.assertIn(receipt["error_code"], {"team_browser_scope_probe_failed", "team_browser_protocol_mismatch"})

    async def test_redirect_is_not_followed_and_receipt_does_not_include_location(self):
        target = "https://unrelated.test/private/" + TOKEN
        receipt, calls = await self.probe([httpx.Response(302, headers={"Location": target})])
        self.assertEqual(receipt["status"], "failed"); self.assertEqual(len(calls), 1)
        self.assertNotIn(target, json.dumps(receipt))

    async def test_transport_errors_and_deadlines_emit_only_closed_receipt(self):
        async def bad_transport(request):
            raise httpx.ConnectError("private diagnostic " + TOKEN + " " + ORIGIN)
        receipt, calls = await self.probe(handler=bad_transport)
        self.assertEqual(receipt["error_code"], "team_browser_protocol_probe_failed")
        self.assertEqual(len(calls), 1)
        cancelled = asyncio.Event()
        async def blocked(request):
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        with patch("reign.team_browser_acceptance.PROBE_SECONDS", .01):
            receipt, calls = await self.probe(handler=blocked)
        self.assertEqual(receipt["status"], "failed"); self.assertTrue(cancelled.is_set())
        self.assertEqual(receipt["error_code"], "team_browser_protocol_probe_failed")

    async def test_unconfigured_credential_prevents_any_network_request(self):
        calls = []
        def handler(request): calls.append(request); return httpx.Response(200, json=HEALTH)
        with patch.dict(os.environ, {"COMPUTER_TOKEN": "short", "COMPUTER_URL": ORIGIN}):
            receipt = await run_acceptance(transport=httpx.MockTransport(handler))
        self.assertEqual(receipt["status"], "failed"); self.assertEqual(receipt["stage"], "configuration")
        self.assertEqual(calls, [])


class BrowserProtocolCLITests(unittest.TestCase):
    def test_cli_prints_one_sanitized_receipt_and_exits_nonzero_on_failure(self):
        for status, expected in (("passed", 0), ("failed", 1)):
            async def result(): return {"verification": "reign_team_browser_protocol", "status": status}
            output = io.StringIO()
            with patch("reign.team_browser_acceptance.run_acceptance", result), \
                 patch("reign.team_browser_acceptance.logging.disable"), redirect_stdout(output):
                self.assertEqual(main(), expected)
            self.assertEqual(json.loads(output.getvalue())["status"], status)
            self.assertEqual(len(output.getvalue().splitlines()), 1)


if __name__ == "__main__": unittest.main()
