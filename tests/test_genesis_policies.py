"""No-authority policy draft acceptance: store/preview only, never enforcement."""
import tempfile
import unittest
from pathlib import Path

from reign.genesis_pilot import GenesisPilot
from reign.genesis_policies import GenesisPolicies, PolicyConflict, PolicyMissing


class PolicyDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/"genesis.sqlite3")
        self.policy = GenesisPolicies(GenesisPilot(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def draft(self):
        return self.policy.create("readonly-policy", "Read Only",
                                  ["workspace_status"], ["browser_click"],
                                  ["alambda-forge.onrender.com"])

    def test_create_preview_update_restart_audit(self):
        first = self.draft()
        self.assertFalse(first["enforced"])
        self.assertEqual(first["revision"],1)
        accepted = self.policy.simulate("readonly-policy","workspace_status",
                                       "https://alambda-forge.onrender.com/")
        self.assertTrue(accepted["allowed"])
        self.assertTrue(accepted["simulation_only"])
        self.assertFalse(accepted["enforced"])
        self.assertEqual(self.policy.simulate("readonly-policy","browser_click")["allowed"],False)
        self.assertEqual(self.policy.simulate("readonly-policy","workspace_status",
                        "https://unapproved.example/")["reason"],"draft_destination_denied")
        changed = self.policy.update("readonly-policy", "Restricted", [],
                                     ["browser_click"], ["alambda-forge.onrender.com"], 1)
        self.assertEqual(changed["revision"],2)
        self.assertFalse(self.policy.simulate("readonly-policy","workspace_status")["allowed"])
        with self.assertRaises(PolicyConflict):
            self.policy.update("readonly-policy","Stale",[],[],[],1)
        reloaded = GenesisPolicies(GenesisPilot(self.path))
        self.assertEqual(reloaded.get("readonly-policy"),changed)
        self.assertEqual([x["action"] for x in reloaded.audit("readonly-policy")],
                         ["updated","created"])

    def test_deny_unsafe_policy_and_external_grants(self):
        bad = [
            (["browser_click"],[],[]),
            (["workspace_status"],["workspace_status"],[]),
            (["workspace_status"],[],["https://example.com/path"]),
            (["workspace_status"],[],["evil.com:443"]),
            (["workspace_status"],[],["../other"]),
        ]
        for allowed,blocked,hosts in bad:
            with self.subTest(allowed=allowed,blocked=blocked,hosts=hosts):
                with self.assertRaises(ValueError):
                    self.policy.create("restricted","Unsafe",allowed,blocked,hosts)
        self.assertEqual(self.policy.list(),[])
        self.draft()
        with self.assertRaises(PolicyConflict):
            self.draft()
        with self.assertRaises(PolicyMissing):
            self.policy.get("missing")
        with self.assertRaises(ValueError):
            self.policy.simulate("readonly-policy","shell_exec")
        self.assertFalse(self.policy.simulate("readonly-policy","workspace_status",
                    "https://alambda-forge.onrender.com.evil.net/")["allowed"])
