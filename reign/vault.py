"""Core's small encrypted credential vault and one-use browser grants.

Production storage is opt-in and must be an actual dedicated mount. This module
never initializes an ephemeral fallback, reads a database administration secret,
or puts a credential in a tool result. Only the worker-only grant redemption
boundary may receive a decrypted password.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import time
import unicodedata
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from reign.config import checked_url


SCHEMA_VERSION = 1
MAX_ACCOUNTS = 100
MAX_GRANTS = 1000
GRANT_TTL_SECONDS = 60
DATABASE_NAME = "vault.sqlite3"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:@-]{1,128}\Z")
_GRANT_ID = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_ERRORS = {
    "invalid_account": "The account details are invalid.",
    "account_exists": "A saved login with that label already exists. Choose a different label.",
    "origin_not_allowed": "The account must use an allowed HTTPS origin.",
    "account_not_found": "The saved account was not found.",
    "vault_not_ready": "The secure vault is not configured with durable storage and a valid key.",
    "vault_unavailable": "The secure vault is unavailable. Its stored data has not been replaced.",
    "grant_invalid": "The credential grant is invalid, expired, or already used.",
}
_CHECK_AAD = b"reign-vault-key-check-v1"
_CHECK_VALUE = b"reign-vault-key-confirmed-v1"


class VaultError(Exception):
    """A closed error vocabulary; provider/database exception text stays private."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "vault_unavailable"
        super().__init__(_ERRORS[self.code])


def canonical_origin(value: str) -> str:
    """Apply the browser's exact allowlist, rejecting URLs that aren't origins."""
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 512:
            raise ValueError
        value = value.strip()
        if not value or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
            raise ValueError
        checked_url(value)
        parsed = urlsplit(value)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
        host = parsed.hostname
        if not host:
            raise ValueError
        return "https://" + (f"[{host}]" if ":" in host else host)
    except (ValueError, TypeError):
        raise VaultError("origin_not_allowed") from None


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _identifier(value, code="invalid_account") -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise VaultError(code)
    return value


