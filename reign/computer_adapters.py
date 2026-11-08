"""Adapter boundary for managed and attached REIGN computers.

No adapter is considered operational until it implements this interface and
passes integration tests against a real supported endpoint.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import FrozenSet, Mapping, Optional

from reign.computer_profiles import Capability, ComputerProfile


@dataclass(frozen=True)
class ConnectionStatus:
    connected: bool
    capabilities: FrozenSet[Capability]
    reason: Optional[str] = None


@dataclass(frozen=True)
class SessionState:
    session_id: str
    profile_id: str
    owner_id: str
    worker_id: str
    control_owner: str
    epoch: int


class ComputerAdapter(ABC):
    @abstractmethod
    async def test_connection(self, profile: ComputerProfile) -> ConnectionStatus:
        """Authenticate and probe actual capabilities, not merely URL reachability."""

    @abstractmethod
    async def open_session(self, profile: ComputerProfile, owner_id: str) -> SessionState:
        """Create or attach, binding session to the selected profile and owner."""

    @abstractmethod
    async def status(self, session: SessionState) -> ConnectionStatus:
        """Recheck worker identity and connectivity."""

    @abstractmethod
    async def frame(self, session: SessionState) -> bytes:
        """Return a fresh authenticated frame for the bound session."""

    @abstractmethod
    async def input(self, session: SessionState, command: Mapping[str, object]) -> SessionState:
        """Reject stale epochs and commands from the wrong controller."""

    @abstractmethod
    async def takeover(self, session: SessionState, expected_epoch: int) -> SessionState:
        """Pause agent input and transfer control to the human."""

    @abstractmethod
    async def handback(self, session: SessionState, expected_epoch: int) -> SessionState:
        """Invalidate old human commands and resume agent ownership."""

    @abstractmethod
    async def close(self, session: SessionState) -> None:
        """Release owned resources, never shut down a user-owned computer."""


class UnsupportedAdapter(ComputerAdapter):
    """Explicit fail-closed placeholder; prevents false desktop support claims."""

    def __init__(self, reason: str):
        self.reason = reason

    async def test_connection(self, profile: ComputerProfile) -> ConnectionStatus:
        profile.validate()
        return ConnectionStatus(False, frozenset(), self.reason)

    async def open_session(self, profile: ComputerProfile, owner_id: str) -> SessionState:
        raise NotImplementedError(self.reason)

    async def status(self, session: SessionState) -> ConnectionStatus:
        return ConnectionStatus(False, frozenset(), self.reason)

    async def frame(self, session: SessionState) -> bytes:
        raise NotImplementedError(self.reason)

    async def input(self, session: SessionState, command: Mapping[str, object]) -> SessionState:
        raise NotImplementedError(self.reason)

    async def takeover(self, session: SessionState, expected_epoch: int) -> SessionState:
        raise NotImplementedError(self.reason)

    async def handback(self, session: SessionState, expected_epoch: int) -> SessionState:
        raise NotImplementedError(self.reason)

    async def close(self, session: SessionState) -> None:
        return None
