import unittest

from reign.sovereign_registry import AgentRecord, AgentRegistry, RegistryDenied


def record(agent_id, *, tenant="tenant-a", owner="owner-a", parent=None,
           capabilities=("web.read",), constitution="v1", state="draft"):
    return AgentRecord(
        agent_id=agent_id, tenant_id=tenant, owner_id=owner,
        name=agent_id, harness="langgraph", provider="render",
        constitution_version=constitution, capabilities=frozenset(capabilities),
        parent_id=parent, state=state,
    )


class SovereignRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()

    def activate(self, r):
        self.registry.create(r, principal_id=r.owner_id)
        return self.registry.transition(r.tenant_id, r.agent_id, "active",
            principal_id=r.owner_id, expected_revision=1)

    def test_two_agents_isolated_and_default_deny(self):
        self.activate(record("reign", capabilities=("web.read", "repo.write")))
        self.activate(record("research", capabilities=("web.read",)))
        self.assertTrue(self.registry.permitted("tenant-a", "reign", "repo.write", principal_id="owner-a"))
        self.assertFalse(self.registry.permitted("tenant-a", "research", "repo.write", principal_id="owner-a"))
        with self.assertRaises(RegistryDenied):
            self.registry.permitted("tenant-a", "research", "web.read", principal_id="owner-b")

    def test_unowned_access_and_cross_tenant_rejected(self):
        self.activate(record("reign"))
        with self.assertRaises(RegistryDenied):
            self.registry.get("tenant-a", "reign", principal_id="owner-b")
        with self.assertRaises(RegistryDenied):
            self.registry.get("tenant-b", "reign", principal_id="owner-a")
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("other"), principal_id="owner-b")
        self.assertEqual(self.registry.list_owned("tenant-a", principal_id="owner-b"), ())

    def test_duplicate_and_invalid_registration(self):
        self.registry.create(record("reign"), principal_id="owner-a")
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("reign"), principal_id="owner-a")
        with self.assertRaises(ValueError):
            record("bad space")
        with self.assertRaises(ValueError):
            record("other", capabilities=("*",))
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("other", state="active"), principal_id="owner-a")

    def test_child_cannot_escalate_or_change_constitution(self):
        self.activate(record("reign", capabilities=("web.read", "repo.write")))
        child = record("research", parent="reign", capabilities=("web.read",))
        self.activate(child)
        self.assertTrue(self.registry.permitted("tenant-a", "research", "web.read", principal_id="owner-a"))
        self.assertFalse(self.registry.permitted("tenant-a", "research", "repo.write", principal_id="owner-a"))
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("overprivileged", parent="reign",
                capabilities=("cloud.admin",)), principal_id="owner-a")
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("newversion", parent="reign",
                constitution="v2"), principal_id="owner-a")
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("selfparent", parent="selfparent"), principal_id="owner-a")

    def test_revocation_propagates_through_parent_and_retired_is_final(self):
        parent = self.activate(record("reign", capabilities=("web.read",)))
        child = self.activate(record("research", parent="reign"))
        self.registry.transition("tenant-a", "reign", "suspended",
            principal_id="owner-a", expected_revision=parent.revision)
        self.assertFalse(self.registry.permitted("tenant-a", "research", "web.read", principal_id="owner-a"))
        with self.assertRaises(RegistryDenied):
            self.registry.transition("tenant-a", "research", "retired",
                principal_id="owner-a", expected_revision=child.revision + 5)
        retired = self.registry.transition("tenant-a", "research", "retired",
            principal_id="owner-a", expected_revision=child.revision)
        with self.assertRaises(RegistryDenied):
            self.registry.transition("tenant-a", "research", "active",
                principal_id="owner-a", expected_revision=retired.revision)

    def test_draft_has_no_execution_rights(self):
        self.registry.create(record("research"), principal_id="owner-a")
        self.assertFalse(self.registry.permitted("tenant-a", "research", "web.read", principal_id="owner-a"))

    def test_inherited_cross_owner_not_allowed(self):
        self.activate(record("reign", capabilities=("web.read",)))
        with self.assertRaises(RegistryDenied):
            self.registry.create(record("research", owner="owner-b",
                parent="reign"), principal_id="owner-b")


if __name__ == "__main__":
    unittest.main()
