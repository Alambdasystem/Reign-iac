"""Core/Temporal/Computer integration tests with mocked transport boundaries.

These tests verify request contracts; they are not a substitute for a live
Git High browser acceptance test on deployed Render services.
"""
import os
import unittest
from httpx import Response
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from reign.core import app


class CoreWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.token = "x" * 40
        self.headers = {"Authorization": "Bearer " + self.token}

    def test_open_browser_dispatches_temporal_workflow(self):
        sid = str(uuid4())
        with patch.dict(os.environ, {"CORE_TOKEN": self.token}):
            with TestClient(app) as client:
                with patch("reign.core.Client.connect", new_callable=AsyncMock) as connect:
                    connect.return_value.execute_workflow = AsyncMock(
                        return_value={"id": sid, "mode": "agent", "epoch": 0})
                    result = client.post("/api/sessions", headers=self.headers,
                        json={"url": "https://alambda-forge.onrender.com/"})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["id"], sid)
                    args = connect.return_value.execute_workflow.await_args
                    self.assertEqual(args.args[0], "OpenBrowser")
                    self.assertEqual(args.args[1]["url"], "https://alambda-forge.onrender.com/")
                    self.assertEqual(args.kwargs["task_queue"], "reign-computer")

    def test_control_and_frame_use_same_worker_session_id(self):
        sid = str(uuid4())
        with patch.dict(os.environ, {"CORE_TOKEN": self.token}):
            with TestClient(app) as client:
                with patch("reign.core.worker", new_callable=AsyncMock) as worker:
                    worker.side_effect = [
                        Response(200, json={"id": sid, "mode": "human", "epoch": 1}),
                        Response(200, content=b"jpeg-data"),
                    ]
                    result = client.post(f"/api/sessions/{sid}/control",
                        headers=self.headers, json={"mode": "human", "epoch": 0})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["epoch"], 1)
                    image = client.get(f"/api/sessions/{sid}/frame", headers=self.headers)
                    self.assertEqual(image.status_code, 200)
                    self.assertEqual(image.content, b"jpeg-data")
                    paths = [call.args[1] for call in worker.await_args_list]
                    self.assertEqual(paths, [f"/sessions/{sid}/control", f"/sessions/{sid}/frame"])


if __name__ == "__main__":
    unittest.main()
