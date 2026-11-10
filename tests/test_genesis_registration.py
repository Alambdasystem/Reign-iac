import unittest

from reign.genesis_registration import GenesisRegistration, GenesisRequest
from reign.sovereign_policy import Constitution
from reign.sovereign_registry import AgentRecord, AgentRegistry, RegistryDenied


class GenesisRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()
        self.policy = Constitution(
            version="v1",
            allowed_tools=frozenset({"web.read", "repo.read"}),
            blocked_tools=frozenset({"cloud.admin"}),
            allowed_hosts=frozenset({"alambda-forge.onrender.com"}))
        self.genesis = GenesisRegistration(self.registry, self.policy)
        self.subject = dict(tenant_id="tenant-a", owner_id="owner-a",
                            principal_id="owner-a")

    def request(self, capabilities=frozenset({"web.read"}), parent=None):
        return GenesisRequest(agent_id="research", name="Research assistant",
                              harness="langgraph", provider="render",
                              capabilities=capabilities, parent_id=parent)

    def test_preview_does_not_create_agent_or_resource(self):
        result = self.genesis.preview(self.request(), **self.subject)
        self.assertEqual(result.state, "draft")
        self.assertEqual(result.allowed_capabilities, ("web.read",))
        self.assertEqual(result.rejected_capabilities, ())
        self.assertTrue(result.needs_approval)
        self.assertFalse(result.provider_provisioned)
        self.assertEqual(self.registry.list_owned("tenant-a", principal_id="owner-a"), ())

    def test_registration_is_draft_and_cannot_execute(self):
        created = self.genesis.register_draft(self.request(), **self.subject)
        self.assertEqual(created.state, "draft")
        self.assertFalse(self.registry.permitted("tenant-a", "research", "web.read",
                                                principal_id="owner-a"))
        decision = self.genesis.guardrail_preview(
            tenant_id="tenant-a", agent_id="research", principal_id="owner-a",
            tool="web.read", url="https://alambda-forge.onrender.com/")
        self.assertFalse(decision.allowed)

    def test_blocked_privilege_never_registers(self):
        request = self.request(frozenset({"web.read", "cloud.admin"}))
        preview = self.genesis.preview(request, **self.subject)
        self.assertEqual(preview.rejected_capabilities, ("cloud.admin",))
        with self.assertRaises(RegistryDenied):
            self.genesis.register_draft(request, **self.subject)
        self.assertEqual(self.registry.list_owned("tenant-a", principal_id="owner-a"), ())

    def test_unverified_or_cross_owner_registration_denied(self):
        with self.assertRaises(RegistryDenied):
            self.genesis.register_draft(self.request(),
                tenant_id="tenant-a", owner_id="owner-a", principal_id="owner-b")

    def test_parent_must_be_active_and_child_permissions_must_attenuate(self):
        parent = AgentRecord(
            agent_id="reign", tenant_id="tenant-a", owner_id="owner-a",
            name="REIGN", harness="langgraph", provider="render",
            constitution_version="v1", capabilities=frozenset({"web.read"}))
        self.registry.create(parent, principal_id="owner-a")
        with self.assertRaises(RegistryDenied):
            self.genesis.register_draft(self.request(parent="reign"), **self.subject)
        self.registry.transition("tenant-a", "reign", "active",
                                 principal_id="owner-a", expected_revision=1)
        with self.assertRaises(RegistryDenied):
            self.genesis.register_draft(
                self.request(frozenset({"repo.read"}), parent="reign"), **self.subject)
        child = self.genesis.register_draft(self.request(parent="reign"), **self.subject)
        self.assertEqual(child.parent_id, "reign")
        self.assertEqual(child.state, "draft")

    def test_duplicate_draft_rejected(self):
        self.genesis.register_draft(self.request(), **self.subject)
        with self.assertRaises(RegistryDenied):
            self.genesis.register_draft(self.request(), **self.subject)
