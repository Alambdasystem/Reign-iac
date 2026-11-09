"""Privacy-preserving trace metadata for REIGN agent operations.

Phase-one in-memory instrumentation only: this module does NOT export to a
collector, persist a session, or enable an admin endpoint. Call sites must not
send raw prompts, tool arguments, tokens, secrets, URLs or model output.
"""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
import time
import uuid

# This is an intentionally *small* immutable public metadata vocabulary.
# Anything unrecognized is rejected rather than being copied into traces.
SERVICES = frozenset({"ax", "core", "mcp", "temporal", "computer"})
OUTCOMES = frozenset({"ok", "error", "blocked", "cancelled"})
OPERATIONS = frozenset({
    "agent.run", "agent.model", "agent.tool", "agent.pause",
    "agent.resume", "agent.cancel", "agent.failure", "browser.action",
})
TOOLS = frozenset({
    "workspace_status", "browser_status", "open_browser", "navigate_browser",
    "browser_observe", "browser_click", "browser_fill", "browser_press",
    "browser_scroll", "vault_status", "vault_list_accounts",
    "vault_request_account", "vault_prepare_signup", "vault_use_account",
})
ERROR_CODES = frozenset({
    "unknown", "timeout", "unavailable", "rejected", "cancelled",
    "browser_occupied", "stale_observation", "control_changed",
    "human_verification_required", "action_uncertain",
    "account_form_unavailable",
})


def new_trace_id() -> str:
    return str(uuid.uuid4())


def new_span_id() -> str:
    return secrets.token_hex(8)


def safe_span(*, trace_id: str, service: str, operation: str,
              outcome: str, started_monotonic: float, tool_name: str | None = None,
              input_tokens: int | None = None, output_tokens: int | None = None,
              error_code: str | None = None, parent_span_id: str | None = None) -> dict:
    """Construct only the allowed span fields, fail closed for unknown metadata.

    A caller can never supply a free-form attribute dictionary through this API.
    """
    trace = str(uuid.UUID(trace_id))
    if service not in SERVICES or operation not in OPERATIONS or outcome not in OUTCOMES:
        raise ValueError("Unsupported telemetry metadata")
    if tool_name is not None and tool_name not in TOOLS:
        raise ValueError("Unrecognized tool name")
    if error_code is not None and error_code not in ERROR_CODES:
        # Do not save raw exception messages/codes from an untrusted website.
        error_code = "unknown"
    if parent_span_id is not None and (
            len(parent_span_id) != 16 or
            any(c not in "0123456789abcdef" for c in parent_span_id)):
        raise ValueError("Invalid parent span")
    if started_monotonic > time.monotonic():
        raise ValueError("Span start is in the future")
    for count in (input_tokens, output_tokens):
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("Invalid token count")
    return {
        "trace_id": trace,
        "span_id": new_span_id(),
        "parent_span_id": parent_span_id,
        "service": service,
        "operation": operation,
        "outcome": outcome,
        "tool_name": tool_name,
        "duration_ms": max(0, round((time.monotonic() - started_monotonic) * 1000)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error_code": error_code,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
