"""Authenticated, versioned AES-256-GCM for REIGN conversation records.

Versioned keyring format (secret manager only):
 REIGN_HISTORY_KEYS_JSON='{"active_version":2,"keys":{"1":"<base64url>","2":"<base64url>"}}'
Prior REIGN_HISTORY_KEY remains supported only when the keyring variable is ABSENT.
Never log/commit either secret. Older key versions MUST remain available for reads
until an independently reviewed and verified re-encryption/retirement process.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import uuid
from dataclasses import dataclass
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PLAINTEXT_BYTES = 65536
KEY_ENV = "REIGN_HISTORY_KEY"
KEYRING_ENV = "REIGN_HISTORY_KEYS_JSON"
MAX_KEY_VERSIONS = 16


def _uuid(value: str) -> str:
    return str(uuid.UUID(str(value)))


def _aad(owner_id: str, thread_id: str, record_id: str, purpose: str, version: int) -> bytes:
    if not owner_id or len(owner_id) > 128 or len(purpose) > 32 or not purpose:
        raise ValueError("Invalid history record identity")
    if purpose not in ("message", "checkpoint", "write"):
        raise ValueError("Invalid record purpose")
    if type(version) is not int or not (1 <= version <= 2147483647):
        raise ValueError("Invalid history encryption version")
    return json.dumps(["reign-history", version, owner_id, _uuid(thread_id),
                       record_id, purpose], separators=(",", ":")).encode("utf-8")


def _decode_key(encoded: str) -> bytes:
    if type(encoded) is not str or not encoded:
        raise ValueError("Invalid history key encoding")
    try:
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid history key encoding") from None
    if len(key) != 32:
        raise ValueError("REIGN history requires a 256-bit key")
    return key


@dataclass(frozen=True)
class SealedRecord:
    ciphertext: bytes
    nonce: bytes
    key_version: int = 1


class HistoryCipher:
    def __init__(self, key: bytes | None = None, *,
                 keys: Mapping[int, bytes] | None = None, active_version: int = 1):
        if (key is None) == (keys is None):
            raise ValueError("Specify exactly one history key source")
        if key is not None:
            keys = {1: key}
        if (type(active_version) is not int or active_version < 1
                or active_version > 2147483647 or
                not keys or len(keys) > MAX_KEY_VERSIONS):
            raise ValueError("Invalid history keyring")
        checked: dict[int, AESGCM] = {}
        seen_keys: set[bytes] = set()
        for version, material in keys.items():
            if type(version) is not int or not (1 <= version <= 2147483647):
                raise ValueError("Invalid history key version")
            if type(material) is not bytes or len(material) != 32:
                raise ValueError("REIGN history requires 256-bit keys")
            if material in seen_keys:
                raise ValueError("Each history key version must use distinct material")
            seen_keys.add(material)
            checked[version] = AESGCM(material)
        if active_version not in checked:
            raise ValueError("Active history encryption key missing")
        self._ciphers = checked
        self._active_version = active_version

    @classmethod
    def from_env(cls) -> "HistoryCipher":
        raw_ring = os.getenv(KEYRING_ENV)
        if raw_ring is not None:  # fail closed, even for empty or malformed keyrings
            try:
                doc = json.loads(raw_ring)
                if type(doc) is not dict or set(doc) != {"active_version", "keys"}:
                    raise ValueError("Invalid history keyring structure")
                active = doc["active_version"]
                raw_keys = doc["keys"]
                if (type(active) is not int or type(raw_keys) is not dict or
                        not (1 <= len(raw_keys) <= MAX_KEY_VERSIONS)):
                    raise ValueError("Invalid history keyring structure")
                keys: dict[int, bytes] = {}
                for version, raw in raw_keys.items():
                    if (type(version) is not str or not version.isascii() or
                            not version.isdecimal() or str(int(version)) != version):
                        raise ValueError("Invalid history key version")
                    keys[int(version)] = _decode_key(raw)
                return cls(keys=keys, active_version=active)
            except (TypeError, KeyError, ValueError, json.JSONDecodeError):
                raise ValueError("Invalid REIGN history keyring") from None
        return cls(_decode_key(os.getenv(KEY_ENV, "")))

    def seal(self, plaintext: bytes, *, owner_id: str, thread_id: str,
             record_id: str, purpose: str) -> SealedRecord:
        if type(plaintext) is not bytes or len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise ValueError("History record exceeds encrypted storage limit")
        version = self._active_version
        associated = _aad(owner_id, thread_id, record_id, purpose, version)
        nonce = os.urandom(12)
        return SealedRecord(self._ciphers[version].encrypt(nonce, plaintext, associated),
                            nonce, version)

    def open(self, record: SealedRecord, *, owner_id: str, thread_id: str,
             record_id: str, purpose: str) -> bytes:
        if (type(record.key_version) is not int or
                record.key_version not in self._ciphers or
                type(record.nonce) is not bytes or len(record.nonce) != 12 or
                type(record.ciphertext) is not bytes or
                len(record.ciphertext) > MAX_PLAINTEXT_BYTES + 16):
            raise ValueError("Invalid encrypted history record")
        associated = _aad(owner_id, thread_id, record_id, purpose, record.key_version)
        return self._ciphers[record.key_version].decrypt(
            record.nonce, record.ciphertext, associated)
