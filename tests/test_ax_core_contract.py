"""Local-only Core + AX smoke test, run after Python dependencies are installed.

Exercises the real FastAPI applications through their HTTP clients, with a
mocked Computer/Temporal boundary. It does not claim live Render acceptance.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from reign.ax import app as ax_app
from reign.core import app as core_app


class AXCoreContractTests(unittest.TestCase):
    def test_authenticated_ax_forwards_core_session_control(self):
        sid = str(uuid4())
        settings = {
            "CORE_TOKEN": "c" * 40,
            "AX_ADMIN_PASSWORD": "p" * 40,
            "AX_SESSION_KEY": "s" * 40,
            "AX_PUBLIC_ORIGIN": "https://ax.example.com",
            "CORE_URL": "https://core.example.com",
        }
        with patch.dict(os.environ, settings):
            with TestClient(core_app) as core, TestClient(
                ax_app, base_url="https://ax.example.com"
            ) as ax:
                async def forward(method, url, **kwargs):
                    from httpx import ASGITransport, AsyncClient
                    async with AsyncClient(
                        transport=ASGITransport(app=core_app),
                        base_url="https://core.example.com",
                    ) as inner:
                        return await inner.request(
                            method, url,
                            headers=kwargs.get("headers"),
                            json=kwargs.get("json"),
                        )

                with patch.object(ax_app.state.http, "request", side_effect=forward):
                    with patch("reign.core.worker", new_callable=AsyncMock) as worker:
                        worker.return_value.json.return_value = {
                            "id": sid, "mode": "human", "epoch": 1
                        }
                        login = ax.post("/login", json={"password": "p" * 40})
                        self.assertEqual(login.status_code, 200)
                        response = ax.post(
                            f"/api/sessions/{sid}/control",
                            headers={
                                "Origin": "https://ax.example.com",
                                "X-CSRF-Token": ax.cookies.get("ax_csrf"),
                            },
                            json={"mode": "human", "epoch": 0},
                        )
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["mode"], "human")
                        self.assertEqual(worker.await_args.args[1], f"/sessions/{sid}/control")


if __name__ == "__main__":
    unittest.main()
