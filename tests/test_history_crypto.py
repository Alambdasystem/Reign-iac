"""History encryption boundary tests; no database or provider connection."""
import base64
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from reign.history_crypto import HistoryCipher, SealedRecord


class HistoryCryptoTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.cipher = HistoryCipher(self.key)
        self.thread = str(uuid4())
        self.record = str(uuid4())
        self.fields = dict(owner_id="alice", thread_id=self.thread,
                           record_id=self.record, purpose="message")

    def test_encrypted_round_trip_and_random_nonce(self):
        one = self.cipher.seal(b"private fixture", **self.fields)
        two = self.cipher.seal(b"private fixture", **self.fields)
        self.assertNotEqual(one.nonce, two.nonce)
        self.assertNotEqual(one.ciphertext, two.ciphertext)
        self.assertNotIn(b"private fixture", one.ciphertext)
        self.assertEqual(self.cipher.open(one, **self.fields), b"private fixture")

    def test_cross_owner_or_thread_or_record_or_purpose_fails(self):
        value = self.cipher.seal(b"private fixture", **self.fields)
        for update in (dict(owner_id="bob"), dict(thread_id=str(uuid4())),
                       dict(record_id=str(uuid4())), dict(purpose="checkpoint")):
            with self.subTest(update=update), self.assertRaises(InvalidTag):
                self.cipher.open(value, **(self.fields | update))

    def test_tamper_and_wrong_key_fail_closed(self):
        value = self.cipher.seal(b"protected", **self.fields)
        altered = SealedRecord(value.ciphertext[:-1] + bytes([value.ciphertext[-1] ^ 1]), value.nonce)
        with self.assertRaises(InvalidTag):
            self.cipher.open(altered, **self.fields)
        with self.assertRaises(InvalidTag):
            HistoryCipher(bytes(reversed(self.key))).open(value, **self.fields)

    def test_rejects_unknown_version_and_excessive_bytes(self):
        with self.assertRaises(ValueError):
            self.cipher.seal(b"x"*65537, **self.fields)
        value = self.cipher.seal(b"x", **self.fields)
        with self.assertRaises(ValueError):
            self.cipher.open(SealedRecord(value.ciphertext, value.nonce, 2), **self.fields)
        with self.assertRaises(ValueError):
            self.cipher.open(SealedRecord(value.ciphertext, b"tiny"), **self.fields)

    def test_explicit_secret_key_required(self):
        with patch.dict(os.environ, {"REIGN_HISTORY_KEY": ""}):
            with self.assertRaises(ValueError):
                HistoryCipher.from_env()
        with patch.dict(os.environ, {"REIGN_HISTORY_KEY": base64.urlsafe_b64encode(self.key).decode()}):
            self.assertEqual(HistoryCipher.from_env().open(
                self.cipher.seal(b"ok", **self.fields), **self.fields), b"ok")
