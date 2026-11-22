"""Encrypted team records on the existing private Core disk.

All public methods reauthorize their server-derived actor inside the transaction.
This store never reads the legacy owner archive, account vault, or browser state.
"""
import base64
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from reign.ax_auth import hash_password, _password_bytes, _verifier, InvalidCredentials
from reign.team_identity import OWNER_ID

WORKSPACE_ID = "rain"
# Display copy is independent of the durable membership, room and crypto bindings.
WORKSPACE_NAME = "Workspace"
MOUNT = "/var/data/reign-vault"
DATABASE_NAME = "reign-team.sqlite3"
MAX_MEMBERS = 32
MAX_ROOM_PARTICIPANTS = 16
MAX_ROOMS = 100
MAX_EVENTS = 20000
MAX_DOCUMENTS = 100
MAX_VERSIONS = 100
MAX_STORAGE_BYTES = 96 * 1024 * 1024
MAX_DAILY_RUNS = 200
MAX_MINUTE_RUNS = 20
EVENT_TYPES = {"message", "assistant_started", "assistant_message", "assistant_error", "document_saved"}
ERRORS = {
    "invalid_team_request": (422, "The workspace request is invalid."),
    "invalid_credentials": (401, "Invalid credentials"),
    "team_identity_required": (401, "Sign in again to use the workspace."),
    "team_owner_required": (403, "Workspace owner access is required."),
    "team_not_found": (404, "The workspace item was not found."),
    "member_exists": (409, "That username is already reserved. Refresh the member list."),
    "document_conflict": (409, "This document changed. Reload it before saving your edits."),
    "idempotency_conflict": (409, "That request identifier was already used for different content."),
    "assistant_busy": (409, "REIGN is already working in this room. Wait for its reply."),
    "team_call_full": (409, "This room already has four call connections."),
    "team_connection_required": (403, "Join the room call before using this connection."),
    "team_voice_closed": (410, "Live voice is no longer connected. Turn the microphone on to reconnect."),
    "team_voice_closing": (409, "Your previous voice connection is still closing. Try Microphone on again in a moment."),
    "team_browser_conflict": (409, "The room browser changed. Refresh the room before trying again."),
    "team_limit": (429, "A workspace limit was reached. Try again later or contact the owner."),
    "assistant_unavailable": (503, "The room assistant is not available."),
    "assistant_failed": (503, "REIGN could not finish this reply. Please try again."),
    "assistant_interrupted": (503, "The service restarted before REIGN finished. Send a new request to continue."),
    "team_unavailable": (503, "The saved workspace is unavailable. Please try again later."),
}


class TeamError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "team_unavailable"
        self.status, self.detail = ERRORS[self.code]
        super().__init__(self.detail)


