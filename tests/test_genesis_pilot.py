"""Genesis pilot tests: real private SQLite file, no external provider or cloud."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reign.genesis_pilot import GenesisPilot, DraftMissing, DraftConflict
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

    def test_preview_rejects_persisted_duplicate_after_restart(self):
        self.pilot.create_draft("research", "Research", ["workspace_status"])
        restarted = GenesisPilot(self.path)
        with self.assertRaises(RegistryDenied):
            restarted.preview("research", "Duplicate", ["workspace_status"])
        self.assertEqual(len(restarted.list_drafts()), 1)

    def test_preview_rejects_full_registry_without_mutation(self):
        for i in range(40):
            self.pilot.create_draft(f"agent-{i:02d}", "Draft", ["workspace_status"])
        with self.assertRaises(RegistryDenied):
            self.pilot.preview("agent-40", "Overflow", ["workspace_status"])
        self.assertEqual(self.pilot.status()["draft_count"], 40)

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

    def test_update_clone_delete_audit_and_restart(self):
        first = self.pilot.create_draft("research", "Research", ["workspace_status"])
        self.assertEqual(first["revision"], 1)
        changed = self.pilot.update_draft("research", "Research Updated",
                   ["browser_status"], expected_revision=1)
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["capabilities"], ["browser_status"])
        with self.assertRaises(DraftConflict):
            self.pilot.update_draft("research", "Stale", [], expected_revision=1)
        copied = self.pilot.clone_draft("research", "research-copy", expected_revision=2)
        self.assertEqual(copied["state"], "draft")
        self.assertFalse(copied["provisioned"])
        self.assertEqual(copied["revision"], 1)
        with self.assertRaises(RegistryDenied):
            self.pilot.update_draft("research", "Unsafe", ["browser_click"], expected_revision=2)
        with self.assertRaises(DraftConflict):
            self.pilot.delete_draft("research", expected_revision=1)
        deleted = self.pilot.delete_draft("research", expected_revision=2)
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(DraftMissing):
            self.pilot.get_draft("research")
        with self.assertRaises(DraftMissing):
            self.pilot.delete_draft("research", expected_revision=2)
        self.assertEqual([event["action"] for event in self.pilot.audit_events("research")],
                         ["deleted", "updated", "created"])
        restarted = GenesisPilot(self.path)
        self.assertEqual([d["agent_id"] for d in restarted.list_drafts()], ["research-copy"])
        self.assertEqual([e["revision"] for e in restarted.audit_events("research")], [3, 2, 1])

    def test_legacy_draft_revision_migrates_without_rewriting(self):
        created = self.pilot.create_draft("legacy", "Legacy", [])
        created.pop("revision")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE agent_drafts SET metadata=? WHERE agent_id='legacy'",
                       (__import__("json").dumps(created),))
        self.assertEqual(self.pilot.get_draft("legacy")["revision"], 1)
        self.assertEqual(self.pilot.update_draft("legacy", "Modern", [],
                         expected_revision=1)["revision"], 2)

    def test_knowledge_and_action_draft_types_are_metadata_not_execution(self):
        action = self.pilot.create_draft("action-one", "Action draft", [],
                                         agent_type="action")
        knowledge = self.pilot.create_draft("knowledge-one", "Knowledge draft",
                                            ["workspace_status"])
        self.assertEqual(action["agent_type"], "action")
        self.assertEqual(knowledge["agent_type"], "knowledge")
        self.assertEqual(self.pilot.status()["execution"], "disabled")
        revised = self.pilot.update_draft("action-one", "Reviewed", [],
                                           expected_revision=1, agent_type="knowledge")
        self.assertEqual(revised["agent_type"], "knowledge")
        copied = self.pilot.clone_draft("action-one", "action-copy",
                                        expected_revision=2)
        self.assertEqual(copied["agent_type"], "knowledge")
        self.assertFalse(copied["provisioned"])
        with self.assertRaises(ValueError):
            self.pilot.create_draft("invalid-one", "Invalid", [], agent_type="executor")
        with self.assertRaises(ValueError):
            self.pilot.update_draft("action-one", "Invalid", [],
                                    expected_revision=2, agent_type="executor")
        self.assertEqual(GenesisPilot(self.path).get_draft("action-one")["agent_type"],
                         "knowledge")

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
