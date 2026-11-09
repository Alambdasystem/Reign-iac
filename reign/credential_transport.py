"""Single-use, context-bound credential delivery to the trusted Computer.

Only a grant reference enters MCP/Temporal. The private Core response is sealed
for the existing authenticated worker; neither ciphertext nor plaintext becomes
a browser tool result. This module provides no model-facing secret getter.
"""
import base64
import json
import os

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def context(grant_id, sid, observation_id, epoch, origin):
    return {"grant_id": grant_id, "session_id": sid, "observation_id": observation_id,
            "epoch": epoch, "origin": origin}


def _key():
    # This dedicated key is never sent as an HTTP bearer token. Using the
    # authentication token here would expose the sealing key on private HTTP.
    try:
        key = base64.b64decode(os.getenv("CREDENTIAL_CHANNEL_KEY", ""), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ValueError("Credential transport is unavailable") from None
    if len(key) != 32:
        raise ValueError("Credential transport is unavailable")
    return key


def _aad(binding):
    return json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()


def seal_credentials(credentials, binding):
    if (set(credentials) != {"username", "password"}
            or not all(isinstance(value, str) and 0 < len(value) <= 2048
                       for value in credentials.values())):
        raise ValueError("Credential transport is unavailable")
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(nonce,
        json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode(), _aad(binding))
    return {"version": 1, "sealed": base64.urlsafe_b64encode(nonce + encrypted).decode()}


def open_credentials(bundle, binding):
    try:
        if bundle.get("version") != 1 or not isinstance(bundle.get("sealed"), str):
            raise ValueError()
        raw = base64.b64decode(bundle["sealed"].encode(), altchars=b"-_", validate=True)
        if not 28 <= len(raw) <= 12000:
            raise ValueError()
        data = json.loads(AESGCM(_key()).decrypt(raw[:12], raw[12:], _aad(binding)))
        if (set(data) != {"username", "password"}
                or not all(isinstance(value, str) and 0 < len(value) <= 2048
                           for value in data.values())):
            raise ValueError()
        return data
    except Exception:
        raise ValueError("Credential transport is unavailable") from None


async def redeem_credentials(grant_id, sid, observation_id, epoch, origin):
    """Called only by the Computer while it holds its session/control lock."""
    binding = context(grant_id, sid, observation_id, epoch, origin)
    base = os.getenv("CORE_URL") or "http://" + os.getenv("CORE_HOST", "core") + ":10000"
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = await client.post(base.rstrip("/") + "/api/vault/grants/redeem",
                headers={"Authorization": "Bearer " + os.environ["COMPUTER_TOKEN"]},
                json=binding)
        if response.status_code != 200 or len(response.content) > 20000:
            raise ValueError()
        return open_credentials(response.json(), binding)
    except Exception:
        raise ValueError("Credential transport is unavailable") from None
