"""Control ownership regression checks with a lightweight fake browser page."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException
from reign.computer import BrowserSessions


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