def identifier(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise TeamError("invalid_team_request") from None


def text(value, maximum, *, bytes_max=None, empty=False):
    if type(value) is not str or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        raise TeamError("invalid_team_request")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise TeamError("invalid_team_request") from None
    if len(encoded) > (bytes_max if bytes_max is not None else maximum * 4):
        raise TeamError("invalid_team_request")
    return value


def boolean(value):
    if type(value) is not bool:
        raise TeamError("invalid_team_request")
    return value


def owner_identity():
    return {"id": OWNER_ID, "username": "admin", "display_name": "Owner",
            "role": "owner", "credential_revision": 0}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _timestamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat(timespec="microseconds")


class TeamStore:
    def __init__(self, directory, key, *, require_mount=False, clock=None):
        self.directory, self.configured_key = directory, key
        self.require_mount = require_mount
        self.clock = clock or time.time

    @classmethod
    def from_env(cls):
        if os.getenv("REIGN_TEAM_MODE", "disabled") != "disk" or os.getenv("REIGN_VAULT_DIR") != MOUNT:
            raise TeamError("team_unavailable")
        return cls(MOUNT, os.getenv("REIGN_VAULT_KEY", ""), require_mount=True)

    @property
    def durable(self):
        return self.require_mount

    def _configuration(self):
        try:
            folder = Path(self.directory)
            if (not folder.is_absolute() or str(folder) != os.path.realpath(folder) or not folder.is_dir()
                    or folder.stat().st_mode & 0o077
                    or (self.require_mount and (str(folder) != MOUNT or not os.path.ismount(folder)))):
                raise ValueError()
            key = self.configured_key
            if isinstance(key, str):
                key = base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)
            if type(key) is not bytes or len(key) != 32:
                raise ValueError()
            return folder, hmac.digest(key, b"reign/team-store/encryption/v1", "sha256")
        except (ValueError, TypeError, OSError):
            raise TeamError("team_unavailable") from None

    @staticmethod
    def _file(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            raise TeamError("team_unavailable")

    @contextmanager
    def _connection(self):
        db = None
        try:
            folder, key = self._configuration()
            path = folder / DATABASE_NAME
            created = False
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(fd); created = True
            except FileExistsError:
                pass
            self._file(path)
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(path) + suffix)
                if os.path.lexists(sidecar):
                    self._file(sidecar)
            db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=3, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA max_page_count=32768")
            db.execute("BEGIN IMMEDIATE")
            self._initialize(db, key, created)
            yield db, key
            db.commit()
        except TeamError:
            if db is not None:
                with suppress(sqlite3.Error): db.rollback()
            raise
        except Exception:
            if db is not None:
                with suppress(sqlite3.Error): db.rollback()
            raise TeamError("team_unavailable") from None
        finally:
            if db is not None:
                with suppress(sqlite3.Error): db.close()

    def _initialize(self, db, key, created):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if created:
            if version or db.execute("SELECT name FROM sqlite_master").fetchone():
                raise TeamError("team_unavailable")
            statements = [
                "CREATE TABLE meta (name TEXT PRIMARY KEY, value BLOB NOT NULL)",
                "CREATE TABLE usage (id INTEGER PRIMARY KEY CHECK(id=1), bytes INTEGER NOT NULL)",
                "CREATE TABLE users (id TEXT PRIMARY KEY, lookup BLOB UNIQUE NOT NULL, revision INTEGER NOT NULL, created TEXT NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL)",
                "CREATE TABLE memberships (workspace TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id), active INTEGER NOT NULL, mac BLOB NOT NULL, PRIMARY KEY(workspace,user_id))",
                "CREATE TABLE rooms (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, created_by TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, next_seq INTEGER NOT NULL DEFAULT 1, nonce BLOB NOT NULL, body BLOB NOT NULL)",
                "CREATE TABLE events (id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id), seq INTEGER NOT NULL, type TEXT NOT NULL, user_id TEXT NOT NULL, created TEXT NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL, UNIQUE(room,seq))",
                "CREATE TABLE receipts (room TEXT NOT NULL, user_id TEXT NOT NULL, client_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(id), run_id TEXT, fingerprint BLOB NOT NULL, PRIMARY KEY(room,user_id,client_id))",
                "CREATE TABLE runs (id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id), user_id TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, trigger_seq INTEGER NOT NULL, error TEXT)",
                "CREATE INDEX runs_created ON runs(created)",
                "CREATE TABLE documents (id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id), user_id TEXT NOT NULL, client_id TEXT NOT NULL, fingerprint BLOB NOT NULL, version INTEGER NOT NULL, updated TEXT NOT NULL, UNIQUE(room,user_id,client_id), UNIQUE(id,room))",
                "CREATE TABLE document_versions (document TEXT NOT NULL, room TEXT NOT NULL, version INTEGER NOT NULL, user_id TEXT NOT NULL, created TEXT NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL, PRIMARY KEY(document,version), FOREIGN KEY(document,room) REFERENCES documents(id,room))",
                "CREATE TABLE browser_bindings (room TEXT PRIMARY KEY REFERENCES rooms(id), session_id TEXT UNIQUE NOT NULL, generation INTEGER NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL)",
            ]
            for statement in statements: db.execute(statement)
            nonce = secrets.token_bytes(12)
            check = AESGCM(key).encrypt(nonce, b"reign-team-store-v1", b"reign-team-key-check-v1")
            db.execute("INSERT INTO meta VALUES (?,?)", ("key_check", nonce + check))
            db.execute("INSERT INTO usage VALUES (1,0)")
            db.execute("PRAGMA user_version=1")
        elif version != 1:
            raise TeamError("team_unavailable")
        value = db.execute("SELECT value FROM meta WHERE name='key_check'").fetchone()
        if not value or AESGCM(key).decrypt(value[0][:12], value[0][12:], b"reign-team-key-check-v1") != b"reign-team-store-v1":
            raise TeamError("team_unavailable")
        # This additive table is part of the initial, still-unreleased schema.
        # Existing test/dev stores retain their records when the surface grows.
        db.execute("CREATE TABLE IF NOT EXISTS browser_bindings (room TEXT PRIMARY KEY REFERENCES rooms(id), session_id TEXT UNIQUE NOT NULL, generation INTEGER NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL)")

    @staticmethod
    def _seal(key, value, *binding):
        nonce = secrets.token_bytes(12)
        return nonce, AESGCM(key).encrypt(nonce, _json(value), _json(["reign-team-v1", *binding]))

    @staticmethod
    def _open(key, nonce, body, *binding):
        return json.loads(AESGCM(key).decrypt(nonce, body, _json(["reign-team-v1", *binding])))

    @staticmethod
    def _fingerprint(key, value):
        return hmac.digest(key, b"reign-team-index-v1\x00" + _json(value), "sha256")

    @staticmethod
    def _reserve(db, size):
        if db.execute("SELECT bytes FROM usage WHERE id=1").fetchone()[0] + size > MAX_STORAGE_BYTES:
            raise TeamError("team_limit")
        db.execute("UPDATE usage SET bytes=bytes+? WHERE id=1", (size,))

    def _member(self, db, key, row):
        membership = db.execute("SELECT * FROM memberships WHERE workspace=? AND user_id=?", (WORKSPACE_ID, row["id"])).fetchone()
        if membership is None:
            raise TeamError("team_identity_required")
        expected = self._fingerprint(key, ["membership", WORKSPACE_ID, row["id"], membership["active"]])
        if not hmac.compare_digest(membership["mac"], expected):
            raise TeamError("team_unavailable")
        value = self._open(key, row["nonce"], row["body"], "user", row["id"], row["revision"])
        return {"id": row["id"], "username": value["username"], "display_name": value["display_name"],
                "role": "employee", "credential_revision": row["revision"],
                "active": bool(membership["active"]), "created_at": row["created"]}, value["password_hash"]

    def _principal(self, db, key, actor):
        if type(actor) is not dict:
            raise TeamError("team_identity_required")
        if actor.get("id") == OWNER_ID and actor.get("role") == "owner" and actor.get("credential_revision") == 0:
            return owner_identity()
        row = db.execute("SELECT * FROM users WHERE id=?", (actor.get("id"),)).fetchone()
        if row is None:
            raise TeamError("team_identity_required")
        member, _ = self._member(db, key, row)
        if not member["active"] or member["role"] != actor.get("role") or member["credential_revision"] != actor.get("credential_revision"):
            raise TeamError("team_identity_required")
        return {key: member[key] for key in ("id", "username", "display_name", "role", "credential_revision")}

    def resolve(self, user_id, credential_revision):
        with self._connection() as (db, key):
            return self._principal(db, key, {"id": user_id, "role": "owner" if user_id == OWNER_ID else "employee",
                                             "credential_revision": credential_revision})

    def authenticate(self, username, password):
        if type(username) is not str or len(username) > 64:
            raise TeamError("invalid_credentials")
        username = username.strip().lower()
        try: raw = _password_bytes(password)
        except InvalidCredentials: raise TeamError("invalid_credentials") from None
        with self._connection() as (db, key):
            row = db.execute("SELECT * FROM users WHERE lookup=?", (self._fingerprint(key, ["username", username]),)).fetchone()
            member, verifier = self._member(db, key, row) if row else (None, None)
        salt, expected = _verifier(verifier) if verifier else (bytes(16), bytes(32))
        actual = hashlib.pbkdf2_hmac("sha256", raw, salt, 600000, dklen=32)
        if member is None or not member["active"] or not hmac.compare_digest(actual, expected):
            raise TeamError("invalid_credentials")
        # Recheck membership/revision after expensive password work.
        return self.resolve(member["id"], member["credential_revision"])

    def create_member(self, actor, username, display_name):
        if type(username) is not str:
            raise TeamError("invalid_team_request")
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", username) or username in {"admin", "owner", "reign", "system"}:
            raise TeamError("invalid_team_request")
        display_name = text(display_name, 80).strip()
        if any(ord(character) < 32 for character in display_name):
            raise TeamError("invalid_team_request")
        # Authorize before spending KDF work; reauthorize before the write.
        if self.resolve(actor.get("id"), actor.get("credential_revision"))["role"] != "owner":
            raise TeamError("team_owner_required")
        password = secrets.token_urlsafe(24)
        verifier = hash_password(password)
        with self._connection() as (db, key):
            if self._principal(db, key, actor)["role"] != "owner": raise TeamError("team_owner_required")
            lookup = self._fingerprint(key, ["username", username])
            if db.execute("SELECT 1 FROM users WHERE lookup=?", (lookup,)).fetchone(): raise TeamError("member_exists")
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_MEMBERS: raise TeamError("team_limit")
            uid, created = str(uuid.uuid4()), _timestamp(self.clock())
            nonce, body = self._seal(key, {"username": username, "display_name": display_name, "password_hash": verifier}, "user", uid, 1)
            self._reserve(db, len(body))
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (uid, lookup, 1, created, nonce, body))
            mac = self._fingerprint(key, ["membership", WORKSPACE_ID, uid, 1])
            db.execute("INSERT INTO memberships VALUES (?,?,?,?)", (WORKSPACE_ID, uid, 1, mac))
            member, _ = self._member(db, key, db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
            return {"member": member, "temporary_password": password}

    def disable_member(self, actor, user_id):
        identifier(user_id)
        with self._connection() as (db, key):
            if self._principal(db, key, actor)["role"] != "owner": raise TeamError("team_owner_required")
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None: raise TeamError("team_not_found")
            mac = self._fingerprint(key, ["membership", WORKSPACE_ID, user_id, 0])
            db.execute("UPDATE memberships SET active=0,mac=? WHERE workspace=? AND user_id=?", (mac, WORKSPACE_ID, user_id))
            member, _ = self._member(db, key, row)
            return {"member": member}

    def _room(self, db, key, room_id):
        identifier(room_id)
        row = db.execute("SELECT * FROM rooms WHERE id=? AND workspace=?", (room_id, WORKSPACE_ID)).fetchone()
        if row is None: raise TeamError("team_not_found")
        value = self._open(key, row["nonce"], row["body"], "room", room_id, WORKSPACE_ID, row["created_by"], row["created"])
        return {"id": room_id, "title": value["title"], "created_at": row["created"], "updated_at": row["updated"]}

    def overview(self, actor):
        with self._connection() as (db, key):
            me = self._principal(db, key, actor)
            members = [{**owner_identity(), "active": True}]
            for row in db.execute("SELECT * FROM users ORDER BY created,id").fetchall():
                member, _ = self._member(db, key, row)
                if me["role"] == "owner" or member["active"]: members.append(member)
            rooms = [self._room(db, key, row[0]) for row in db.execute("SELECT id FROM rooms WHERE workspace=? ORDER BY updated DESC,id LIMIT ?", (WORKSPACE_ID, MAX_ROOMS)).fetchall()]
            return {"workspace": {"id": WORKSPACE_ID, "name": WORKSPACE_NAME, "durable": self.durable}, "me": me, "members": members, "rooms": rooms}

    def room_participants(self, actor, room_id, user_ids):
        """Resolve only nominated live-room users against current membership."""
        if type(user_ids) is not list or len(user_ids) > MAX_ROOM_PARTICIPANTS:
            raise TeamError("invalid_team_request")
        for user_id in user_ids:
            if user_id != OWNER_ID: identifier(user_id)
        with self._connection() as (db, key):
            self._principal(db, key, actor); self._room(db, key, room_id)
            participants = {}
            for user_id in dict.fromkeys(user_ids):
                if user_id == OWNER_ID:
                    participants[user_id] = owner_identity()["display_name"]
                    continue
                row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if row is None: continue
                try: member, _ = self._member(db, key, row)
                except TeamError as error:
                    if error.code == "team_identity_required": continue
                    raise
                if member["active"]: participants[user_id] = member["display_name"]
            return participants

    def create_room(self, actor, title):
        title = text(title, 120).strip()
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor)
            if db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] >= MAX_ROOMS: raise TeamError("team_limit")
            rid, created = str(uuid.uuid4()), _timestamp(self.clock())
            nonce, body = self._seal(key, {"title": title}, "room", rid, WORKSPACE_ID, principal["id"], created)
            self._reserve(db, len(body))
            db.execute("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?)", (rid, WORKSPACE_ID, principal["id"], created, created, 1, nonce, body))
            return {"room": self._room(db, key, rid)}

    def authorize_room(self, actor, room_id):
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor)
            return {"room": self._room(db, key, room_id), "me": principal}

    def _browser_binding(self, db, key, room_id):
        row = db.execute("SELECT * FROM browser_bindings WHERE room=?", (room_id,)).fetchone()
        if row is None: return None
        value = self._open(key, row["nonce"], row["body"], "browser", room_id, row["session_id"], row["generation"])
        return {"room_id": room_id, "session_id": row["session_id"], "generation": row["generation"], **value}

    def browser_binding(self, actor, room_id):
        with self._connection() as (db, key):
            self._principal(db, key, actor); self._room(db, key, room_id)
            return self._browser_binding(db, key, room_id)

    def reserve_browser(self, actor, room_id, session_id):
        """Reserve a trusted server-generated session before any workflow call."""
        identifier(session_id)
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor); self._room(db, key, room_id)
            prior = self._browser_binding(db, key, room_id)
            if prior and prior["status"] in {"opening", "active", "uncertain"}:
                raise TeamError("team_browser_conflict")
            if db.execute("SELECT 1 FROM browser_bindings WHERE session_id=?", (session_id,)).fetchone():
                raise TeamError("team_browser_conflict")
            generation = prior["generation"] + 1 if prior else 1
            created = _timestamp(self.clock())
            value = {"status": "opening", "created_by": principal["id"], "created_at": created, "updated_at": created}
            nonce, body = self._seal(key, value, "browser", room_id, session_id, generation)
            self._reserve(db, len(body))
            db.execute("INSERT INTO browser_bindings VALUES (?,?,?,?,?) ON CONFLICT(room) DO UPDATE SET session_id=excluded.session_id,generation=excluded.generation,nonce=excluded.nonce,body=excluded.body", (room_id, session_id, generation, nonce, body))
            return {"room_id": room_id, "session_id": session_id, "generation": generation, **value}

    def update_browser(self, actor, room_id, session_id, status):
        identifier(session_id)
        if status not in {"opening", "active", "uncertain", "expired", "closed"}:
            raise TeamError("invalid_team_request")
        with self._connection() as (db, key):
            self._principal(db, key, actor); self._room(db, key, room_id)
            prior = self._browser_binding(db, key, room_id)
            if not prior or prior["session_id"] != session_id:
                raise TeamError("team_browser_conflict")
            # Retired bindings cannot be revived by a late workflow result.
            if prior["status"] in {"closed", "expired"} and status != prior["status"]:
                raise TeamError("team_browser_conflict")
            value = {name: prior[name] for name in ("status", "created_by", "created_at", "updated_at")}
            value.update(status=status, updated_at=_timestamp(self.clock()))
            nonce, body = self._seal(key, value, "browser", room_id, session_id, prior["generation"])
            self._reserve(db, len(body))
            db.execute("UPDATE browser_bindings SET nonce=?,body=? WHERE room=? AND session_id=?", (nonce, body, room_id, session_id))
            return {"room_id": room_id, "session_id": session_id, "generation": prior["generation"], **value}

    def _event(self, db, key, room_id, actor, kind, data):
        if kind not in EVENT_TYPES: raise TeamError("invalid_team_request")
        row = db.execute("SELECT next_seq FROM rooms WHERE id=? AND workspace=?", (room_id, WORKSPACE_ID)).fetchone()
        if row is None: raise TeamError("team_not_found")
        seq = row[0]
        if seq > MAX_EVENTS: raise TeamError("team_limit")
        eid, created = str(uuid.uuid4()), _timestamp(self.clock())
        nonce, body = self._seal(key, {"display_name": actor["display_name"], "data": data}, "event", room_id, seq, eid, kind, actor["id"], created)
        self._reserve(db, len(body))
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (eid, room_id, seq, kind, actor["id"], created, nonce, body))
        db.execute("UPDATE rooms SET next_seq=?,updated=? WHERE id=?", (seq + 1, created, room_id))
        return {"seq": seq, "id": eid, "type": kind, "user_id": actor["id"], "display_name": actor["display_name"], "created_at": created, "data": data}

    def _read_event(self, key, row):
        value = self._open(key, row["nonce"], row["body"], "event", row["room"], row["seq"], row["id"], row["type"], row["user_id"], row["created"])
        return {"seq": row["seq"], "id": row["id"], "type": row["type"], "user_id": row["user_id"], "display_name": value["display_name"], "created_at": row["created"], "data": value["data"]}

    def _assistant(self, db, room_id):
        row = db.execute("SELECT id,status,error FROM runs WHERE room=? ORDER BY trigger_seq DESC LIMIT 1", (room_id,)).fetchone()
        if row is None: return {"status": "idle", "run_id": None}
        result = {"status": row["status"], "run_id": row["id"]}
        if row["error"]: result["error"] = ERRORS.get(row["error"], ERRORS["assistant_failed"])[1]
        return result

    def events(self, actor, room_id, after=None):
        if after is not None and (type(after) is not int or after < 0): raise TeamError("invalid_team_request")
        with self._connection() as (db, key):
            self._principal(db, key, actor); room = self._room(db, key, room_id)
            if after is None:
                rows = db.execute("SELECT * FROM events WHERE room=? ORDER BY seq DESC LIMIT 100", (room_id,)).fetchall()[::-1]
            else:
                rows = db.execute("SELECT * FROM events WHERE room=? AND seq>? ORDER BY seq LIMIT 100", (room_id, after)).fetchall()
            last = rows[-1]["seq"] if rows else (after or 0)
            return {"room": room, "events": [self._read_event(key, row) for row in rows], "last_seq": last,
                    "assistant": self._assistant(db, room_id), "documents": self._documents(db, key, room_id)}

    def _receipt(self, db, key, actor, room_id, client_id, fingerprint):
        found = db.execute("SELECT * FROM receipts WHERE room=? AND user_id=? AND client_id=?", (room_id, actor["id"], client_id)).fetchone()
        if found is None: return None
        if not hmac.compare_digest(found["fingerprint"], fingerprint): raise TeamError("idempotency_conflict")
        row = db.execute("SELECT * FROM events WHERE id=? AND room=?", (found["event_id"], room_id)).fetchone()
        if row is None: raise TeamError("team_unavailable")
        return {"event": self._read_event(key, row), "run_id": found["run_id"], "duplicate": True}

    def message_receipt(self, actor, room_id, data):
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor); self._room(db, key, room_id)
            return self._receipt(db, key, principal, room_id, data["client_message_id"], self._fingerprint(key, data))

    def append_message(self, actor, room_id, data):
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor); self._room(db, key, room_id)
            fingerprint = self._fingerprint(key, data)
            found = self._receipt(db, key, principal, room_id, data["client_message_id"], fingerprint)
            if found: return found
            run_id = None
            if data["ask_reign"]:
                now = self.clock()
                if db.execute("SELECT 1 FROM runs WHERE room=? AND status='running'", (room_id,)).fetchone(): raise TeamError("assistant_busy")
                if (db.execute("SELECT COUNT(*) FROM runs WHERE created>=?", (int(now // 86400) * 86400,)).fetchone()[0] >= MAX_DAILY_RUNS
                        or db.execute("SELECT COUNT(*) FROM runs WHERE created>?", (now - 60,)).fetchone()[0] >= MAX_MINUTE_RUNS):
                    raise TeamError("team_limit")
                run_id = str(uuid.uuid4())
            event = self._event(db, key, room_id, principal, "message", data)
            if run_id:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,NULL)", (run_id, room_id, principal["id"], "running", self.clock(), event["seq"]))
                self._event(db, key, room_id, {"id": "reign", "display_name": "REIGN"}, "assistant_started", {"run_id": run_id})
            db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?)", (room_id, principal["id"], data["client_message_id"], event["id"], run_id, fingerprint))
            return {"event": event, "run_id": run_id, "duplicate": False}

    def room_context(self, actor, room_id, run_id=None):
        with self._connection() as (db, key):
            self._principal(db, key, actor); room = self._room(db, key, room_id)
            before = MAX_EVENTS + 1
            if run_id:
                run = db.execute("SELECT trigger_seq FROM runs WHERE id=? AND room=? AND user_id=? AND status='running'", (run_id, room_id, actor["id"])).fetchone()
                if run is None: raise TeamError("team_not_found")
                before = run[0]
            rows = db.execute("SELECT * FROM events WHERE room=? AND seq<? AND type IN ('message','assistant_message') ORDER BY seq DESC LIMIT 24", (room_id, before)).fetchall()[::-1]
            messages = []
            for row in rows:
                event = self._read_event(key, row)
                messages.append({"role": "assistant" if event["type"] == "assistant_message" else "user", "text": event["data"]["text"],
                                 "user_id": event["user_id"], "display_name": event["display_name"], "created_at": event["created_at"],
                                 "input_source": event["data"].get("input_source", "text") if event["type"] == "message" else "assistant"})
            return {"workspace": {"id": WORKSPACE_ID, "name": WORKSPACE_NAME}, "room": room, "messages": messages,
                    "documents": self._documents(db, key, room_id), "run_id": run_id}

    def _document(self, db, key, room_id, document_id):
        row = db.execute("SELECT v.* FROM documents d JOIN document_versions v ON v.document=d.id AND v.room=d.room AND v.version=d.version WHERE d.id=? AND d.room=?", (document_id, room_id)).fetchone()
        if row is None: raise TeamError("team_not_found")
        value = self._open(key, row["nonce"], row["body"], "document", room_id, document_id, row["version"], row["user_id"], row["created"])
        return {"id": document_id, "title": value["title"], "content": value["content"], "version": row["version"], "updated_at": row["created"]}

    def _documents(self, db, key, room_id):
        result = []
        for row in db.execute("SELECT id FROM documents WHERE room=? ORDER BY updated DESC,id LIMIT ?", (room_id, MAX_DOCUMENTS)).fetchall():
            document = self._document(db, key, room_id, row[0]); document.pop("content")
            result.append(document)
        return result

    def document(self, actor, room_id, document_id):
        identifier(document_id)
        with self._connection() as (db, key):
            self._principal(db, key, actor); self._room(db, key, room_id)
            return {"document": self._document(db, key, room_id, document_id)}

    def _save_document(self, db, key, actor, room_id, title, content, client_id=None, document_id=None, expected_version=None):
        title = text(title, 120).strip(); content = text(content, 65536, bytes_max=65536, empty=True)
        stamp = _timestamp(self.clock())
        if document_id is None:
            identifier(client_id)
            fingerprint = self._fingerprint(key, {"title": title, "content": content})
            row = db.execute("SELECT * FROM documents WHERE room=? AND user_id=? AND client_id=?", (room_id, actor["id"], client_id)).fetchone()
            if row:
                if not hmac.compare_digest(row["fingerprint"], fingerprint): raise TeamError("idempotency_conflict")
                return self._document(db, key, room_id, row["id"])
            if db.execute("SELECT COUNT(*) FROM documents WHERE room=?", (room_id,)).fetchone()[0] >= MAX_DOCUMENTS: raise TeamError("team_limit")
            document_id, version = str(uuid.uuid4()), 1
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (document_id, room_id, actor["id"], client_id, fingerprint, version, stamp))
        else:
            identifier(document_id)
            if type(expected_version) is not int or expected_version < 1: raise TeamError("invalid_team_request")
            row = db.execute("SELECT version FROM documents WHERE id=? AND room=?", (document_id, room_id)).fetchone()
            if row is None: raise TeamError("team_not_found")
            if row[0] != expected_version: raise TeamError("document_conflict")
            version = expected_version + 1
            if version > MAX_VERSIONS: raise TeamError("team_limit")
            db.execute("UPDATE documents SET version=?,updated=? WHERE id=? AND room=?", (version, stamp, document_id, room_id))
        nonce, body = self._seal(key, {"title": title, "content": content}, "document", room_id, document_id, version, actor["id"], stamp)
        self._reserve(db, len(body))
        db.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)", (document_id, room_id, version, actor["id"], stamp, nonce, body))
        self._event(db, key, room_id, actor, "document_saved", {"document_id": document_id, "title": title, "version": version})
        return {"id": document_id, "title": title, "content": content, "version": version, "updated_at": stamp}

    def save_document(self, actor, room_id, title, content, *, client_id=None, document_id=None, expected_version=None):
        with self._connection() as (db, key):
            principal = self._principal(db, key, actor); self._room(db, key, room_id)
            return {"document": self._save_document(db, key, principal, room_id, title, content, client_id, document_id, expected_version)}

    def finish_run(self, actor, room_id, run_id, result):
        with self._connection() as (db, key):
            self._principal(db, key, actor); self._room(db, key, room_id)
            row = db.execute("SELECT status FROM runs WHERE id=? AND room=? AND user_id=?", (run_id, room_id, actor["id"])).fetchone()
            if row is None or row[0] != "running": raise TeamError("team_not_found")
            assistant = {"id": "reign", "display_name": "REIGN"}
            data = {"run_id": run_id, "text": result["text"]}
            for field in ("segments", "audio_id", "speech_status", "voice_session_id"):
                if field in result: data[field] = result[field]
            documents = []
            for document in result.get("documents", []):
                saved = self._save_document(db, key, assistant, room_id, document["title"], document["content"], str(uuid.uuid4()))
                documents.append(saved["id"])
            if documents: data["document_ids"] = documents
            event = self._event(db, key, room_id, assistant, "assistant_message", data)
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
            return event

    def fail_run(self, room_id, run_id, code="assistant_failed"):
        if code not in {"assistant_failed", "assistant_interrupted", "assistant_unavailable"}: code = "assistant_failed"
        with self._connection() as (db, key):
            row = db.execute("SELECT status FROM runs WHERE id=? AND room=?", (run_id, room_id)).fetchone()
            if row is None or row[0] != "running": return
            db.execute("UPDATE runs SET status='failed',error=? WHERE id=?", (code, run_id))
            try:
                self._event(db, key, room_id, {"id": "reign", "display_name": "REIGN"}, "assistant_error", {"run_id": run_id, "code": code, "detail": ERRORS[code][1]})
            except TeamError as error:
                if error.code != "team_limit": raise

    def initialize(self):
        with self._connection() as (db, _):
            running = [(row["room"], row["id"]) for row in db.execute("SELECT room,id FROM runs WHERE status='running'").fetchall()]
        for room_id, run_id in running:
            self.fail_run(room_id, run_id, "assistant_interrupted")
