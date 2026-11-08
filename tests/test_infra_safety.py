import copy
import unittest

from scripts.check_plan import validate
from scripts.preflight import check


class PlanPolicyTests(unittest.TestCase):
    def plan(self, **updates):
        service = {"name": "reign-core-pilot", "region": "oregon", "num_instances": 1, "plan": "starter"}
        service.update(updates)
        return {"planned_values": {}, "resource_changes": [{
            "address": 'render_private_service.core["pilot"]',
            "change": {"actions": ["create"], "after": service},
        }]}

    def test_new_pilot_is_allowed(self):
        self.assertEqual(len(validate(self.plan())), 1)

    def test_empty_plan_is_allowed(self):
        self.assertEqual(validate({"planned_values": {}}), [])

    def test_delete_and_replacement_are_blocked(self):
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                plan = self.plan()
                plan["resource_changes"][0]["change"]["actions"] = actions
                validate(plan)

    def test_existing_workmates_is_blocked_even_if_renamed(self):
        with self.assertRaises(ValueError):
            validate(self.plan(id="srv-dbhrjvsipgvc73di8gd0"))

    def test_imported_protected_resource_is_blocked(self):
        plan = self.plan()
        plan["resource_changes"][0]["change"]["before"] = {"id": "srv-dbk4ff942hec73ejt9jg"}
        with self.assertRaises(ValueError):
            validate(plan)

    def test_unrelated_resources_are_blocked(self):
        plan = self.plan()
        plan["resource_changes"][0]["address"] = "render_web_service.workmates"
        with self.assertRaises(ValueError):
            validate(plan)

    def test_capacity_and_rename_are_blocked(self):
        for update in ({"name": "workmates-api"}, {"region": "ohio"}, {"num_instances": 2}, {"plan": "pro"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate(self.plan(**update))

    def test_summary_contains_no_environment_secrets(self):
        self.assertNotIn("sensitive-test-value", str(validate(self.plan(env_vars={"TOKEN": "sensitive-test-value"}))))


class PreflightTests(unittest.TestCase):
    def env(self):
        return {
            "RENDER_API_KEY": "test-only", "TF_HTTP_USERNAME": "test-only", "TF_HTTP_PASSWORD": "test-only",
            "PLAN_ENCRYPTION_KEY": "test-only-not-secret-0000000000000000", "TF_VAR_runtime_secrets": "test-only",
            "TF_HTTP_ADDRESS": "https://state.example.invalid/reign",
            "TF_HTTP_LOCK_ADDRESS": "https://state.example.invalid/reign/lock",
            "TF_HTTP_UNLOCK_ADDRESS": "https://state.example.invalid/reign/lock",
        }

    def test_plan_does_not_require_spending_approval(self):
        check(self.env())

    def test_apply_requires_explicit_approval(self):
        with self.assertRaises(ValueError):
            check(self.env(), applying=True)
        check(dict(self.env(), PAID_CHANGE_APPROVAL="true"), applying=True)

    def test_backend_credentials_in_url_are_blocked(self):
        for address in ("http://state.example.invalid/reign", "https://user:pass@state.example.invalid/reign", "https://state.example.invalid/?token=test"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                check(dict(self.env(), TF_HTTP_ADDRESS=address))

    def test_missing_locking_endpoint_is_blocked(self):
        env = self.env()
        del env["TF_HTTP_LOCK_ADDRESS"]
        with self.assertRaises(ValueError):
            check(env)


if __name__ == "__main__":
    unittest.main()
