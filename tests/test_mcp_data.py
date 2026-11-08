import os
import unittest
from unittest.mock import patch

from reign.mcp_data import principal


class MCPDataTests(unittest.TestCase):
    def test_missing_owner_is_rejected(self):
        with patch.dict(os.environ, {"REIGN_MCP_OWNER_ID": "",
                                     "REIGN_MCP_SCOPES": "sessions:read"}):
            with self.assertRaises(PermissionError):
                principal()

    def test_scopes_are_server_assigned(self):
        with patch.dict(os.environ, {"REIGN_MCP_OWNER_ID": "user-1",
                                     "REIGN_MCP_SCOPES": "sessions:read"}):
            result = principal()
            self.assertEqual(result.owner_id, "user-1")
            self.assertNotIn("tasks:write", result.scopes)


if __name__ == "__main__":
    unittest.main()
