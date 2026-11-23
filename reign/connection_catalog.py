"""Curated provider metadata for the REIGN Connections picker.

Provider and tool readiness must come from server-side registration. This
catalog publishes presentation metadata and explicitly advertises zero tools.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    description: str


PROVIDERS = (
    Provider("gmail", "Gmail", "Connect a Google account for approved Gmail tools."),
    Provider("outlook", "Outlook", "Connect a Microsoft account for approved Outlook tools."),
)


def public_catalog():
    return {
        "providers": [
            {**asdict(provider), "availability": "unavailable", "tools": []}
            for provider in PROVIDERS
        ]
    }
