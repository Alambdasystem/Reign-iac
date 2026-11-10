import unittest

from reign.sovereign_gateway import ConstitutionalGateway, GatewayDenied, GatewayRequest
from reign.sovereign_identity import GrantAuthority, IdentityDenied
from reign.sovereign_policy import Constitution
from reign.sovereign_registry import AgentRecord, AgentRegistry

NOW = 1700000000

class SovereignEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()
        for name, scopes, parent in (
            ("reign", ("web.read", "repo.read"), None),
            ("research", ("web.read",), "reign"),
        ):
            agent = AgentRecord(
                agent_id=name, tenant_id="tenant-a", owner_id="owner-a", name=name,
                harness="langgraph", provider="render", constitution_version="v1",
                parent_id=parent, capabilities=frozenset(scopes),
            )
            self.registry.create(agent, principal_id="owner-a")
            self.registry.transition("tenant-a", name, "active",
                                     principal_id="owner-a", expected_revision=1)
        self.authority = GrantAuthority(b"test-secret-is-strong-enough-for-fixture-only")
        self.calls = []
        self.gateway = ConstitutionalGateway(
            authority=self.authority, registry=self.registry,
            constitution=Constitution(
                version="v1",
                allowed_tools=frozenset({"web.read", "repo.read"}),
                blocked_tools=frozenset({"cloud.admin"}),
                allowed_hosts=frozenset({"alambda-forge.onrender.com"}),
            ),
            handlers={
                "web.read": lambda url: self.calls.append(("web.read", url)) or {"status":"ok"},
                "repo.read": lambda url: self.calls.append(("repo.read", url)) or {"status":"ok"},
            },
        )

    def grant(self, agent="research", owner="owner-a", tenant="tenant-a"):
        return self.authority.issue(tenant_id=tenant, owner_id=owner, agent_id=agent,
                                    now=NOW, ttl_seconds=60)

    def test_valid_read_only_and_reject_privilege_expansion(self):
        grant = self.grant()
        result = self.gateway.execute(GatewayRequest(grant, "web.read",
              "https://alambda-forge.onrender.com/path"), now=NOW+1)
        self.assertEqual(result, {"status":"ok"})
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(GatewayDenied):
            self.gateway.execute(GatewayRequest(grant,"repo.read"), now=NOW+1)
        with self.assertRaises(GatewayDenied):
            self.gateway.execute(GatewayRequest(grant,"cloud.admin"), now=NOW+1)
        self.assertEqual(len(self.calls),1)

    def test_expired_or_forged_or_wrong_audience_denied_before_effect(self):
        valid = self.grant()
        forged = valid[:-1] + ("A" if valid[-1] != "A" else "B")
        wrong = GrantAuthority(b"a-different-test-secret-not-production-1234")
        for token, moment in ((forged,NOW+1), (valid,NOW+60),
                              (wrong.issue(tenant_id="tenant-a", owner_id="owner-a",
                                           agent_id="research", now=NOW),NOW+1),
                              ("",NOW+1)):
            with self.subTest(token=token[:6]):
                with self.assertRaises(GatewayDenied):
                    self.gateway.execute(GatewayRequest(token,"web.read"), now=moment)
        self.assertEqual(self.calls,[])

    def test_tenant_impersonation_and_bad_destination_denied(self):
        with self.assertRaises(GatewayDenied):
            self.gateway.execute(GatewayRequest(self.grant(owner="owner-b"),"web.read"),now=NOW)
        for url in ("http://alambda-forge.onrender.com",
                    "https://alambda-forge.onrender.com.attacker.test",
                    "https://127.0.0.1/private"):
            with self.assertRaises(GatewayDenied):
                self.gateway.execute(GatewayRequest(self.grant(),"web.read",url),now=NOW)
        self.assertEqual(self.calls,[])

    def test_revoked_parent_ends_child_access_without_reissuing_grant(self):
        grant = self.grant()
        self.registry.transition("tenant-a","reign","suspended",
                                 principal_id="owner-a",expected_revision=2)
        with self.assertRaises(GatewayDenied):
            self.gateway.execute(GatewayRequest(grant,"web.read"),now=NOW+1)
        self.assertEqual(self.calls,[])

    def test_policy_missing_handler_and_version_mismatch_fail_closed(self):
        gateway = ConstitutionalGateway(authority=self.authority,registry=self.registry,
            constitution=Constitution("v2",frozenset({"web.read"}),frozenset(),
                                      frozenset({"alambda-forge.onrender.com"})),
            handlers={"web.read":lambda url:self.calls.append(url)})
        with self.assertRaises(GatewayDenied):
            gateway.execute(GatewayRequest(self.grant(),"web.read"),now=NOW+1)
        empty_gateway = ConstitutionalGateway(authority=self.authority,registry=self.registry,
            constitution=self.gateway.constitution,handlers={})
        with self.assertRaises(GatewayDenied):
            empty_gateway.execute(GatewayRequest(self.grant(),"web.read"),now=NOW+1)
        self.assertEqual(self.calls,[])

    def test_does_not_accept_bogus_grant_ttl_or_weak_secret(self):
        with self.assertRaises(ValueError):
            GrantAuthority(b"short")
        with self.assertRaises(ValueError):
            self.authority.issue(tenant_id="tenant-a",owner_id="owner-a",
                                 agent_id="research",ttl_seconds=301,now=NOW)
        with self.assertRaises(IdentityDenied):
            self.authority.verify(self.grant(),now=NOW-1)

if __name__ == "__main__":
    unittest.main()
