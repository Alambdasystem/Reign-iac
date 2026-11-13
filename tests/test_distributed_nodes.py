"""Distributed node inventory and routing-preview regression tests.

No remote machine access, real dispatch, AWS/Azure provisioning or provider calls.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from reign.core import app as core_app
from reign.distributed_nodes import NodeRegistry, NodeDenied


class NodeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "nodes.sqlite")
        self.now = 1000
        self.registry = NodeRegistry(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_enrollment_heartbeat_and_restart_durability(self):
        receipt = self.registry.enroll("render-browser", "render", "Browser worker",
                                       ["browser.observe", "browser.action"])
        self.assertEqual(len(receipt["enrollment_token"]), 64)
        self.assertFalse(receipt["dispatch_enabled"])
        self.assertEqual(self.registry.list_nodes()[0]["status"], "offline")
        with open(self.path, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(receipt["enrollment_token"].encode(), raw)
        self.registry.heartbeat("render-browser", receipt["enrollment_token"])
        recovered = NodeRegistry(self.path, clock=lambda: self.now)
        self.assertEqual(recovered.list_nodes()[0]["status"], "online")
        preview = recovered.route_preview("browser.action")
        self.assertEqual(preview["candidate_nodes"], ["render-browser"])
        self.assertFalse(preview["dispatch_enabled"])
        self.assertEqual(os.stat(self.path).st_mode & 0o077, 0)

    def test_stale_and_disabled_nodes_never_selected(self):
        token = self.registry.enroll("gpu-east", "onprem", "GPU", ["model.inference"])
        self.registry.heartbeat("gpu-east", token["enrollment_token"])
        self.now += 91
        self.assertEqual(self.registry.route_preview("model.inference")["eligible_count"], 0)
        self.registry.disable("gpu-east")
        self.assertEqual(self.registry.list_nodes()[0]["status"], "disabled")
        with self.assertRaises(NodeDenied):
            self.registry.heartbeat("gpu-east", token["enrollment_token"])

    def test_wrong_node_or_token_rejected_without_side_effect(self):
        token = self.registry.enroll("edge-a", "edge", "Edge", ["workflow.execute"])
        with self.assertRaises(NodeDenied):
            self.registry.heartbeat("edge-a", "0" * 64)
        with self.assertRaises(NodeDenied):
            self.registry.heartbeat("edge-b", token["enrollment_token"])
        with self.assertRaises(NodeDenied):
            self.registry.heartbeat("edge-a", "")
        self.assertEqual(self.registry.list_nodes()[0]["status"], "offline")

    def test_capability_and_duplicate_rejection(self):
        with self.assertRaises(ValueError):
            self.registry.enroll("bad name", "render", "Bad", [])
        with self.assertRaises(ValueError):
            self.registry.enroll("bad-kind", "unapproved", "Bad", [])
        with self.assertRaises(ValueError):
            self.registry.enroll("bad-cap", "render", "Bad", ["shell.exec"])
        with self.assertRaises(ValueError):
            self.registry.enroll("bad-dup", "render", "Bad", ["browser.observe", "browser.observe"])
        self.registry.enroll("computer", "render", "Computer", ["browser.observe"])
        with self.assertRaises(NodeDenied):
            self.registry.enroll("computer", "render", "Duplicate", ["browser.action"])
        with self.assertRaises(ValueError):
            self.registry.route_preview("shell.exec")

    def test_symlink_and_hardlink_rejected(self):
        other = Path(self.temp.name) / "existing"
        other.write_text("fixture")
        symlink = Path(self.temp.name) / "linked"
        symlink.symlink_to(other)
        with self.assertRaises(RuntimeError):
            NodeRegistry(str(symlink))
        hardlink = Path(self.temp.name) / "hardlinked"
        os.link(other, hardlink)
        with self.assertRaises(RuntimeError):
            NodeRegistry(str(hardlink))

    def test_activation_requires_verified_mount(self):
        with patch.dict(os.environ, {
            "REIGN_NODES_MODE": "inventory",
            "REIGN_VAULT_DIR": self.temp.name,
        }):
            with self.assertRaises(RuntimeError):
                NodeRegistry.from_env()


class NodeCoreAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_only_enrollment_node_token_heartbeat_and_no_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            registry = NodeRegistry(str(Path(root) / "nodes.sqlite"), clock=lambda: 1000)
            core_app.state.nodes = registry
            try:
                transport = httpx.ASGITransport(app=core_app)
                async with httpx.AsyncClient(transport=transport, base_url="http://core.test") as client:
                    self.assertEqual((await client.get("/api/nodes")).status_code, 401)
                    self.assertEqual((await client.post("/api/nodes/enroll",json={
                        "node_id": "worker", "kind": "render", "label": "Worker",
                        "capabilities": ["browser.observe"],
                    })).status_code, 401)
                    with patch.dict(os.environ, {"CORE_TOKEN": "x" * 40}):
                        admin = {"Authorization": "Bearer " + "x" * 40}
                        payload = {"node_id": "worker", "kind": "render",
                                   "label": "Worker", "capabilities": ["browser.observe"]}
                        enrolled = await client.post("/api/nodes/enroll", headers=admin, json=payload)
                        self.assertEqual(enrolled.status_code, 200)
                        secret = enrolled.json()["enrollment_token"]
                        before = await client.get("/api/nodes", headers=admin)
                        self.assertNotIn(secret, before.text)
                        self.assertEqual(before.json()["nodes"][0]["status"], "offline")
                        rejected = await client.post("/api/nodes/worker/heartbeat",
                                                     headers={"x-node-token": "a" * 64})
                        self.assertEqual(rejected.status_code, 401)
                        heartbeat = await client.post("/api/nodes/worker/heartbeat",
                                                     headers={"x-node-token": secret})
                        self.assertEqual(heartbeat.status_code, 200)
                        self.assertFalse(heartbeat.json()["dispatch_enabled"])
                        preview = await client.get(
                            "/api/nodes/route-preview/browser.observe", headers=admin)
                        self.assertEqual(preview.json()["candidate_nodes"], ["worker"])
                        self.assertFalse(preview.json()["dispatch_enabled"])
                        denied = await client.post("/api/nodes/worker/disable")
                        self.assertEqual(denied.status_code, 401)
                        disabled = await client.post("/api/nodes/worker/disable", headers=admin)
                        self.assertEqual(disabled.status_code, 200)
                        self.assertEqual((await client.post(
                            "/api/nodes/worker/heartbeat",
                            headers={"x-node-token": secret})).status_code, 401)
            finally:
                del core_app.state.nodes
