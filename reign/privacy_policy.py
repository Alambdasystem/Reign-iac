"""Genesis privacy control-plane policy contract; no production dispatch hook yet.

This module is deliberately fail-closed for unclassified outbound payloads.
Heuristic detection is an *additional* negative signal, never evidence that
arbitrary text is free of PHI/PII. No logs, prompts or values are persisted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

MAX_CONTENT = 65536


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    UNCLASSIFIED = "unclassified"


# Safety backstop for *obvious* sensitive content. Incomplete by design:
# classify content at ingestion and use reviewed sensitive-data detectors.
_DETECTORS = (
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")),
    ("ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}(?!\d)")),
    ("api_key", re.compile(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|ghp_[a-z0-9]{20,})\b")),
    ("patient_record", re.compile(r"(?i)\b(?:patient\s+(?:name|id|record|diagnosis)|medical\s+record|diagnosed\s+with|date\s+of\s+birth)\s*[:#=-]?\s*\S+")),
)


@dataclass(frozen=True)
class PrivacyDecision:
    allowed: bool
    reason: Literal[
        "allow_public_explicit",
        "deny_invalid_input",
        "deny_unclassified",
        "deny_sensitive_label",
        "deny_sensitive_indicator",
        "deny_unapproved_destination",
    ]
    classification: Classification
    indicator_types: tuple[str, ...]

    def public_metadata(self) -> dict:
        """No original content, match substrings, addresses, destinations or secrets."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "classification": self.classification.value,
            "indicator_types": list(self.indicator_types),
            "enforcement": "draft_policy_contract_only",
        }


def indicator_types(value: str) -> tuple[str, ...]:
    if type(value) is not str or len(value.encode("utf-8")) > MAX_CONTENT:
        raise ValueError("Invalid privacy inspection input")
    return tuple(name for name, pattern in _DETECTORS if pattern.search(value))


def decide_external(
    value: str,
    *,
    classification: Classification = Classification.UNCLASSIFIED,
    destination_approved: bool = False,
) -> PrivacyDecision:
    """Conservative decision for future model/MCP egress; currently no execution hook.

    Any unknown text remains blocked even if no pattern is found. Explicit
    PUBLIC is allowed only for approved destinations without known indicators.
    This is not a PHI/PII detection guarantee or de-identification service.
    """
    if not isinstance(classification, Classification) or type(destination_approved) is not bool:
        return PrivacyDecision(False, "deny_invalid_input", Classification.UNCLASSIFIED, ())
    try:
        indicators = indicator_types(value)
    except (ValueError, UnicodeError):
        return PrivacyDecision(False, "deny_invalid_input", classification, ())
    if indicators:
        return PrivacyDecision(False, "deny_sensitive_indicator", classification, indicators)
    if classification == Classification.UNCLASSIFIED:
        return PrivacyDecision(False, "deny_unclassified", classification, ())
    if classification != Classification.PUBLIC:
        return PrivacyDecision(False, "deny_sensitive_label", classification, ())
    if not destination_approved:
        return PrivacyDecision(False, "deny_unapproved_destination", classification, ())
    return PrivacyDecision(True, "allow_public_explicit", classification, ())
