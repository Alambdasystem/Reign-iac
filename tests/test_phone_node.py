"""Exercise phone check-ins across both APIs using the existing node credentials."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from reign.ax import app as ax
from reign.core import app as core
from reign.distributed_nodes import NodeRegistry


class PhoneNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_and_revoke_across_ax_and_core(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "nodes.db")
            registry = NodeRegistry(path)
            receipt = registry.enroll("my-phone", "edge", "My phone", ["model.inference"])
            token = receipt["enrollment_token"]
            app = {"app_id": "systems.alambda.reignnode", "name": "REIGN Node", "version": "0.1.0",
                   "model": "SmolLM2-360M-Instruct-Q4_K_M", "runtime": "llama.cpp / wllama 2.3.7"}
            path_url = "/api/nodes/my-phone/heartbeat"
            transport = httpx.ASGITransport(app=core)

            async def upstream(request):
                self.assertNotIn("Authorization", request.headers)
                self.assertNotIn("Cookie", request.headers)
                self.assertEqual(request.url.path, path_url)
                return await transport.handle_async_request(request)

            async with (httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as internal,
                        httpx.AsyncClient(transport=httpx.ASGITransport(app=ax), base_url="https://ax.test") as client):
                with (patch.object(ax.state, "http", internal, create=True),
                      patch.object(core.state, "nodes", registry, create=True),
                      patch.dict(os.environ, {"CORE_URL": "https://core.test"})):
                    headers = {"x-node-token": token}
                    for bad in [{"apps": None}, {"apps": [app, app]}, {"apps": [{**app, "bad": 0}]}]:
                        self.assertEqual((await client.post(path_url, json=bad, headers=headers)).status_code, 422)
                    self.assertEqual((await client.post(path_url, content=b"x" * 8200, headers=headers)).status_code, 422)
                    self.assertEqual((await client.post(path_url, json={"apps": [app]})).status_code, 401)
                    self.assertEqual((await client.post(path_url, json={"apps": [app]},
                                         headers={"x-node-token": "0" * 64})).status_code, 401)
                    self.assertIsNone(registry.list_nodes()[0]["last_seen"])
                    result = await client.post(path_url, json={"apps": [app]}, headers=headers)
                    self.assertEqual(result.status_code, 200)
                    self.assertTrue(result.json()["inventory_updated"])
                    self.assertFalse(result.json()["dispatch_enabled"])
                    node = NodeRegistry(path).list_nodes()[0]
                    self.assertEqual(node["apps"], [app])
                    self.assertEqual(node["status"], "online")
                    self.assertIsInstance(node["apps_reported_at"], int)
                    self.assertFalse((await client.post(path_url, headers=headers)).json()["inventory_updated"])
                    self.assertEqual(registry.list_nodes()[0]["apps"], [app])
                    self.assertEqual((await client.post(path_url, json={"apps": []}, headers=headers)).status_code, 200)
                    self.assertEqual(registry.list_nodes()[0]["apps"], [])
                    registry.disable("my-phone")
                    self.assertEqual((await client.post(path_url, json={"apps": [app]}, headers=headers)).status_code, 401)
                    self.assertEqual(registry.list_nodes()[0]["apps"], [])
