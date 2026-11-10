"""Genesis pilot tests: real private SQLite file, no external provider or cloud."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reign.genesis_pilot import GenesisPilot
from reign.sovereign_registry import RegistryDenied


class GenesisPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "genesis.sqlite")
        self.pilot = GenesisPilot(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_restart_list_without_creating_resources(self):
        draft = self.pilot.create_draft("research", "Research analyst", ["workspace_status"])
        self.assertEqual(draft["state"], "draft")
        self.assertFalse(draft["provisioned"])
        reloaded = GenesisPilot(self.path)
        self.assertEqual(reloaded.list_drafts(), [draft])
        self.assertEqual(reloaded.status()["execution"], "disabled")
        self.assertEqual(reloaded.status()["draft_count"], 1)
        self.assertEqual(os.stat(self.path).st_mode & 0o077, 0)

    def test_preview_does_not_mutate(self):
        preview = self.pilot.preview("research", "Research", ["workspace_status", "browser_click"])
        self.assertEqual(preview["allowed_capabilities"], ("workspace_status",))
        self.assertEqual(preview["rejected_capabilities"], ("browser_click",))
        self.assertEqual(self.pilot.list_drafts(), [])

    def test_blocked_capabilities_and_duplicate_registration_fail(self):
        with self.assertRaises(RegistryDenied):
            self.pilot.create_draft("research", "Research", ["browser_click"])
        self.assertEqual(self.pilot.list_drafts(), [])
        self.pilot.create_draft("research", "Research", ["browser_status"])
        with self.assertRaises(RegistryDenied):
            self.pilot.create_draft("research", "Duplicate", ["browser_status"])
        self.assertEqual(len(self.pilot.list_drafts()), 1)

    def test_invalid_id_and_malformed_capabilities(self):
        with self.assertRaises(ValueError):
            self.pilot.create_draft("BAD ID", "Oops", [])
        with self.assertRaises(ValueError):
            self.pilot.create_draft("okay", "Oops", ["workspace_status"] * 13)

    def test_symlink_registry_is_rejected(self):
        target = str(Path(self.tmp.name) / "real")
        Path(target).write_text("test")
        link = str(Path(self.tmp.name) / "link")
        os.symlink(target, link)
        with self.assertRaises(RuntimeError):
            GenesisPilot(link)

    def test_activation_refuses_non_mount(self):
        with patch.dict(os.environ, {"GENESIS_DRAFT_MODE": "enabled",
                                     "REIGN_VAULT_DIR": self.tmp.name}):
            with self.assertRaises(RuntimeError):
                GenesisPilot.from_env()
