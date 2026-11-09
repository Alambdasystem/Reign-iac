"""Control ownership regression checks with a lightweight fake browser page."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from temporalio.exceptions import ApplicationError
from reign.computer import BrowserSessions, app, open_browser


class ControlTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = BrowserSessions()
        self.page = AsyncMock()
        self.page.title.return_value = "Git High"
        self.page.url = "https://alambda-forge.onrender.com/"
        self.manager.sessions["s1"] = {
            "mode": "agent", "epoch": 0, "page": self.page,
            "http_status": 200, "last_access": 0,
        }

    async def test_active_discovery_preserves_browser_page_and_human_ownership(self):
        context = AsyncMock()
        self.manager.browser = AsyncMock()
        self.manager.sessions["s1"]["context"] = context
        await self.manager.control("s1", "human", 0)
        result = await self.manager.active()
        self.assertEqual(result, {"session": {
            "id": "s1", "mode": "human", "epoch": 1,
            "title": "Git High", "url": self.page.url, "http_status": 200,
            "viewport": {"width": 1280, "height": 800},
        }})
        self.assertEqual(len(self.manager.sessions), 1)
        self.assertIs(self.manager.sessions["s1"]["page"], self.page)
        self.assertEqual(self.manager.sessions["s1"]["mode"], "human")
        self.assertEqual(self.manager.sessions["s1"]["epoch"], 1)
        self.manager.browser.new_context.assert_not_awaited()
        self.page.goto.assert_not_awaited()
        context.close.assert_not_awaited()

    async def test_active_discovery_does_not_launch_when_no_session_exists(self):
        self.manager.sessions.clear()
        self.manager.launch = AsyncMock()
        self.assertEqual(await self.manager.active(), {"session": None})
        self.manager.launch.assert_not_awaited()
        self.assertFalse(self.manager.sessions)

    async def test_active_endpoint_requires_worker_auth_and_precedes_sid_route(self):
        token = "test-only-worker-token-" + "x" * 32
        with patch("reign.computer.manager", self.manager), patch.object(app.state, "token", token, create=True):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker.test") as client:
                self.assertEqual((await client.get("/sessions/active")).status_code, 401)
                result = await client.get("/sessions/active", headers={"Authorization": "Bearer " + token})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["session"]["id"], "s1")
                self.assertNotIn(token, result.text)
                self.manager.sessions.clear()
                self.assertEqual((await client.get("/sessions/active", headers={"Authorization": "Bearer " + token})).json(), {"session": None})

    async def test_occupied_open_is_typed_nonretryable_and_keeps_existing_context(self):
        self.manager.browser = AsyncMock()
        with patch("reign.computer.manager", self.manager), patch.dict(os.environ, {"MAX_SESSIONS": "1"}):
            with self.assertRaises(ApplicationError) as error:
                await open_browser({"id": "second-session", "url": self.page.url})
        self.assertEqual(error.exception.type, "WorkerOccupied")
        self.assertTrue(error.exception.non_retryable)
        self.assertEqual(list(self.manager.sessions), ["s1"])
        self.manager.browser.new_context.assert_not_awaited()
        self.page.goto.assert_not_awaited()

    async def test_takeover_handback_and_stale_epoch(self):
        result = await self.manager.control("s1", "human", 0)
        self.assertEqual((result["mode"], result["epoch"]), ("human", 1))
        with self.assertRaises(HTTPException) as stale:
            await self.manager.control("s1", "agent", 0)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as duplicate:
            await self.manager.control("s1", "human", 1)
        self.assertEqual(duplicate.exception.status_code, 409)
        result = await self.manager.control("s1", "agent", 1)
        self.assertEqual((result["mode"], result["epoch"]), ("agent", 2))

    async def test_old_human_input_rejected_after_handback(self):
        from reign.computer import Input
        await self.manager.control("s1", "human", 0)
        await self.manager.control("s1", "agent", 1)
        with self.assertRaises(HTTPException) as stale:
            await self.manager.input("s1", Input(kind="click", epoch=1, x=10, y=10))
        self.assertEqual(stale.exception.status_code, 409)
        self.page.mouse.click.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
