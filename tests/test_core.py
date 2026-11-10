import os
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from fastapi.testclient import TestClient
from reign.core import app


class CoreTests(unittest.TestCase):
    def test_temporal_control_rejection_stays_a_conflict(self):
        from reign.core import workflow_http_error
        from temporalio.exceptions import ApplicationError
        rejected = ApplicationError("Control changed", type="ControlChanged", non_retryable=True)
        self.assertEqual(workflow_http_error(rejected).status_code, 409)
        self.assertEqual(workflow_http_error(ConnectionError()).status_code, 503)

    def test_health_is_process_only(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})

    def test_private_endpoints_require_token(self):
        with patch.dict(os.environ, {"CORE_TOKEN": "x" * 40}):
            with TestClient(app) as client:
                for path in ("/api/readyz", "/api/agent", "/api/chat/00000000-0000-0000-0000-000000000000"):
                    with self.subTest(path=path):
                        self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.post("/api/sessions", json={}).status_code, 401)
                self.assertEqual(client.post("/api/chat", json={"message": "hi"}).status_code, 401)

    def test_disallowed_navigation_rejected_before_dispatch(self):
        with patch.dict(os.environ, {"CORE_TOKEN": "x" * 40}):
            with TestClient(app) as client:
                response = client.post("/api/sessions",
                    headers={"Authorization": "Bearer " + "x" * 40},
                    json={"url": "https://example.org"})
                self.assertEqual(response.status_code, 422)

    def test_database_readiness_is_authenticated_and_fails_closed(self):
        token = "x" * 40
        headers = {"Authorization": "Bearer " + token}
        with patch.dict(os.environ, {"CORE_TOKEN": token}):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/database/readyz").status_code, 401)
                with patch("reign.database.probe_reign_database", new_callable=AsyncMock) as probe:
                    probe.return_value = True
                    self.assertEqual(client.get("/api/database/readyz", headers=headers).json(),
                                     {"status": "ready", "schema": "reign"})
                    probe.return_value = False
                    self.assertEqual(client.get("/api/database/readyz", headers=headers).status_code, 503)
                    probe.side_effect = ConnectionError("private database unavailable")
                    self.assertEqual(client.get("/api/database/readyz", headers=headers).status_code, 503)

    def test_history_readiness_is_private_and_fail_closed(self):
        token = "x" * 40
        headers = {"Authorization": "Bearer " + token}
        with patch.dict(os.environ, {"CORE_TOKEN": token, "REIGN_HISTORY_MODE": "disabled"}):
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/history/readyz").status_code, 401)
                # Must not claim durable storage just because Core is healthy.
                self.assertEqual(client.get("/api/history/readyz", headers=headers).status_code, 503)
                with patch("reign.history_readiness.history_ready", new_callable=AsyncMock) as probe:
                    probe.return_value = True
                    self.assertEqual(client.get("/api/history/readyz", headers=headers).json(),
                                     {"status": "preflight_passed", "storage": "encrypted_postgres",
                                      "activation": "disabled"})
                    probe.return_value = False
                    self.assertEqual(client.get("/api/history/readyz", headers=headers).status_code, 503)

    def test_ready_requires_both_temporal_and_computer(self):
        token = "x" * 40
        headers = {"Authorization": "Bearer " + token}
        with patch.dict(os.environ, {"CORE_TOKEN": token}):
            with TestClient(app) as client:
                with patch("reign.core.Client.connect", new_callable=AsyncMock) as connect, \
                     patch("reign.core.worker", new_callable=AsyncMock) as worker:
                    connect.return_value.workflow_service.get_system_info = AsyncMock(return_value={})
                    worker.return_value.status_code = 200
                    self.assertEqual(client.get("/api/readyz", headers=headers).status_code, 200)
                    worker.side_effect = HTTPException(503, "Worker unavailable")
                    self.assertEqual(client.get("/api/readyz", headers=headers).status_code, 503)
                    worker.side_effect = None
                    connect.side_effect = ConnectionError("Temporal unavailable")
                    self.assertEqual(client.get("/api/readyz", headers=headers).status_code, 503)


if __name__ == "__main__":
    unittest.main()
