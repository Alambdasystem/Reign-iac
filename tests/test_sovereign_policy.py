import unittest

from reign.sovereign_policy import Constitution, decide
from reign.sovereign_registry import AgentRecord, AgentRegistry


class ConstitutionalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()
        self.constitution = Constitution(
            version="v1",
            allowed_tools=frozenset({"web.read", "repo.read"}),
            blocked_tools=frozenset({"cloud.admin"}),
            allowed_hosts=frozenset({"alambda-forge.onrender.com"}),
        )
        root = AgentRecord(agent_id="reign", tenant_id="tenant-a", owner_id="owner-a",
                           name="REIGN", harness="langgraph", provider="render",
                           constitution_version="v1",
                           capabilities=frozenset({"web.read", "repo.read", "cloud.admin"}))
        self.registry.create(root, principal_id="owner-a")
        self.registry.transition("tenant-a", "reign", "active",
                                 principal_id="owner-a", expected_revision=1)
        child = AgentRecord(agent_id="research", tenant_id="tenant-a", owner_id="owner-a",
                            name="Research", harness="langgraph", provider="render",
                            constitution_version="v1", parent_id="reign",
                            capabilities=frozenset({"web.read"}))
        self.registry.create(child, principal_id="owner-a")
        self.registry.transition("tenant-a", "research", "active",
                                 principal_id="owner-a", expected_revision=1)

    def check(self, tool="web.read", url=None, agent_id="research", owner="owner-a",
              constitution=None):
        return decide(self.registry, constitution or self.constitution,
                      tenant_id="tenant-a", agent_id=agent_id,
                      principal_id=owner, tool=tool, url=url)

    def test_allowed_tool_and_exact_https_host(self):
        result = self.check(url="https://alambda-forge.onrender.com/path")
        self.assertTrue(result.allowed)

    def test_unknown_and_blocked_tool_fail_closed(self):
        self.assertFalse(self.check(tool="new.tool").allowed)
        self.assertEqual(self.check(tool="cloud.admin", agent_id="reign").reason,
                         "constitutional_tool_denied")
        self.assertEqual(self.check(tool="repo.read").reason, "agent_scope_denied")

    def test_url_guard_rejects_escape_attempts(self):
        for url in (
            "http://alambda-forge.onrender.com/",
            "https://alambda-forge.onrender.com.evil.test",
            "https://evil.test/path",
            "https://alambda-forge.onrender.com:8443/",
            "https://user:pass@alambda-forge.onrender.com/",
            "https://alambda-forge.onrender.com/#fragment",
            "file:///etc/passwd",
            "https://127.0.0.1/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.check(url=url).allowed)

    def test_wrong_owner_and_unregistered_agent_denied(self):
        self.assertEqual(self.check(owner="owner-b").reason, "identity_or_registry_denied")
        self.assertEqual(self.check(agent_id="unknown").reason, "identity_or_registry_denied")

    def test_constitution_mismatch_denied(self):
        policy = Constitution("v2", frozenset({"web.read"}), frozenset(),
                              frozenset({"alambda-forge.onrender.com"}))
        self.assertEqual(self.check(constitution=policy).reason, "constitution_mismatch")

    def test_parent_suspension_revokes_child_policy_decisions(self):
        self.registry.transition("tenant-a", "reign", "suspended",
                                 principal_id="owner-a", expected_revision=2)
        self.assertFalse(self.check().allowed)

    def test_invalid_policy_conflict_rejected(self):
        with self.assertRaises(ValueError):
            Constitution("v1", frozenset({"web.read"}), frozenset({"web.read"}),
                         frozenset({"alambda-forge.onrender.com"}))


if __name__ == "__main__":
    unittest.main()
