"""Shared REIGN computer connection profile contract.

This module intentionally does not connect to arbitrary hosts or store secrets.
Adapters must be implemented and verified separately.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit


class ConnectionKind(str, Enum):
    MANAGED_BROWSER = "managed_browser"
    REMOTE_BROWSER = "remote_browser"
    REMOTE_DESKTOP = "remote_desktop"


class Capability(str, Enum):
    BROWSER = "browser"
    DESKTOP = "desktop"
    LIVE_FRAMES = "live_frames"
    HUMAN_TAKEOVER = "human_takeover"


@dataclass(frozen=True)
class ComputerProfile:
    profile_id: str
    owner_id: str
    kind: ConnectionKind
    endpoint: Optional[str] = None
    secret_ref: Optional[str] = None

    def validate(self) -> None:
        if not self.profile_id.strip() or not self.owner_id.strip():
            raise ValueError("Profile and owner identifiers are required")
        if self.kind == ConnectionKind.MANAGED_BROWSER:
            if self.endpoint or self.secret_ref:
                raise ValueError("Managed browser profiles cannot specify external connection details")
            return
        if not self.endpoint or not self.secret_ref:
            raise ValueError("External connections require an endpoint and server-side secret reference")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in ("https", "wss") or not parsed.hostname:
            raise ValueError("External connection endpoint must use HTTPS or WSS")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Credentials and fragments are prohibited in endpoint URLs")
        if parsed.query:
            raise ValueError("Endpoint query strings are prohibited")
        if not self.secret_ref.startswith("secret://"):
            raise ValueError("Use a server-side secret reference, never inline credentials")


@dataclass(frozen=True)
class ComputerSessionBinding:
    session_id: str
    profile_id: str
    owner_id: str
    worker_id: str
    control_epoch: int

    def accepts_input(self, *, owner_id: str, worker_id: str, epoch: int) -> bool:
        return (self.owner_id == owner_id and self.worker_id == worker_id
                and self.control_epoch == epoch)
