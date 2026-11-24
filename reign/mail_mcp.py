"""Fixed loopback bridge from the private MCP process to Core's mailbox vault."""

import os

import httpx


async def unread(args, meta):
    key = os.getenv("REIGN_MAIL_MCP_KEY", "")
    port = os.getenv("REIGN_MAIL_CORE_PORT", "")
    if meta.get("reign_mail_owner") is not True or not key or not port.isascii() or not port.isdecimal():
        return {"error": "Mailbox owner authorization is required", "code": "mail_owner_required", "status": 403}
    if not 1 <= int(port) <= 65535:
        return {"error": "Mailbox preview unavailable", "code": "mail_unavailable", "status": 503}
    try:
        async with httpx.AsyncClient(timeout=55, trust_env=False, follow_redirects=False) as client:
            result = await client.post("http://127.0.0.1:" + port + "/internal/mail/mcp-unread",
                                       json=args, headers={"Authorization": "Bearer " + key})
        if result.status_code == 409:
            return {"error": "Connect or reconnect this mailbox first", "code": "mail_reconnect", "status": 409}
        if result.status_code == 200 and len(result.content) <= 30000:
            return result.json()
    except (httpx.HTTPError, ValueError):
        pass
    return {"error": "Mailbox preview unavailable", "code": "mail_unavailable", "status": 503}
