import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from reign.team_store import DATABASE_NAME, TeamError, TeamStore, owner_identity
from reign.team_api import TeamRuntime


class TeamStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reign-team-test-")
        self.addCleanup(self.temp.cleanup)
        self.key = bytes(range(32))
        self.store = TeamStore(self.temp.name, self.key)
        self.store.initialize()
        self.owner = owner_identity()
        created = self.store.create_member(self.owner, "fixture-employee", "Fixture Employee")
        self.member = self.store.authenticate("fixture-employee", created["temporary_password"])
        self.password = created["temporary_password"]
        self.room = self.store.create_room(self.member, "Private fixture room")["room"]["id"]

    def data(self, **extra):
        return TeamRuntime.message_data({"client_message_id": str(uuid.uuid4()), "text": "Sensitive fixture discussion", **extra})

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(TeamError) as error: function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_encrypted_records_and_employee_credentials_survive_a_fresh_store(self):
        sent = self.store.append_message(self.member, self.room, self.data())
        document = self.store.save_document(self.member, self.room, "Confidential document title", "Confidential document contents",
                                            client_id=str(uuid.uuid4()))["document"]
        fresh = TeamStore(self.temp.name, self.key); fresh.initialize()
        self.assertEqual(fresh.authenticate("FIXTURE-EMPLOYEE", self.password)["id"], self.member["id"])
        restored = fresh.events(self.member, self.room)
        self.assertEqual(restored["events"][0], sent["event"])
        self.assertEqual(fresh.document(self.member, self.room, document["id"])["document"], document)
        self.assertFalse(fresh.overview(self.member)["workspace"]["durable"], "temporary test directories must not claim production durability")
        raw = (Path(self.temp.name) / DATABASE_NAME).read_bytes()
        for secret in (self.password, "pbkdf2_sha256", "Sensitive fixture discussion", "Private fixture room", "Confidential document title", "Confidential document contents", "fixture-employee"):
            self.assertNotIn(secret.encode(), raw)
        self.assertEqual((Path(self.temp.name) / DATABASE_NAME).stat().st_mode & 0o077, 0)

    def test_console_grant_is_per_member_mac_bound_and_revocable(self):
        self.assertFalse(self.store.console_access(self.member["id"], self.member["credential_revision"]))
        self.assert_code("team_owner_required", self.store.set_console_access, self.member, self.member["id"], True)
        self.assert_code("invalid_team_request", self.store.set_console_access, self.owner, self.member["id"], 1)
        granted = self.store.set_console_access(self.owner, self.member["id"], True)["member"]
        self.assertTrue(granted["console_access"])
        fresh = TeamStore(self.temp.name, self.key); fresh.initialize()
        self.assertTrue(fresh.console_access(self.member["id"], self.member["credential_revision"]))
        self.assertEqual(fresh.authenticate("fixture-employee", self.password)["role"], "employee")
        with sqlite3.connect(Path(self.temp.name) / DATABASE_NAME) as db:
            db.execute("UPDATE memberships SET console_access=0 WHERE user_id=?", (self.member["id"],))
        self.assert_code("team_unavailable", self.store.console_access, self.member["id"], self.member["credential_revision"])
        with sqlite3.connect(Path(self.temp.name) / DATABASE_NAME) as db:
            db.execute("UPDATE memberships SET console_access=1 WHERE user_id=?", (self.member["id"],))
        self.assertFalse(self.store.set_console_access(self.owner, self.member["id"], False)["member"]["console_access"])
        self.store.set_console_access(self.owner, self.member["id"], True)
        self.store.disable_member(self.owner, self.member["id"])
        self.assert_code("team_identity_required", self.store.console_access, self.member["id"], self.member["credential_revision"])

    def test_only_owner_creates_members_and_disabled_accounts_cannot_resolve_or_read(self):
        self.assert_code("invalid_team_request", self.store.create_member, self.owner, "control-character", "Cannot\tSign In")
        self.assert_code("team_owner_required", self.store.create_member, self.member, "other", "Other")
        self.assert_code("team_identity_required", self.store.resolve, self.member["id"], 2)
        self.assert_code("invalid_credentials", self.store.authenticate, "fixture-employee", "incorrect-synthetic-password")
        self.store.disable_member(self.owner, self.member["id"])
        self.assert_code("invalid_credentials", self.store.authenticate, "fixture-employee", self.password)
        self.assert_code("team_identity_required", self.store.events, self.member, self.room)
        self.assert_code("member_exists", self.store.create_member, self.owner, "fixture-employee", "New occupant")
        self.assertTrue(self.store.events(self.owner, self.room)["room"])

    def test_workspace_display_name_preserves_legacy_bindings_and_saved_titles(self):
        with patch("reign.team_store.WORKSPACE_NAME", "Rain"):
            room = self.store.create_room(self.member, "Rain planning notes")["room"]
            self.assertEqual(self.store.overview(self.member)["workspace"]["name"], "Rain")
        database = Path(self.temp.name) / DATABASE_NAME
        with sqlite3.connect(database) as db:
            original = db.execute("SELECT * FROM rooms WHERE id=?", (room["id"],)).fetchone()
            self.assertEqual(original[1], "rain")
            self.assertEqual(db.execute("SELECT DISTINCT workspace FROM memberships").fetchall(), [("rain",)])
        fresh = TeamStore(self.temp.name, self.key); fresh.initialize()
        actor = fresh.authenticate("fixture-employee", self.password)
        overview = fresh.overview(actor)
        context = fresh.room_context(actor, room["id"])
        self.assertEqual(overview["workspace"], {"id": "rain", "name": "Workspace", "durable": False})
        self.assertEqual(context["workspace"], {"id": "rain", "name": "Workspace"})
        self.assertEqual(context["room"], room)
        self.assertIn(room, overview["rooms"])
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute("SELECT * FROM rooms WHERE id=?", (room["id"],)).fetchone(), original)

    def test_message_retries_have_one_ordered_event_and_conflicting_reuse_is_rejected(self):
        data = self.data()
        first = self.store.append_message(self.member, self.room, data)
        second = self.store.append_message(self.member, self.room, data)
        self.assertEqual(first["event"], second["event"])
        self.assertTrue(second["duplicate"])
        self.assert_code("idempotency_conflict", self.store.append_message, self.member, self.room, {**data, "text": "Different"})
        for index in range(3): self.store.append_message(self.owner, self.room, self.data(text=f"Message {index}"))
        events = self.store.events(self.member, self.room, 1)
        self.assertEqual([event["seq"] for event in events["events"]], [2, 3, 4])
        self.assertEqual(events["last_seq"], 4)
        other = self.store.create_room(self.owner, "Another room")["room"]["id"]
        self.assertEqual(self.store.events(self.member, other)["events"], [])
        self.assertEqual(self.store.room_context(self.member, other)["messages"], [])

    def test_document_versions_conflicts_and_cross_room_binding(self):
        cid = str(uuid.uuid4())
        first = self.store.save_document(self.member, self.room, "Plan", "Version one", client_id=cid)["document"]
        repeated = self.store.save_document(self.member, self.room, "Plan", "Version one", client_id=cid)["document"]
        self.assertEqual(first, repeated)
        second = self.store.save_document(self.owner, self.room, "Plan updated", "Version two", document_id=first["id"], expected_version=1)["document"]
        self.assertEqual(second["version"], 2)
        self.assert_code("document_conflict", self.store.save_document, self.member, self.room, "Old edit", "Must remain unsaved", document_id=first["id"], expected_version=1)
        self.assertEqual(self.store.document(self.member, self.room, first["id"])["document"]["content"], "Version two")
        other = self.store.create_room(self.owner, "Different room")["room"]["id"]
        self.assert_code("team_not_found", self.store.document, self.member, other, first["id"])
        self.assert_code("team_not_found", self.store.save_document, self.member, other, "No", "No", document_id=first["id"], expected_version=2)

    def test_restart_marks_interrupted_runs_failed_without_replaying_them(self):
        receipt = self.store.append_message(self.member, self.room, self.data(ask_reign=True))
        self.assertIsNotNone(receipt["run_id"])
        fresh = TeamStore(self.temp.name, self.key); fresh.initialize()
        snapshot = fresh.events(self.member, self.room)
        self.assertEqual(snapshot["assistant"]["status"], "failed")
        self.assertEqual(snapshot["events"][-1]["type"], "assistant_error")
        self.assertEqual(snapshot["events"][-1]["data"]["code"], "assistant_interrupted")
        fresh.initialize()
        self.assertEqual(len(fresh.events(self.member, self.room)["events"]), 3)

    def test_assistant_state_uses_durable_event_order_even_if_clock_moves_backwards(self):
        with patch.object(self.store, "clock", return_value=2000):
            first = self.store.append_message(self.member, self.room, self.data(ask_reign=True))
            self.store.finish_run(self.member, self.room, first["run_id"], {"text": "First completed answer"})
        with patch.object(self.store, "clock", return_value=1000):
            newest = self.store.append_message(self.member, self.room, self.data(ask_reign=True))
        self.assertEqual(self.store.events(self.member, self.room)["assistant"], {"status": "running", "run_id": newest["run_id"]})

    def test_wrong_keys_unsafe_paths_and_membership_tampering_fail_closed(self):
        wrong = TeamStore(self.temp.name, bytes(reversed(range(32))))
        self.assert_code("team_unavailable", wrong.initialize)
        link = Path(self.temp.name) / "linked"
        link.symlink_to(self.temp.name, target_is_directory=True)
        self.assert_code("team_unavailable", TeamStore(str(link), self.key).initialize)
        with sqlite3.connect(Path(self.temp.name) / DATABASE_NAME) as db:
            db.execute("UPDATE memberships SET active=0 WHERE user_id=?", (self.member["id"],))
        self.assert_code("team_unavailable", self.store.resolve, self.member["id"], 1)

    def test_storage_and_run_budgets_are_enforced_transactionally(self):
        with patch("reign.team_store.MAX_STORAGE_BYTES", 1):
            self.assert_code("team_limit", self.store.append_message, self.member, self.room, self.data())
        self.assertEqual(self.store.events(self.member, self.room)["events"], [])
        receipt = self.store.append_message(self.member, self.room, self.data(ask_reign=True))
        self.store.fail_run(self.room, receipt["run_id"])
        with patch("reign.team_store.MAX_DAILY_RUNS", 1):
            self.assert_code("team_limit", self.store.append_message, self.member, self.room, self.data(ask_reign=True))
        self.assertEqual(len(self.store.events(self.member, self.room)["events"]), 3)

    def test_unknown_workspace_binding_cannot_expose_room_data(self):
        self.store.append_message(self.owner, self.room, self.data())
        with sqlite3.connect(Path(self.temp.name) / DATABASE_NAME) as db:
            db.execute("UPDATE rooms SET workspace='unrelated-workspace' WHERE id=?", (self.room,))
        self.assert_code("team_not_found", self.store.events, self.member, self.room)
        self.assertEqual(self.store.overview(self.member)["rooms"], [])

    def test_browser_bindings_survive_restart_and_reject_stale_or_cross_room_updates(self):
        sid, other_sid = str(uuid.uuid4()), str(uuid.uuid4())
        self.assertIsNone(self.store.browser_binding(self.member, self.room))
        reserved = self.store.reserve_browser(self.member, self.room, sid)
        self.assertEqual((reserved["generation"], reserved["status"]), (1, "opening"))
        self.assert_code("team_browser_conflict", self.store.reserve_browser, self.owner, self.room, other_sid)
        active = self.store.update_browser(self.member, self.room, sid, "active")
        fresh = TeamStore(self.temp.name, self.key); fresh.initialize()
        self.assertEqual(fresh.browser_binding(self.member, self.room), active)
        other_room = self.store.create_room(self.owner, "Second browser room")["room"]["id"]
        self.assert_code("team_browser_conflict", self.store.update_browser, self.owner, other_room, sid, "closed")
        self.assert_code("team_browser_conflict", self.store.reserve_browser, self.owner, other_room, sid)
        self.store.update_browser(self.member, self.room, sid, "closed")
        self.assert_code("team_browser_conflict", self.store.update_browser, self.owner, self.room, sid, "active")
        second = self.store.reserve_browser(self.owner, self.room, other_sid)
        self.assertEqual(second["generation"], 2)
        self.assert_code("team_browser_conflict", self.store.update_browser, self.member, self.room, sid, "closed")
        self.store.disable_member(self.owner, self.member["id"])
        self.assert_code("team_identity_required", self.store.browser_binding, self.member, self.room)
        with sqlite3.connect(Path(self.temp.name) / DATABASE_NAME) as db:
            db.execute("UPDATE browser_bindings SET generation=100 WHERE room=?", (self.room,))
        self.assert_code("team_unavailable", self.store.browser_binding, self.owner, self.room)


if __name__ == "__main__": unittest.main()
