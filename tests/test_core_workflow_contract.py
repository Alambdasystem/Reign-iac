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
from temporalio.exceptions import ApplicationError
from reign.core import app
from reign.session_errors import BROWSER_OCCUPIED_CODE, BROWSER_OCCUPIED_MESSAGE


class WrappedFailure(Exception):
    def __init__(self, cause):
        super().__init__("private transport failure")
        self.cause = cause


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

    def test_active_session_discovery_is_authenticated_and_uses_static_route(self):
        session = {"id": str(uuid4()), "mode": "human", "epoch": 3,
                   "title": "Git High", "url": "https://alambda-forge.onrender.com/"}
        with patch.dict(os.environ, {"CORE_TOKEN": self.token}), TestClient(app) as client:
            with patch("reign.core.worker", new_callable=AsyncMock) as worker:
                self.assertEqual(client.get("/api/sessions/active").status_code, 401)
                worker.assert_not_awaited()
                for current in (None, session):
                    worker.return_value = Response(200, json={"session": current})
                    result = client.get("/api/sessions/active", headers=self.headers)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json(), {"session": current})
                    self.assertEqual(result.headers["cache-control"], "no-store")
                    worker.assert_awaited_with("GET", "/sessions/active")

    def test_occupied_workflow_remains_sanitized_409_through_nested_failures(self):
        occupied = ApplicationError("private worker exception body", type="WorkerOccupied", non_retryable=True)
        with patch.dict(os.environ, {"CORE_TOKEN": self.token}), TestClient(app) as client:
            with patch("reign.core.Client.connect", new_callable=AsyncMock) as connect:
                execute = AsyncMock(side_effect=WrappedFailure(WrappedFailure(occupied)))
                connect.return_value.execute_workflow = execute
                result = client.post("/api/sessions", headers=self.headers, json={})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json(), {"code": BROWSER_OCCUPIED_CODE, "detail": BROWSER_OCCUPIED_MESSAGE})
                self.assertNotIn("private", result.text)
                self.assertEqual(execute.await_count, 1)
                execute.side_effect = RuntimeError("private provider token")
                result = client.post("/api/sessions", headers=self.headers, json={})
                self.assertEqual(result.status_code, 503)
                self.assertNotIn("private", result.text)


if __name__ == "__main__":
    unittest.main()
