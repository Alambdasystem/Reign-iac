import unittest

from reign.computer_profiles import (
    ComputerProfile, ComputerSessionBinding, ConnectionKind,
)


class ComputerProfilesTests(unittest.TestCase):
    def test_managed_browser(self):
        ComputerProfile("p1", "owner", ConnectionKind.MANAGED_BROWSER).validate()

    def test_external_browser_requires_secret_reference(self):
        profile = ComputerProfile("p1", "owner", ConnectionKind.REMOTE_BROWSER,
                                  "wss://worker.example.com/connect", "secret://worker/p1")
        profile.validate()

    def test_rejects_credentials_in_url(self):
        for url in ("https://user:pass@worker.example.com",
                    "https://worker.example.com?token=abc",
                    "http://worker.example.com"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    ComputerProfile("p1", "owner", ConnectionKind.REMOTE_BROWSER,
                                    url, "secret://worker/p1").validate()

    def test_rejects_missing_secret(self):
        with self.assertRaises(ValueError):
            ComputerProfile("p1", "owner", ConnectionKind.REMOTE_DESKTOP,
                            "https://worker.example.com").validate()

    def test_session_binding_rejects_stale_epoch_and_other_worker(self):
        session = ComputerSessionBinding("s1", "p1", "owner", "worker-1", 4)
        self.assertTrue(session.accepts_input(owner_id="owner", worker_id="worker-1", epoch=4))
        self.assertFalse(session.accepts_input(owner_id="owner", worker_id="worker-1", epoch=3))
        self.assertFalse(session.accepts_input(owner_id="owner", worker_id="worker-2", epoch=4))
        self.assertFalse(session.accepts_input(owner_id="other", worker_id="worker-1", epoch=4))


if __name__ == "__main__":
    unittest.main()
