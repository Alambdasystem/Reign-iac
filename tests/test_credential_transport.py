"""Synthetic tests for the private one-use credential delivery boundary."""
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from reign.credential_transport import context, open_credentials, redeem_credentials, seal_credentials


CHANNEL_KEY = base64.b64encode(bytes(range(32))).decode("ascii")
OTHER_KEY = base64.b64encode(b"z" * 32).decode("ascii")
WORKER_TOKEN = "synthetic-worker-authentication-token-only-8872"
CREDENTIALS = {"username": "synthetic-user@example.invalid", "password": "Synthetic-secret-password-only-7293"}
BINDING = context("synthetic-grant-reference", "11111111-1111-4111-8111-111111111111",
                  "synthetic-observation-1", 7, "https://alambda-forge.onrender.com")
SAFE_ERROR = "Credential transport is unavailable"


class CredentialTransportTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"CREDENTIAL_CHANNEL_KEY": CHANNEL_KEY, "COMPUTER_TOKEN": WORKER_TOKEN})
        env.start()
        self.addCleanup(env.stop)

    def assert_closed_failure(self, operation):
        with self.assertRaises(ValueError) as caught:
            operation()
        self.assertEqual(str(caught.exception), SAFE_ERROR)
        for value in (*CREDENTIALS.values(), CHANNEL_KEY, WORKER_TOKEN):
            self.assertNotIn(value, str(caught.exception))

    def test_round_trip_contains_no_plaintext_and_uses_fresh_nonces(self):
        first = seal_credentials(CREDENTIALS, BINDING)
        second = seal_credentials(CREDENTIALS, BINDING)
        self.assertEqual(set(first), {"version", "sealed"})
        self.assertEqual(first["version"], 1)
        self.assertNotEqual(first["sealed"], second["sealed"])
        raw_first = base64.urlsafe_b64decode(first["sealed"])
        raw_second = base64.urlsafe_b64decode(second["sealed"])
        self.assertNotEqual(raw_first[:12], raw_second[:12])
        for value in (*CREDENTIALS.values(), CHANNEL_KEY, WORKER_TOKEN):
            self.assertNotIn(value, json.dumps(first))
            self.assertNotIn(value.encode(), raw_first)
        self.assertEqual(open_credentials(first, BINDING), CREDENTIALS)
        self.assertEqual(open_credentials(second, BINDING), CREDENTIALS)

    def test_every_grant_and_browser_binding_is_authenticated(self):
        bundle = seal_credentials(CREDENTIALS, BINDING)
        altered = {"grant_id": "another-grant", "session_id": "22222222-2222-4222-8222-222222222222",
                   "observation_id": "another-observation", "epoch": 8, "origin": "https://other.example"}
        for name, value in altered.items():
            with self.subTest(field=name):
                self.assert_closed_failure(lambda name=name, value=value:
                    open_credentials(bundle, dict(BINDING, **{name: value})))

    def test_nonce_ciphertext_and_authentication_tag_tampering_fail_closed(self):
        bundle = seal_credentials(CREDENTIALS, BINDING)
        raw = base64.urlsafe_b64decode(bundle["sealed"])
        for offset in (0, 15, len(raw) - 1):
            with self.subTest(offset=offset):
                changed = bytearray(raw)
                changed[offset] ^= 1
                tampered = {"version": 1, "sealed": base64.urlsafe_b64encode(changed).decode("ascii")}
                self.assert_closed_failure(lambda: open_credentials(tampered, BINDING))

    def test_maximum_vault_unicode_values_fit_transport_without_changing_whitespace(self):
        credentials = {"username": " " + "🙂" * 318 + " ", "password": " " + "🔐" * 1022 + " "}
        bundle = seal_credentials(credentials, BINDING)
        self.assertLessEqual(len(base64.urlsafe_b64decode(bundle["sealed"])), 12000)
        self.assertEqual(open_credentials(bundle, BINDING), credentials)

    def test_missing_or_invalid_channel_key_never_falls_back_to_worker_token(self):
        for key in ("", "invalid-base64", base64.b64encode(b"short").decode(), " " + CHANNEL_KEY):
            with self.subTest(length=len(key)), patch.dict(os.environ, {"CREDENTIAL_CHANNEL_KEY": key}):
                self.assert_closed_failure(lambda: seal_credentials(CREDENTIALS, BINDING))

    def test_different_channel_key_cannot_decrypt_but_auth_token_rotation_does_not_change_key(self):
        bundle = seal_credentials(CREDENTIALS, BINDING)
        with patch.dict(os.environ, {"CREDENTIAL_CHANNEL_KEY": OTHER_KEY}):
            self.assert_closed_failure(lambda: open_credentials(bundle, BINDING))
        with patch.dict(os.environ, {"COMPUTER_TOKEN": "different-synthetic-authentication-token-9321"}):
            self.assertEqual(open_credentials(bundle, BINDING), CREDENTIALS)

    def test_malformed_or_oversized_bundles_return_only_safe_error(self):
        cases = ({}, {"version": 2, "sealed": "invalid"}, {"version": 1, "sealed": CREDENTIALS["password"]},
                 {"version": 1, "sealed": base64.urlsafe_b64encode(b"x" * 27).decode()},
                 {"version": 1, "sealed": base64.urlsafe_b64encode(b"x" * 12001).decode()},
                 {"version": 1, "sealed": None})
        for bundle in cases:
            with self.subTest(keys=list(bundle)):
                self.assert_closed_failure(lambda: open_credentials(bundle, BINDING))

    def test_seal_refuses_unexpected_fields_or_unbounded_credentials(self):
        for credentials in ({**CREDENTIALS, "token": "synthetic"}, {"username": "", "password": "synthetic"},
                            {"username": "synthetic", "password": "x" * 2049}):
            self.assert_closed_failure(lambda: seal_credentials(credentials, BINDING))


class CredentialRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"CREDENTIAL_CHANNEL_KEY": CHANNEL_KEY, "COMPUTER_TOKEN": WORKER_TOKEN,
                                     "CORE_URL": "http://synthetic-core.internal:10000"})
        env.start()
        self.addCleanup(env.stop)

    async def redeem(self):
        return await redeem_credentials(BINDING["grant_id"], BINDING["session_id"],
            BINDING["observation_id"], BINDING["epoch"], BINDING["origin"])

    def client(self, response=None, error=None):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        client.post.side_effect = error
        return client

    async def test_private_request_uses_worker_auth_only_and_cannot_follow_redirects(self):
        bundle = seal_credentials(CREDENTIALS, BINDING)
        client = self.client(httpx.Response(200, json=bundle))
        with patch("reign.credential_transport.httpx.AsyncClient", return_value=client) as factory:
            self.assertEqual(await self.redeem(), CREDENTIALS)
        factory.assert_called_once_with(timeout=10, trust_env=False, follow_redirects=False)
        client.post.assert_awaited_once_with("http://synthetic-core.internal:10000/api/vault/grants/redeem",
            headers={"Authorization": "Bearer " + WORKER_TOKEN}, json=BINDING)
        serialized = repr(client.post.await_args)
        for value in (CHANNEL_KEY, *CREDENTIALS.values()):
            self.assertNotIn(value, serialized)

    async def test_redirect_and_auth_failure_never_replay_or_expose_response_body(self):
        for status in (302, 401, 500):
            with self.subTest(status=status):
                response = httpx.Response(status, headers={"Location": "https://untrusted.example/"},
                                          text=CREDENTIALS["password"])
                client = self.client(response)
                with patch("reign.credential_transport.httpx.AsyncClient", return_value=client) as factory:
                    with self.assertRaisesRegex(ValueError, "^" + SAFE_ERROR + "$"):
                        await self.redeem()
                self.assertFalse(factory.call_args.kwargs["follow_redirects"])
                self.assertEqual(client.post.await_count, 1)

    async def test_network_errors_and_oversized_responses_are_closed_without_retries(self):
        clients = (self.client(error=ConnectionError(CREDENTIALS["password"])),
                   self.client(httpx.Response(200, content=b"x" * 20001)))
        for client in clients:
            with patch("reign.credential_transport.httpx.AsyncClient", return_value=client):
                with self.assertRaises(ValueError) as caught:
                    await self.redeem()
            self.assertEqual(str(caught.exception), SAFE_ERROR)
            self.assertEqual(client.post.await_count, 1)


if __name__ == "__main__":
    unittest.main()
