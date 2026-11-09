"""Trace schema security regression tests; no external service required."""
import time
import unittest
from unittest.mock import patch
from reign.telemetry import new_trace_id, safe_span


class TelemetryContractTests(unittest.TestCase):
    def setUp(self):
        self.trace = new_trace_id()
        self.started = time.monotonic()

    def span(self, **overrides):
        args = dict(trace_id=self.trace, service="core", operation="agent.tool",
                    outcome="ok", tool_name="browser_click",
                    started_monotonic=self.started, input_tokens=7, output_tokens=2)
        args.update(overrides)
        return safe_span(**args)

    def test_valid_span_contains_only_allowlisted_fields(self):
        value = self.span()
        self.assertEqual(value["tool_name"], "browser_click")
        self.assertEqual(len(value["span_id"]), 16)
        self.assertGreaterEqual(value["duration_ms"], 0)
        self.assertEqual(value["trace_id"], self.trace)
        self.assertEqual(set(value), {"trace_id", "span_id", "parent_span_id",
            "service", "operation", "outcome", "tool_name", "duration_ms",
            "input_tokens", "output_tokens", "error_code", "recorded_at"})

    def test_unrecognized_tool_operation_or_service_fail_closed(self):
        for args in ({"tool_name": "read_password"},
                     {"service": "https://outside.example"},
                     {"operation": "user.prompt"},
                     {"outcome": "completed with password"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.span(**args)

    def test_raw_provider_error_is_discarded(self):
        value = self.span(outcome="error", error_code="Bearer secret-value")
        self.assertEqual(value["error_code"], "unknown")
        self.assertNotIn("secret-value", str(value))

    def test_negative_tokens_and_invalid_parent_are_rejected(self):
        for args in ({"input_tokens": -1}, {"output_tokens": True},
                     {"parent_span_id": "not-a-hex-span"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.span(**args)

    def test_no_freeform_prompt_or_tool_argument_fields(self):
        with self.assertRaises(TypeError):
            self.span(prompt="please log in with my password")
        with self.assertRaises(TypeError):
            self.span(url="https://site.example/?token=secret")
        with self.assertRaises(TypeError):
            self.span(tool_args={"password": "secret"})