def _epoch(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        raise VaultError("grant_invalid")
    return value


def _text(value, *, maximum, allow_controls=False, empty=False) -> str:
    if not isinstance(value, str) or (not empty and not value) or "\x00" in value:
        raise VaultError("invalid_account")
    try:
        value.encode("utf-8")
        if len(value) > maximum:
            raise VaultError("invalid_account")
    except UnicodeError:
        raise VaultError("invalid_account") from None
    if not allow_controls and any(unicodedata.category(c).startswith("C") for c in value):
        raise VaultError("invalid_account")
    return value


class Vault:
    """A bounded SQLite vault; explicit test directories don't claim durability.

    ``key`` is exactly 32 random bytes or their standard/URL-safe base64 encoding.
    Constructing an instance has no filesystem side effects. Every operation
    rechecks storage, so a detached production disk cannot become a new local DB.
    """

    def __init__(self, directory, key, *, require_mount=False, clock=None):
        self._directory = directory
        self._configured_key = key
        self._require_mount = require_mount
        self._clock = clock or time.time

    def _configuration(self):
        try:
            if not self._directory:
                raise VaultError("vault_not_ready")
            directory = Path(self._directory)
            if (not directory.is_absolute() or directory == Path("/")
                    or not directory.is_dir()
                    or str(directory) != os.path.realpath(directory)
                    or (self._require_mount and not os.path.ismount(directory))):
                raise VaultError("vault_not_ready")
            key = self._configured_key
            if isinstance(key, str):
                key = base64.b64decode(key, altchars=b"-_", validate=True)
            if not isinstance(key, bytes) or len(key) != 32:
                raise VaultError("vault_not_ready")
            return directory, key
        except VaultError:
            raise
        except (OSError, TypeError, ValueError):
            raise VaultError("vault_not_ready") from None

    def _now(self):
        value = float(self._clock())
        if not math.isfinite(value) or not 0 <= value < 253402300799:
            raise VaultError("vault_unavailable")
        return value

    @staticmethod
    def _timestamp(now):
        return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _check_file(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise VaultError("vault_unavailable")
        os.chmod(path, 0o600, follow_symlinks=False)

    @contextmanager
    def _connection(self):
        connection = None
        try:
            directory, key = self._configuration()
            os.chmod(directory, 0o700, follow_symlinks=False)
            path = directory / DATABASE_NAME
            created = False
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
                created = True
            except FileExistsError:
                pass
            self._check_file(path)
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(path) + suffix)
                if os.path.lexists(sidecar):
                    self._check_file(sidecar)
            # mode=rw prevents SQLite from recreating a missing file after the
            # checks above. The enclosing dedicated directory is private.
            connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                         timeout=3, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            self._initialize(connection, key, created)
            yield connection, key
            connection.commit()
        except VaultError:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.rollback()
            raise
        except Exception:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.rollback()
            raise VaultError("vault_unavailable") from None
        finally:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()

    @staticmethod
    def _initialize(connection, key, created):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if created:
            if version or connection.execute("SELECT name FROM sqlite_master").fetchone():
                raise VaultError("vault_unavailable")
            connection.execute("""CREATE TABLE vault_meta (
                name TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL
            )""")
            connection.execute("""CREATE TABLE accounts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, label_index BLOB NOT NULL,
                origin TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                UNIQUE(owner, label_index)
            )""")
            connection.execute("""CREATE TABLE grants (
                token_hash BLOB PRIMARY KEY, owner TEXT NOT NULL,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL, session_id TEXT NOT NULL,
                observation_id TEXT NOT NULL, epoch INTEGER NOT NULL, origin TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL, signature BLOB NOT NULL
            )""")
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(key).encrypt(nonce, _CHECK_VALUE, _CHECK_AAD)
            connection.execute("INSERT INTO vault_meta VALUES (?, ?, ?)", ("key_check", nonce, ciphertext))
            connection.execute("PRAGMA user_version=1")
        elif version != SCHEMA_VERSION:
            # An existing empty/truncated/unknown database isn't a new vault.
            raise VaultError("vault_unavailable")
        check = connection.execute("SELECT nonce, ciphertext FROM vault_meta WHERE name=?", ("key_check",)).fetchone()
        if check is None or AESGCM(key).decrypt(check["nonce"], check["ciphertext"], _CHECK_AAD) != _CHECK_VALUE:
            raise VaultError("vault_unavailable")

    @staticmethod
    def _account_aad(row):
        return _json(["reign-vault-account", SCHEMA_VERSION, row["owner"], row["id"],
                      row["origin"], row["revision"], row["created_at"], row["updated_at"]])

    @staticmethod
    def _label_index(key, owner, label):
        index_key = hmac.digest(key, b"reign-vault-label-index-v1", "sha256")
        return hmac.digest(index_key, _json([owner, label.casefold()]), "sha256")

    def _decode_account(self, row, key):
        plaintext = AESGCM(key).decrypt(row["nonce"], row["ciphertext"], self._account_aad(row))
        data = json.loads(plaintext)
        if (not isinstance(data, dict) or set(data) != {"label", "username", "password"}
                or not all(isinstance(data[name], str) for name in data)
                or not hmac.compare_digest(row["label_index"], self._label_index(key, row["owner"], data["label"]))):
            raise VaultError("vault_unavailable")
        return data

    @staticmethod
    def _metadata(row, data):
        return {"id": row["id"], "label": data["label"], "origin": row["origin"],
                "username": data["username"], "created_at": row["created_at"],
                "updated_at": row["updated_at"]}

    def status(self):
        durable = False
        try:
            directory, _ = self._configuration()
            durable = bool(self._require_mount and os.path.ismount(directory))
            with self._connection() as (connection, key):
                if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    raise VaultError("vault_unavailable")
                for row in connection.execute("SELECT * FROM accounts"):
                    self._decode_account(row, key)
            return {"ready": True, "durable": durable, "code": None}
        except VaultError as error:
            return {"ready": False, "durable": durable, "code": error.code}

    def list_accounts(self, owner="admin"):
        owner = _identifier(owner)
        with self._connection() as (connection, key):
            rows = connection.execute("SELECT * FROM accounts WHERE owner=? ORDER BY created_at, id", (owner,)).fetchall()
            return [self._metadata(row, self._decode_account(row, key)) for row in rows]

    def save_account(self, owner, label, origin, username, password):
        return self._store_account(owner, label, origin, username, password, insert_only=False)

    def create_generated_account(self, owner, label, origin, username):
        """Prepare credentials only; this does not create an account on a website."""
        return self._store_account(owner, label, origin, username, secrets.token_urlsafe(32), insert_only=True)

    def _store_account(self, owner, label, origin, username, password, *, insert_only):
        owner = _identifier(owner)
        if not isinstance(label, str):
            raise VaultError("invalid_account")
        label = _text(unicodedata.normalize("NFKC", label.strip()), maximum=80)
        origin = canonical_origin(origin)
        data = {"label": label, "username": _text(username, maximum=320),
                "password": _text(password, maximum=1024, allow_controls=True)}
        with self._connection() as (connection, key):
            index = self._label_index(key, owner, label)
            existing = connection.execute("SELECT * FROM accounts WHERE owner=? AND label_index=?", (owner, index)).fetchone()
            if existing is not None:
                previous = self._decode_account(existing, key)
                if not insert_only and existing["origin"] == origin and previous == data:
                    return self._metadata(existing, previous)
                # A generic Save form isn't authorization to change an existing
                # login. Keep both the credential and any issued grants intact.
                raise VaultError("account_exists")
            if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= MAX_ACCOUNTS:
                raise VaultError("invalid_account")
            timestamp = self._timestamp(self._now())
            row = {"id": str(uuid.uuid4()), "owner": owner, "origin": origin,
                   "revision": 1, "created_at": timestamp, "updated_at": timestamp}
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(key).encrypt(nonce, _json(data), self._account_aad(row))
            connection.execute("""INSERT INTO accounts
                (id, owner, label_index, origin, revision, created_at, updated_at, nonce, ciphertext)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], owner, index, origin, row["revision"], row["created_at"], row["updated_at"], nonce, ciphertext))
            return self._metadata(row, data)

    def remove_account(self, owner, account_id):
        owner = _identifier(owner)
        account_id = _identifier(account_id)
        with self._connection() as (connection, _):
            return bool(connection.execute("DELETE FROM accounts WHERE owner=? AND id=?", (owner, account_id)).rowcount)

    @staticmethod
    def _grant_signature(key, row):
        signing_key = hmac.digest(key, b"reign-vault-grant-binding-v1", "sha256")
        fields = ["reign-vault-grant", SCHEMA_VERSION, row["token_hash"].hex(),
                  row["owner"], row["account_id"], row["revision"], row["session_id"],
                  row["observation_id"], row["epoch"], row["origin"],
                  row["created_at"], row["expires_at"]]
        return hmac.digest(signing_key, _json(fields), "sha256")

    def issue_grant(self, owner, account_id, session_id, observation_id, epoch, origin):
        owner = _identifier(owner)
        account_id = _identifier(account_id)
        session_id = _identifier(session_id, "grant_invalid")
        observation_id = _identifier(observation_id, "grant_invalid")
        epoch = _epoch(epoch)
        origin = canonical_origin(origin)
        with self._connection() as (connection, key):
            account = connection.execute("SELECT * FROM accounts WHERE owner=? AND id=?", (owner, account_id)).fetchone()
            if account is None:
                raise VaultError("account_not_found")
            if account["origin"] != origin:
                raise VaultError("grant_invalid")
            # Authenticate the record before creating a usable capability.
            self._decode_account(account, key)
            now = self._now()
            connection.execute("DELETE FROM grants WHERE expires_at<=? OR created_at>?", (now, now))
            if connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0] >= MAX_GRANTS:
                raise VaultError("grant_invalid")
            grant_id = secrets.token_urlsafe(32)
            row = {"token_hash": hashlib.sha256(grant_id.encode("ascii")).digest(),
                   "owner": owner, "account_id": account_id, "revision": account["revision"],
                   "session_id": session_id, "observation_id": observation_id, "epoch": epoch,
                   "origin": origin, "created_at": now, "expires_at": now + GRANT_TTL_SECONDS}
            signature = self._grant_signature(key, row)
            connection.execute("""INSERT INTO grants
                (token_hash, owner, account_id, revision, session_id, observation_id, epoch,
                 origin, created_at, expires_at, signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["token_hash"], owner, account_id, row["revision"], session_id, observation_id,
                 epoch, origin, now, row["expires_at"], signature))
            return {"grant_id": grant_id}

    def redeem_grant(self, grant_id, session_id, observation_id, epoch, origin):
        """Internal worker boundary only. Never register this as an agent tool."""
        if not isinstance(grant_id, str) or not _GRANT_ID.fullmatch(grant_id):
            raise VaultError("grant_invalid")
        session_id = _identifier(session_id, "grant_invalid")
        observation_id = _identifier(observation_id, "grant_invalid")
        epoch = _epoch(epoch)
        try:
            origin = canonical_origin(origin)
        except VaultError:
            raise VaultError("grant_invalid") from None
        token_hash = hashlib.sha256(grant_id.encode("ascii")).digest()
        with self._connection() as (connection, key):
            grant = connection.execute("SELECT * FROM grants WHERE token_hash=?", (token_hash,)).fetchone()
            now = self._now()
            if (grant is None
                    or not hmac.compare_digest(grant["signature"], self._grant_signature(key, grant))
                    or not grant["created_at"] <= now < grant["expires_at"]
                    or grant["expires_at"] - grant["created_at"] != GRANT_TTL_SECONDS
                    or (grant["session_id"], grant["observation_id"], grant["epoch"], grant["origin"])
                    != (session_id, observation_id, epoch, origin)):
                raise VaultError("grant_invalid")
            account = connection.execute("SELECT * FROM accounts WHERE owner=? AND id=?",
                                         (grant["owner"], grant["account_id"])).fetchone()
            if account is None or account["revision"] != grant["revision"] or account["origin"] != origin:
                raise VaultError("grant_invalid")
            data = self._decode_account(account, key)
            connection.execute("DELETE FROM grants WHERE token_hash=?", (token_hash,))
            return {"username": data["username"], "password": data["password"]}


def get_vault() -> Vault:
    """Lazy production factory: missing mounts/keys never become ephemeral vaults."""
    return Vault(os.getenv("REIGN_VAULT_DIR"), os.getenv("REIGN_VAULT_KEY"), require_mount=True)
