"""Local-only Core + AX smoke test, run after Python dependencies are installed.

Exercises the real FastAPI applications through their HTTP clients, with a
mocked Computer/Temporal boundary. It does not claim live Render acceptance.
"""
import os
import unittest
from httpx import Response
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError
from reign.ax import app as ax_app
from reign.core import app as core_app
from reign.session_errors import BROWSER_OCCUPIED_CODE, BROWSER_OCCUPIED_MESSAGE


class AXCoreContractTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"CORE_TOKEN": "c" * 40, "AX_ADMIN_PASSWORD": "p" * 40,
                         "AX_SESSION_KEY": "s" * 40, "AX_PUBLIC_ORIGIN": "https://ax.example.com",
                         "CORE_URL": "https://core.example.com"}

    async def forward(self, method, url, **kwargs):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=core_app), base_url="https://core.example.com") as inner:
            return await inner.request(method, url, headers=kwargs.get("headers"), json=kwargs.get("json"))

    def test_discovery_and_occupied_error_survive_authenticated_ax_core_chain(self):
        session = {"id": str(uuid4()), "url": "https://alambda-forge.onrender.com/",
                   "title": "Git High", "mode": "human", "epoch": 5}
        with patch.dict(os.environ, self.settings), TestClient(core_app), TestClient(ax_app, base_url="https://ax.example.com") as ax:
            with patch.object(ax_app.state.http, "request", side_effect=self.forward), patch("reign.core.worker", new_callable=AsyncMock) as worker:
                self.assertEqual(ax.get("/api/sessions/active").status_code, 401)
                worker.assert_not_awaited()
                self.assertEqual(ax.post("/login", json={"password": self.settings["AX_ADMIN_PASSWORD"]}).status_code, 200)
                for current in (None, session):
                    worker.return_value = Response(200, json={"session": current})
                    response = ax.get("/api/sessions/active")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"session": current})
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    worker.assert_awaited_with("GET", "/sessions/active")
                with patch("reign.core.Client.connect", new_callable=AsyncMock) as connect:
                    result = AsyncMock(side_effect=ApplicationError(
                        "private worker error", type="WorkerOccupied", non_retryable=True))
                    connect.return_value.start_workflow = AsyncMock(return_value=Mock(result=result))
                    response = ax.post("/api/sessions", json={}, headers={"Origin": self.settings["AX_PUBLIC_ORIGIN"],
                        "X-CSRF-Token": ax.cookies.get("ax_csrf")})
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json(), {"code": BROWSER_OCCUPIED_CODE, "detail": BROWSER_OCCUPIED_MESSAGE})
                    self.assertNotIn("private", response.text)
                    connect.return_value.start_workflow.assert_awaited_once()
                    result.assert_awaited_once()

    def test_occupied_proxy_uses_only_allowlisted_code_not_downstream_body(self):
        with patch.dict(os.environ, self.settings), TestClient(ax_app, base_url="https://ax.example.com") as ax:
            ax.post("/login", json={"password": self.settings["AX_ADMIN_PASSWORD"]})
            headers = {"Origin": self.settings["AX_PUBLIC_ORIGIN"], "X-CSRF-Token": ax.cookies.get("ax_csrf")}
            for code in (BROWSER_OCCUPIED_CODE, "private-code", None, []):
                with self.subTest(code=code), patch.object(ax_app.state.http, "request", new_callable=AsyncMock) as proxy:
                    proxy.return_value = Response(409, json={"code": code, "detail": "private secret body"})
                    response = ax.post("/api/sessions", headers=headers, json={})
                    self.assertEqual(response.status_code, 409)
                    self.assertNotIn("private", response.text)
                    self.assertEqual(response.json().get("code"), BROWSER_OCCUPIED_CODE if code == BROWSER_OCCUPIED_CODE else None)

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
                        worker.return_value = Response(200, json={
                            "id": sid, "mode": "human", "epoch": 1
                        })
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
