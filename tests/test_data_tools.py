import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from reign.data_tools import (
    AccessDenied, DataAction, DataCommand, DataPrincipal,
    SessionDataGateway, validate_command,
)


class DataToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = DataPrincipal("owner-1", frozenset({"sessions:read", "tasks:write"}))

    def test_rejects_unscoped_or_unknown_arguments(self):
        sid = str(uuid4())
        with self.assertRaises(AccessDenied):
            validate_command(DataCommand(DataAction.GET_SESSION,
                DataPrincipal("owner-1", frozenset()), {"session_id": sid}))
        with self.assertRaises(AccessDenied):
            validate_command(DataCommand(DataAction.GET_SESSION, self.owner,
                {"session_id": sid, "sql": "DROP TABLE users"}))
        with self.assertRaises(AccessDenied):
            validate_command(DataCommand(DataAction.LIST_SESSIONS, self.owner,
                {"limit": 10000}))
        with self.assertRaises(AccessDenied):
            validate_command(DataCommand(DataAction.LIST_SESSIONS, self.owner,
                {"limit": True}))

    async def test_session_read_is_owner_scoped_and_parameterized(self):
        db = AsyncMock()
        sid = str(uuid4())
        await SessionDataGateway(db).execute(DataCommand(
            DataAction.GET_SESSION, self.owner, {"session_id": sid}))
        query, identifier, owner = db.fetchrow.await_args.args
        self.assertIn("owner_id=$2", query)
        self.assertEqual(str(identifier), sid)
        self.assertEqual(owner, "owner-1")

    async def test_task_update_is_owner_scoped(self):
        db = AsyncMock()
        tid = str(uuid4())
        await SessionDataGateway(db).execute(DataCommand(
            DataAction.SAVE_TASK_RESULT, self.owner,
            {"task_id": tid, "result": "done"}))
        query, result, identifier, owner = db.execute.await_args.args
        self.assertIn("owner_id=$3", query)
        self.assertEqual((result, str(identifier), owner), ("done", tid, "owner-1"))


if __name__ == "__main__":
    unittest.main()
