"""Bounded named-avatar authentication for the single-instance AX pilot.

Credentials live in Render-managed environment configuration. Sessions live in
this process and require sign-in after a restart. A shared session store is
required before AX is scaled to multiple processes or instances.
"""

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import secrets
import time


PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 3600
USERNAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_PASSWORD_BYTES = 1024
MAX_ACCOUNTS = 32
MAX_SESSIONS_PER_USER = 8
MAX_SESSIONS = 512
LOGIN_WINDOW_SECONDS = 60
LOGIN_PER_CLIENT = 20
LOGIN_TOTAL = 60


class AuthConfigurationError(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidSession(Exception):
    pass


class LoginThrottled(Exception):
    pass


def _encoded(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decoded(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise AuthConfigurationError()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise AuthConfigurationError() from None
    if len(raw) != length or _encoded(raw) != value:
        raise AuthConfigurationError()
    return raw


def _password_bytes(value):
    if not isinstance(value, str):
        raise InvalidCredentials()
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raise InvalidCredentials() from None
    if not 1 <= len(raw) <= MAX_PASSWORD_BYTES or b"\x00" in raw:
        raise InvalidCredentials()
    return raw


def _verifier(value):
    if not isinstance(value, str) or len(value) > 150:
        raise AuthConfigurationError()
    parts = value.split("$")
    if len(parts) != 4 or parts[:2] != ["pbkdf2_sha256", str(PASSWORD_ITERATIONS)]:
        raise AuthConfigurationError()
    return _decoded(parts[2], 16), _decoded(parts[3], 32)


def hash_password(password):
    """Return a salted verifier; never log or persist the supplied password."""
    raw = _password_bytes(password)
    if len(raw) < 15:
        raise ValueError("Use a password of at least 15 UTF-8 bytes")
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", raw, salt, PASSWORD_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_encoded(salt)}${_encoded(key)}"


def _accounts():
    raw = os.environ.get("AX_AVATAR_USERS_JSON", "")
    if not raw:
        return {}
    if len(raw) > 32768:
        raise AuthConfigurationError()
    try:
        accounts = json.loads(raw)
    except (ValueError, UnicodeError):
        raise AuthConfigurationError() from None
    if not isinstance(accounts, dict) or len(accounts) > MAX_ACCOUNTS:
        raise AuthConfigurationError()
    for username, account in accounts.items():
        if (not isinstance(username, str) or not USERNAME.fullmatch(username)
                or username in ("admin", "owner") or not isinstance(account, dict)
                or set(account) != {"display_name", "password_hash"}):
            raise AuthConfigurationError()
        display = account["display_name"]
        if (not isinstance(display, str) or not display.strip() or len(display) > 80
                or any(ord(character) < 32 for character in display)):
            raise AuthConfigurationError()
        _verifier(account["password_hash"])
    return accounts


@dataclass(frozen=True)
class Identity:
    username: str
    display_name: str
    role: str
    revision: str
    user_id: str = ""


def employee_candidate(username, password):
    """Route only valid, non-local names to the private employee directory."""
    _password_bytes(password)
    if not isinstance(username, str) or len(username) > 64:
        raise InvalidCredentials()
    name = username.strip().lower()
    if name in ("", "owner", "admin"):
        return False
    if not USERNAME.fullmatch(name):
        raise InvalidCredentials()
    return name not in _accounts()


def reserved_username(username):
    return username in {"", "owner", "admin"} or username in _accounts()


def identity_for(username, role):
    """Resolve current configuration, so removal/rotation invalidates sessions."""
    if role == "owner" and username == "admin":
        password = os.environ.get("AX_ADMIN_PASSWORD", "")
        if len(password) < 32:
            raise AuthConfigurationError()
        revision = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return Identity("admin", "Owner", "owner", revision)
    if role != "avatar":
        raise InvalidSession()
    account = _accounts().get(username)
    if account is None:
        raise InvalidSession()
    revision = hashlib.sha256(account["password_hash"].encode("ascii")).hexdigest()
    return Identity(username, account["display_name"].strip(), "avatar", revision)


def authenticate(username, password):
    raw = _password_bytes(password)
    if not isinstance(username, str) or len(username) > 64:
        raise InvalidCredentials()
    username = username.strip().lower()
    if username in ("", "admin", "owner"):
        identity = identity_for("admin", "owner")
        if not hmac.compare_digest(raw, os.environ["AX_ADMIN_PASSWORD"].encode("utf-8")):
            raise InvalidCredentials()
        return identity
    if not USERNAME.fullmatch(username):
        raise InvalidCredentials()
    account = _accounts().get(username)
    # Unknown users consume the same bounded KDF work as a wrong named password.
    salt, expected = _verifier(account["password_hash"]) if account else (bytes(16), bytes(32))
    actual = hashlib.pbkdf2_hmac("sha256", raw, salt, PASSWORD_ITERATIONS, dklen=32)
    if account is None or not hmac.compare_digest(actual, expected):
        raise InvalidCredentials()
    # Keep the revision of the exact verifier checked above. The HTTP handler
    # compares it with current configuration after the KDF finishes.
    revision = hashlib.sha256(account["password_hash"].encode("ascii")).hexdigest()
    return Identity(username, account["display_name"].strip(), "avatar", revision)


@dataclass(frozen=True)
class Session:
    nonce: str
    identity: Identity
    csrf_hash: str
    expires: float


class AuthState:
    def __init__(self):
        self.boot = secrets.token_urlsafe(32)
        self.sessions = {}
        self.attempts = deque()
        self.pending_logins = 0

    def admit_login(self, client):
        now = time.monotonic()
        while self.attempts and self.attempts[0][0] <= now - LOGIN_WINDOW_SECONDS:
            self.attempts.popleft()
        client = hashlib.sha256(client.encode("utf-8")).digest()
        if (self.pending_logins >= 2 or len(self.attempts) >= LOGIN_TOTAL
                or sum(key == client for _, key in self.attempts) >= LOGIN_PER_CLIENT):
            raise LoginThrottled()
        self.attempts.append((now, client))

    def _prune(self):
        now = time.monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}

    def create(self, identity):
        self._prune()
        own = [key for key, session in self.sessions.items()
               if session.identity.username == identity.username]
        for key in own[:max(0, len(own) - MAX_SESSIONS_PER_USER + 1)]:
            self.sessions.pop(key, None)
        if len(self.sessions) >= MAX_SESSIONS:
            raise LoginThrottled()
        nonce, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        session = Session(nonce, identity, hashlib.sha256(csrf.encode("ascii")).hexdigest(),
                          time.monotonic() + SESSION_SECONDS)
        self.sessions[nonce] = session
        payload = {"v": 2, "boot": self.boot, "nonce": nonce,
                   "username": identity.username, "role": identity.role}
        return payload, csrf, session

    def resolve(self, payload):
        if (not isinstance(payload, dict)
                or set(payload) != {"v", "boot", "nonce", "username", "role"}
                or payload["v"] != 2 or payload["boot"] != self.boot
                or not isinstance(payload["nonce"], str) or not TOKEN.fullmatch(payload["nonce"])):
            raise InvalidSession()
        self._prune()
        session = self.sessions.get(payload["nonce"])
        if (session is None or payload["username"] != session.identity.username
                or payload["role"] != session.identity.role):
            raise InvalidSession()
        # Employee membership is authoritative in Core. AX checks it on page and
        # profile reads; Core rechecks every signed team request, including after
        # long polls. The local registry still binds role, nonce and expiry.
        if session.identity.role == "employee":
            if not session.identity.user_id:
                raise InvalidSession()
            return session
        current = identity_for(session.identity.username, session.identity.role)
        if not hmac.compare_digest(current.revision, session.identity.revision):
            self.sessions.pop(session.nonce, None)
            raise InvalidSession()
        return session

    def revoke(self, session):
        self.sessions.pop(session.nonce, None)


def profile(session):
    identity = session.identity
    owner = identity.role == "owner"
    return {"authenticated": True, "username": identity.username,
            "display_name": identity.display_name, "role": identity.role,
            "landing_path": "/" if owner else "/team" if identity.role == "employee" else "/avatar-lab",
            "capabilities": {"avatar_lab": True, "managed_browser": owner,
                             "external_computer": False, "voice": owner, "ai_chat": owner}}


def valid_csrf(session, supplied, cookie):
    if (not isinstance(supplied, str) or not TOKEN.fullmatch(supplied)
            or not isinstance(cookie, str) or not TOKEN.fullmatch(cookie)
            or not hmac.compare_digest(supplied, cookie)):
        return False
    return hmac.compare_digest(hashlib.sha256(supplied.encode("ascii")).hexdigest(), session.csrf_hash)
