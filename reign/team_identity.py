"""Short-lived AX actor assertions, separate from service authorization."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

OWNER_ID = "reign-owner"
ACTOR_HEADER = "X-Reign-Team-Actor"
ACTOR_TTL = 60
AUDIENCE = "reign-team-core"


class TeamIdentityError(ValueError):
    pass


def _key():
    value = os.environ.get("CORE_TOKEN", "")
    if len(value) < 32:
        raise TeamIdentityError("Team authentication unavailable")
    return hmac.digest(value.encode("utf-8"), b"reign/team-actor/v1", "sha256")


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value):
    if not isinstance(value, str) or not value or len(value) > 1800:
        raise TeamIdentityError("Invalid team identity")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise TeamIdentityError("Invalid team identity") from None
    if _b64(raw) != value:
        raise TeamIdentityError("Invalid team identity")
    return raw


def _subject(user_id, role, revision):
    if type(revision) is not int or not 0 <= revision <= 2147483647:
        raise TeamIdentityError("Invalid team identity")
    if role == "owner":
        if user_id != OWNER_ID or revision != 0:
            raise TeamIdentityError("Invalid team identity")
    elif role == "employee":
        try:
            if type(user_id) is not str or str(uuid.UUID(user_id)) != user_id or revision < 1:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise TeamIdentityError("Invalid team identity") from None
    else:
        raise TeamIdentityError("Invalid team identity")


def issue_actor(user_id, role, credential_revision, *, now=None):
    _subject(user_id, role, credential_revision)
    stamp = int(time.time() if now is None else now)
    payload = {"v": 1, "iss": "reign-ax", "aud": AUDIENCE, "sub": user_id,
               "role": role, "rev": credential_revision, "iat": stamp, "exp": stamp + ACTOR_TTL}
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"))
    return encoded + "." + _b64(hmac.digest(_key(), encoded.encode("ascii"), "sha256"))


def verify_actor(token, *, now=None):
    if type(token) is not str or len(token) > 2048 or token.count(".") != 1:
        raise TeamIdentityError("Invalid team identity")
    body, signature = token.split(".")
    supplied = _decode(signature)
    try:
        expected = hmac.digest(_key(), body.encode("ascii"), "sha256")
    except UnicodeError:
        raise TeamIdentityError("Invalid team identity") from None
    if not hmac.compare_digest(supplied, expected):
        raise TeamIdentityError("Invalid team identity")
    try:
        claims = json.loads(_decode(body))
        if (type(claims) is not dict or set(claims) != {"v", "iss", "aud", "sub", "role", "rev", "iat", "exp"}
                or claims["v"] != 1 or claims["iss"] != "reign-ax" or claims["aud"] != AUDIENCE
                or type(claims["iat"]) is not int or type(claims["exp"]) is not int):
            raise ValueError()
        stamp = time.time() if now is None else now
        if not claims["iat"] <= stamp < claims["exp"] or claims["exp"] - claims["iat"] != ACTOR_TTL:
            raise ValueError()
        _subject(claims["sub"], claims["role"], claims["rev"])
    except (ValueError, TypeError, KeyError):
        raise TeamIdentityError("Invalid team identity") from None
    return claims
