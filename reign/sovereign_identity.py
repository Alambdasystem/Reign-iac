"""Signed, short-lived Sovereign identity grants.

Proof-of-concept backend boundary; must be connected to an OIDC-authenticated
control plane and independently enforced at provider and network boundaries.
Never accept an agent-supplied identity as authentication.
"""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time

class IdentityDenied(PermissionError):
    pass

def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 8192:
        raise IdentityDenied("Invalid grant")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise IdentityDenied("Invalid grant") from None

@dataclass(frozen=True)
class VerifiedWorkload:
    tenant_id: str
    owner_id: str
    agent_id: str
    audience: str
    expires_at: int

class GrantAuthority:
    """Symmetric signing for isolated gateway trials, not a production IdP."""
    def __init__(self, secret: bytes, audience: str = "sovereign-mcp"):
        if not isinstance(secret, bytes) or len(secret) < 32 or not audience:
            raise ValueError("Strong signing key and audience required")
        self._key, self.audience = secret, audience

    def issue(self, *, tenant_id: str, owner_id: str, agent_id: str,
              ttl_seconds: int = 60, now: int | None = None) -> str:
        if not 1 <= ttl_seconds <= 300:
            raise ValueError("Grant TTL must be between 1 and 300 seconds")
        from reign.sovereign_registry import AgentRecord
        # Validate identifiers with the same schema as agent registrations.
        AgentRecord(agent_id=agent_id, tenant_id=tenant_id, owner_id=owner_id,
                    name="Grant subject", harness="langgraph", provider="render",
                    constitution_version="identity-v1", capabilities=frozenset())
        issued = int(time.time()) if now is None else now
        payload = dict(v=1, tenant=tenant_id, owner=owner_id, agent=agent_id,
                       aud=self.audience, iat=issued, exp=issued + ttl_seconds)
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        mac = _encode(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        return encoded + "." + mac

    def verify(self, token: str, *, now: int | None = None) -> VerifiedWorkload:
        if not isinstance(token, str) or token.count(".") != 1:
            raise IdentityDenied("Invalid grant")
        encoded, signature = token.split(".", 1)
        expected = hmac.new(self._key, encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise IdentityDenied("Invalid grant")
        try:
            payload = json.loads(_decode(encoded))
            if set(payload) != {"v", "tenant", "owner", "agent", "aud", "iat", "exp"}:
                raise ValueError()
            if payload["v"] != 1 or payload["aud"] != self.audience:
                raise ValueError()
            issued, expires = payload["iat"], payload["exp"]
            current = int(time.time()) if now is None else now
            if (type(issued) is not int or type(expires) is not int
                    or issued > current or expires <= current
                    or expires - issued > 300):
                raise ValueError()
            from reign.sovereign_registry import AgentRecord
            AgentRecord(agent_id=payload["agent"], tenant_id=payload["tenant"],
                        owner_id=payload["owner"], name="Verified", harness="langgraph",
                        provider="render", constitution_version="identity-v1",
                        capabilities=frozenset())
            return VerifiedWorkload(payload["tenant"], payload["owner"], payload["agent"],
                                    payload["aud"], expires)
        except (ValueError, TypeError, KeyError):
            raise IdentityDenied("Invalid grant") from None
