import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from reign.core import app


class CoreTests(unittest.TestCase):
    def test_health_is_process_only(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})

    def test_private_endpoints_require_token(self):
        with patch.dict(os.environ, {"CORE_TOKEN": "x" * 40}):
            with TestClient(app) as client:
                for path in ("/api/readyz",):
                    with self.subTest(path=path):
                        self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.post("/api/sessions", json={}).status_code, 401)

    def test_disallowed_navigation_rejected_before_dispatch(self):
        with patch.dict(os.environ, {"CORE_TOKEN": "x" * 40}):
            with TestClient(app) as client:
                response = client.post("/api/sessions",
                    headers={"Authorization": "Bearer " + "x" * 40},
                    json={"url": "https://example.org"})
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
