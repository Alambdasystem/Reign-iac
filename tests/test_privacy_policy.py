"""No real patient or customer data in privacy foundation tests."""
import unittest

from reign.privacy_policy import Classification, decide_external, indicator_types


class PrivacyPolicyContractTests(unittest.TestCase):
    def test_unclassified_is_denied_even_when_no_patterns_match(self):
        d = decide_external("Hello from the workspace", destination_approved=True)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "deny_unclassified")

    def test_explicit_public_needs_approved_destination(self):
        self.assertEqual(
            decide_external("Product documentation", classification=Classification.PUBLIC,
                            destination_approved=False).reason,
            "deny_unapproved_destination")
        d = decide_external("Product documentation", classification=Classification.PUBLIC,
                            destination_approved=True)
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "allow_public_explicit")

    def test_all_non_public_labels_are_denied(self):
        for label in (Classification.INTERNAL, Classification.CONFIDENTIAL,
                      Classification.RESTRICTED):
            with self.subTest(label=label):
                self.assertFalse(decide_external("Internal notes", classification=label,
                                                 destination_approved=True).allowed)

    def test_obvious_sensitive_indicators_block_even_when_public(self):
        samples = {
            "email": "Contact alex@example.test",
            "ssn": "SSN 123-45-6789",
            "phone": "Call 314-555-0187",
            "api_key": "sk-example123456789abcdef",
            "patient_record": "Patient diagnosis: ExampleCondition",
        }
        for marker, message in samples.items():
            with self.subTest(marker=marker):
                self.assertIn(marker, indicator_types(message))
                decision = decide_external(message, classification=Classification.PUBLIC,
                                           destination_approved=True)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "deny_sensitive_indicator")
                meta = str(decision.public_metadata())
                self.assertNotIn(message, meta)
                self.assertNotIn("example.test", meta)
                self.assertNotIn("123-45-6789", meta)

    def test_invalid_data_or_labels_fail_closed(self):
        for payload in (None, b"bytes", "x" * 65537):
            with self.subTest(type=type(payload).__name__):
                self.assertEqual(decide_external(payload).reason, "deny_invalid_input")
        self.assertFalse(decide_external("abc", classification="public",
                                         destination_approved=True).allowed)
        self.assertFalse(decide_external("abc", classification=Classification.PUBLIC,
                                         destination_approved="yes").allowed)

    def test_metadata_has_only_bounded_classification_fields(self):
        result = decide_external("Contact alex@example.test", classification=Classification.PUBLIC,
                                 destination_approved=True).public_metadata()
        self.assertEqual(set(result), {"allowed", "reason", "classification",
                                       "indicator_types", "enforcement"})
        self.assertEqual(result["enforcement"], "draft_policy_contract_only")
