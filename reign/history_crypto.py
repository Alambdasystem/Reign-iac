"""Authenticated encryption for future durable REIGN conversation records.

Not wired into production persistence. This key is independent of the website
credential-vault key and must be supplied only through secret management.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PLAINTEXT_BYTES = 65536
KEY_ENV = "REIGN_HISTORY_KEY"


def _uuid(value: str) -> str:
    return str(uuid.UUID(str(value)))


def _aad(owner_id: str, thread_id: str, record_id: str, purpose: str, version: int) -> bytes:
    if not owner_id or len(owner_id) > 128 or len(purpose) > 32 or not purpose:
        raise ValueError("Invalid history record identity")
    if purpose not in ("message", "checkpoint", "write"):
        raise ValueError("Invalid record purpose")
    if version != 1:
        raise ValueError("Unknown history encryption version")
    # JSON array encoding avoids separator ambiguity, binding every ciphertext
    # to its authenticated owner and original record identity.
    return json.dumps(["reign-history", version, owner_id, _uuid(thread_id),
                       record_id, purpose], separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class SealedRecord:
    ciphertext: bytes
    nonce: bytes
    key_version: int = 1


class HistoryCipher:
    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("REIGN history requires a distinct 256-bit key")
        self._cipher = AESGCM(key)

    @classmethod
    def from_env(cls) -> "HistoryCipher":
        raw = os.getenv(KEY_ENV, "")
        if not raw:
            raise ValueError("REIGN history key unavailable")
        try:
            key = base64.b64decode(raw, altchars=b"-_", validate=True)
        except (ValueError, base64.binascii.Error):
            raise ValueError("Invalid history key encoding") from None
        return cls(key)

    def seal(self, plaintext: bytes, *, owner_id: str, thread_id: str,
             record_id: str, purpose: str) -> SealedRecord:
        if type(plaintext) is not bytes or len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise ValueError("History record exceeds encrypted storage limit")
        associated = _aad(owner_id, thread_id, record_id, purpose, 1)
        nonce = os.urandom(12)
        return SealedRecord(self._cipher.encrypt(nonce, plaintext, associated), nonce)

    def open(self, record: SealedRecord, *, owner_id: str, thread_id: str,
             record_id: str, purpose: str) -> bytes:
        if (record.key_version != 1 or type(record.nonce) is not bytes
                or len(record.nonce) != 12 or type(record.ciphertext) is not bytes
                or len(record.ciphertext) > MAX_PLAINTEXT_BYTES + 16):
            raise ValueError("Invalid encrypted history record")
        associated = _aad(owner_id, thread_id, record_id, purpose, record.key_version)
        # Authentication failure is intentionally not converted to plaintext
        # or a partially recovered record; caller must fail closed.
        return self._cipher.decrypt(record.nonce, record.ciphertext, associated)
