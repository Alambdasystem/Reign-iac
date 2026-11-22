"""Safe unit contracts for the opt-in private Ollama probe (no live network)."""
import os
import unittest
from unittest.mock import patch
from reign import ollama_tailnet_probe as probe


class OllamaProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_private_key_only_from_named_server_side_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ssh_private_key_not_configured"):
                probe._private_key()
        with patch.dict(os.environ, {"REIGN_OLLAMA_SSH_PRIVATE_KEY": "bad"}, clear=True):
            with self.assertRaisesRegex(ValueError, "invalid_private_key"):
                probe._private_key()
        fixture = "-----BEGIN OPENSSH PRIVATE KEY-----\\nsynthetic-only\\n-----END OPENSSH PRIVATE KEY-----"
        with patch.dict(os.environ, {"REIGN_OLLAMA_SSH_PRIVATE_KEY": fixture}, clear=True):
            self.assertEqual(probe._private_key().splitlines()[0], "-----BEGIN OPENSSH PRIVATE KEY-----")

    async def test_absent_userspace_daemon_cannot_touch_ssh_or_ollama(self):
        with patch.dict(os.environ, {"REIGN_TAILSCALE_ENABLED": "0"}, clear=True), \\
             patch.object(probe.subprocess, "Popen") as spawn:
            receipt = await probe.probe()
        self.assertEqual(receipt["error_code"], "tailscale_socket_unavailable")
        self.assertEqual(receipt["ssh"], "not_tested")
        self.assertEqual(receipt["inference"], "not_tested")
        spawn.assert_not_called()

    def test_startup_is_explicit_opt_in(self):
        with patch.dict(os.environ, {"REIGN_OLLAMA_PROBE_ENABLED": "0"}):
            self.assertIsNone(probe.schedule_startup_probe())


if __name__ == "__main__":
    unittest.main()
