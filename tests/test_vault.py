"""Synthetic local storage tests; these do not access a deployed vault or key."""
import base64
import concurrent.futures
import os
import sqlite3
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from reign.vault import (
    DATABASE_NAME, GRANT_TTL_SECONDS, MAX_ACCOUNTS, Vault, VaultError,
    canonical_origin, get_vault,
)


ORIGIN = "https://vault-test.example"
OTHER_ORIGIN = "https://other-test.example"
USERNAME = "synthetic-vault-user@example.invalid"
PASSWORD = "Synthetic-password-only-123!NEVER-REAL"
LABEL = "Synthetic saved login"
KEY = bytes(range(32))


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.directory = Path(self.storage.name)
        self.now = 1_800_000_000.0
        self.vault = Vault(self.directory, KEY, clock=lambda: self.now)
        self.env = patch.dict(os.environ, {"ALLOWED_HOSTS": "vault-test.example,other-test.example"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @property
    def database(self):
        return self.directory / DATABASE_NAME

    def save(self, *, owner="admin", label=LABEL, origin=ORIGIN, username=USERNAME, password=PASSWORD):
        return self.vault.save_account(owner, label, origin, username, password)

    def issue(self, account, **overrides):
        args = {"owner": "admin", "account_id": account["id"], "session_id": "session-1",
                "observation_id": "observation-1", "epoch": 7, "origin": ORIGIN}
        args.update(overrides)
        return self.vault.issue_grant(**args)["grant_id"]

    def redeem(self, grant_id, **overrides):
        args = {"grant_id": grant_id, "session_id": "session-1", "observation_id": "observation-1",
                "epoch": 7, "origin": ORIGIN}
        args.update(overrides)
        return self.vault.redeem_grant(**args)

    def assert_code(self, code, operation):
        with self.assertRaises(VaultError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertNotIn(USERNAME, str(caught.exception))

    def sql(self, statement, parameters=()):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(statement, parameters).fetchall()

    def test_constructor_and_factory_are_lazy_and_unconfigured_factory_does_not_write(self):
        self.assertFalse(self.database.exists())
        with patch.dict(os.environ, {}, clear=True):
            vault = get_vault()
            self.assertEqual(vault.status(), {"ready": False, "durable": False, "code": "vault_not_ready"})
            self.assert_code("vault_not_ready", lambda: vault.list_accounts())
        self.assertFalse(self.database.exists())

    def test_production_requires_real_mount_and_valid_key_without_fallback(self):
        encoded = base64.b64encode(KEY).decode("ascii")
        with patch.dict(os.environ, {"REIGN_VAULT_DIR": str(self.directory), "REIGN_VAULT_KEY": encoded}):
            with patch("reign.vault.os.path.ismount", return_value=False):
                self.assertFalse(get_vault().status()["ready"])
                self.assertFalse(self.database.exists())
            with patch("reign.vault.os.path.ismount", return_value=True):
                self.assertEqual(get_vault().status(), {"ready": True, "durable": True, "code": None})
            before = self.database.read_bytes()
            with patch("reign.vault.os.path.ismount", return_value=False):
                self.assert_code("vault_not_ready", lambda: get_vault().list_accounts())
            self.assertEqual(before, self.database.read_bytes())

    def test_invalid_keys_do_not_initialize_even_with_a_mount(self):
        for key in (None, "", "not-a-key", " " + base64.b64encode(KEY).decode(),
                    base64.b64encode(b"short").decode(), b"short", base64.b64encode(KEY).decode().rstrip("=")):
            with self.subTest(kind=type(key).__name__, length=len(key) if key is not None else None):
                vault = Vault(self.directory, key, require_mount=True)
                with patch("reign.vault.os.path.ismount", return_value=True):
                    self.assertEqual(vault.status()["code"], "vault_not_ready")
                self.assertFalse(self.database.exists())

    def test_explicit_test_storage_does_not_claim_durability_and_encoded_keys_work(self):
        self.assertEqual(self.vault.status(), {"ready": True, "durable": False, "code": None})
        self.save()
        for encoder in (base64.b64encode, base64.urlsafe_b64encode):
            reopened = Vault(self.directory, encoder(KEY).decode("ascii"))
            self.assertEqual(reopened.list_accounts()[0]["username"], USERNAME)

    def test_credentials_and_labels_encrypted_on_disk_and_metadata_survives_reopen(self):
        account = self.save()
        self.assertEqual(set(account), {"id", "label", "origin", "username", "created_at", "updated_at"})
        self.assertNotIn(PASSWORD, repr(account))
        raw = self.database.read_bytes()
        for value in (PASSWORD, USERNAME, LABEL):
            self.assertNotIn(value.encode(), raw)
        reopened = Vault(self.directory, KEY)
        self.assertEqual(reopened.list_accounts(), [account])
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)

    def test_wrong_key_does_not_modify_or_replace_existing_data(self):
        account = self.save()
        before = self.database.read_bytes()
        wrong = Vault(self.directory, b"x" * 32)
        self.assertEqual(wrong.status()["code"], "vault_unavailable")
        self.assert_code("vault_unavailable", lambda: wrong.save_account("admin", "another", ORIGIN, USERNAME, PASSWORD))
        self.assert_code("vault_unavailable", lambda: wrong.remove_account("admin", account["id"]))
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(self.vault.list_accounts(), [account])

    def test_existing_empty_or_corrupt_database_is_not_reinitialized(self):
        for content in (b"", b"synthetic-corrupt-not-a-database"):
            with self.subTest(length=len(content)):
                self.database.write_bytes(content)
                self.assertEqual(self.vault.status()["code"], "vault_unavailable")
                self.assertEqual(self.database.read_bytes(), content)
                self.database.unlink()

    def test_symlink_directory_database_and_hardlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere)
            link = self.directory / "linked-directory"
            link.symlink_to(target, target_is_directory=True)
            self.assertEqual(Vault(link, KEY).status()["code"], "vault_not_ready")
            self.assertEqual(list(target.iterdir()), [])
            external = target / "untouched"
            external.write_bytes(b"untouched")
            self.database.symlink_to(external)
            self.assertEqual(self.vault.status()["code"], "vault_unavailable")
            self.assertEqual(external.read_bytes(), b"untouched")
            self.database.unlink()
            os.link(external, self.database)
            self.assertEqual(self.vault.status()["code"], "vault_unavailable")
            self.assertEqual(external.read_bytes(), b"untouched")

    def test_owner_isolation_and_parameterized_labels(self):
        label = "saved'); DROP TABLE accounts; --"
        first = self.save(label=label)
        second = self.save(owner="different-owner", label=label, username="other-synthetic-user")
        self.assertEqual(self.vault.list_accounts(), [first])
        self.assertEqual(self.vault.list_accounts("different-owner"), [second])
        self.assertFalse(self.vault.remove_account("different-owner", first["id"]))
        self.assert_code("account_not_found", lambda: self.issue(first, owner="different-owner"))
        self.assertEqual(self.vault.list_accounts(), [first])

    def test_same_save_is_idempotent_changed_duplicates_preserve_credentials_and_grants(self):
        first = self.save()
        initial = self.sql("SELECT nonce, revision FROM accounts")[0]
        grant = self.issue(first)
        self.now += 1
        repeated = self.save()
        self.assertEqual(repeated, first)
        repeated_row = self.sql("SELECT nonce, revision FROM accounts")[0]
        self.assertEqual(tuple(repeated_row), tuple(initial))
        self.assertEqual(self.redeem(grant)["password"], PASSWORD)
        old_grant = self.issue(first)
        before = self.database.read_bytes()
        self.assert_code("account_exists", lambda: self.save(password="Replacement-synthetic-password"))
        self.assert_code("account_exists", lambda: self.save(username="replacement-synthetic-user"))
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(self.vault.list_accounts(), [first])
        self.assertEqual(self.redeem(old_grant)["password"], PASSWORD)
        second = self.save(label="Another synthetic login")
        second_nonce = self.sql("SELECT nonce FROM accounts WHERE id=?", (second["id"],))[0]["nonce"]
        self.assertNotEqual(second_nonce, initial["nonce"])

    def test_labels_are_normalized_per_owner_but_cannot_move_to_another_origin(self):
        first = self.save(label=" Work Account ")
        repeated = self.save(label="Work Account")
        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(len(self.vault.list_accounts()), 1)
        self.assert_code("account_exists", lambda: self.save(label="WORK ACCOUNT"))
        self.assert_code("account_exists", lambda: self.save(label="Work Account", origin=OTHER_ORIGIN))
        self.assertEqual(self.vault.list_accounts(), [first])

    def test_account_bounds_and_origin_validation(self):
        for args in ({"label": ""}, {"label": "x" * 81}, {"username": ""},
                     {"username": "x" * 321}, {"password": ""}, {"password": "x" * 1025},
                     {"owner": "bad owner"}, {"label": "invisible\u200b"}):
            with self.subTest(fields=list(args)):
                self.assert_code("invalid_account", lambda args=args: self.save(**args))
        for origin in ("http://vault-test.example", "https://unlisted.example", ORIGIN + "/login",
                       ORIGIN + "?token=synthetic", ORIGIN + "#fragment", "https://user:synthetic@vault-test.example",
                       ORIGIN + ":444", "https://vault- test.example", ORIGIN + "/" + "x" * 512):
            with self.subTest(origin=origin):
                self.assert_code("origin_not_allowed", lambda origin=origin: self.save(origin=origin))
        self.assertEqual(canonical_origin("  https://VAULT-TEST.EXAMPLE:443/  "), ORIGIN)
        self.assertFalse(self.database.exists())

    def test_character_bounds_and_credential_whitespace_are_preserved(self):
        username = " " + "ü" * 318 + " "
        password = " " + "密" * 1022 + " "
        account = self.save(username=username, password=password)
        self.assertEqual(account["username"], username)
        self.assertEqual(self.redeem(self.issue(account)), {"username": username, "password": password})
        self.assert_code("invalid_account", lambda: self.save(username=username + "x"))
        self.assert_code("invalid_account", lambda: self.save(password=password + "x"))

    def test_one_hundred_account_limit_still_allows_idempotent_saves_and_removal(self):
        for index in range(MAX_ACCOUNTS):
            self.save(label=f"Synthetic {index}")
        self.assert_code("invalid_account", lambda: self.save(label="one too many"))
        self.save(label="Synthetic 0")
        account = self.vault.list_accounts()[0]
        self.assertTrue(self.vault.remove_account("admin", account["id"]))
        self.assertFalse(self.vault.remove_account("admin", account["id"]))
        self.save(label="replacement")
        self.assertEqual(len(self.vault.list_accounts()), MAX_ACCOUNTS)

    def test_tampering_ciphertext_or_identity_never_returns_partial_accounts(self):
        first = self.save()
        self.save(label="Second synthetic account")
        row = self.sql("SELECT * FROM accounts WHERE id=?", (first["id"],))[0]
        mutations = {"owner": "attacker", "origin": OTHER_ORIGIN, "revision": 200,
                     "created_at": "changed", "updated_at": "changed", "label_index": b"changed",
                     "ciphertext": bytes(row["ciphertext"][:-1]) + bytes([row["ciphertext"][-1] ^ 1])}
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.sql(f"UPDATE accounts SET {field}=? WHERE id=?", (value, first["id"]))
                self.assertEqual(self.vault.status()["code"], "vault_unavailable")
                if field != "owner":
                    self.assert_code("vault_unavailable", lambda: self.vault.list_accounts())
                self.sql(f"UPDATE accounts SET {field}=? WHERE id=?", (row[field], first["id"]))
        self.assertTrue(self.vault.status()["ready"])

    def test_grants_are_single_use_persist_across_reopen_and_not_stored_as_bearer_tokens(self):
        account = self.save()
        grant = self.issue(account)
        raw = self.database.read_bytes()
        for value in (grant, PASSWORD, USERNAME):
            self.assertNotIn(value.encode(), raw)
        reopened = Vault(self.directory, KEY, clock=lambda: self.now)
        result = reopened.redeem_grant(grant, "session-1", "observation-1", 7, ORIGIN)
        self.assertEqual(result, {"username": USERNAME, "password": PASSWORD})
        self.assert_code("grant_invalid", lambda: self.redeem(grant))

    def test_wrong_binding_never_releases_or_consumes_a_valid_grant(self):
        account = self.save()
        grant = self.issue(account)
        for changes in ({"session_id": "session-2"}, {"observation_id": "observation-2"},
                        {"epoch": 8}, {"origin": OTHER_ORIGIN}, {"origin": "https://unlisted.example"},
                        {"epoch": True}, {"epoch": -1}, {"session_id": ""}):
            with self.subTest(fields=list(changes)):
                self.assert_code("grant_invalid", lambda changes=changes: self.redeem(grant, **changes))
        self.assertEqual(self.redeem(grant)["password"], PASSWORD)

    def test_expiry_is_exactly_sixty_seconds_and_clock_rollback_fails_closed(self):
        account = self.save()
        first = self.issue(account)
        self.now += GRANT_TTL_SECONDS - 0.001
        self.assertEqual(self.redeem(first)["password"], PASSWORD)
        expired = self.issue(account)
        self.now += GRANT_TTL_SECONDS
        self.assert_code("grant_invalid", lambda: self.redeem(expired))
        future = self.issue(account)
        self.now -= 1
        self.assert_code("grant_invalid", lambda: self.redeem(future))

    def test_deletion_invalidates_grants_and_another_owner_cannot_issue_them(self):
        account = self.save()
        grant = self.issue(account)
        self.assert_code("account_not_found", lambda: self.issue(account, owner="another-owner"))
        self.assert_code("grant_invalid", lambda: self.issue(account, origin=OTHER_ORIGIN))
        self.assertTrue(self.vault.remove_account("admin", account["id"]))
        self.assert_code("grant_invalid", lambda: self.redeem(grant))
        self.assertEqual(self.sql("SELECT * FROM grants"), [])

    def test_grant_cannot_survive_a_changed_account_revision(self):
        account = self.save()
        grant = self.issue(account)
        self.sql("UPDATE accounts SET revision=revision+1 WHERE id=?", (account["id"],))
        self.assert_code("grant_invalid", lambda: self.redeem(grant))

    def test_grant_row_tampering_cannot_change_its_account_or_page_or_lifetime(self):
        account = self.save()
        other = self.save(label="Another synthetic account", username="different-synthetic-user")
        grant = self.issue(account)
        original = self.sql("SELECT * FROM grants")[0]
        mutations = {"owner": "another-owner", "account_id": other["id"], "revision": 8,
                     "session_id": "session-2", "observation_id": "observation-2", "epoch": 8,
                     "origin": OTHER_ORIGIN, "expires_at": original["expires_at"] + 3600}
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.sql(f"UPDATE grants SET {field}=?", (value,))
                matching = {field: value} if field in {"session_id", "observation_id", "epoch", "origin"} else {}
                self.assert_code("grant_invalid", lambda matching=matching: self.redeem(grant, **matching))
                self.sql(f"UPDATE grants SET {field}=?", (original[field],))
        self.assertEqual(self.redeem(grant)["password"], PASSWORD)

    def test_two_independent_workers_can_redeem_only_once(self):
        grant = self.issue(self.save())
        barrier = threading.Barrier(2)

        def redeem_once():
            instance = Vault(self.directory, KEY, clock=lambda: self.now)
            barrier.wait(timeout=3)
            try:
                instance.redeem_grant(grant, "session-1", "observation-1", 7, ORIGIN)
                return "redeemed"
            except VaultError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: redeem_once(), range(2)))
        self.assertCountEqual(outcomes, ["redeemed", "grant_invalid"])

    def test_generated_account_returns_no_password_and_duplicate_never_overwrites(self):
        account = self.vault.create_generated_account("admin", LABEL, ORIGIN, USERNAME)
        self.assertEqual(set(account), {"id", "label", "origin", "username", "created_at", "updated_at"})
        generated = self.redeem(self.issue(account))["password"]
        self.assertGreaterEqual(len(generated), 32)
        self.assertNotIn(generated, repr(account))
        self.assertNotIn(generated.encode(), self.database.read_bytes())
        self.assert_code("account_exists", lambda: self.vault.create_generated_account("admin", LABEL.upper(), ORIGIN, "changed-user"))
        self.assertEqual(self.vault.list_accounts(), [account])
        self.assertEqual(self.redeem(self.issue(account))["password"], generated)

    def test_concurrent_generated_signup_preparation_is_insert_only(self):
        self.assertTrue(self.vault.status()["ready"])
        barrier = threading.Barrier(2)

        def prepare():
            instance = Vault(self.directory, KEY, clock=lambda: self.now)
            barrier.wait(timeout=3)
            try:
                return instance.create_generated_account("admin", LABEL, ORIGIN, USERNAME)
            except VaultError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: prepare(), range(2)))
        self.assertEqual(sum(isinstance(outcome, dict) for outcome in outcomes), 1)
        self.assertIn("account_exists", outcomes)
        self.assertEqual(len(self.vault.list_accounts()), 1)

    def test_safe_errors_never_embed_input_or_database_details(self):
        self.save()
        self.sql("UPDATE vault_meta SET ciphertext=?", (b"synthetic-corrupt",))
        for operation in (lambda: self.vault.list_accounts(), lambda: self.save(),
                          lambda: self.vault.issue_grant("admin", "unknown", "session", "observation", 0, ORIGIN)):
            with self.assertRaises(VaultError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "vault_unavailable")
            for value in (PASSWORD, USERNAME, str(self.directory), "sqlite", "InvalidTag", "synthetic-corrupt"):
                self.assertNotIn(value, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
