"""Core routes for the sealed owner mailbox store and its private MCP bridge."""

import secrets
from typing import Literal

from fastapi import Depends, HTTPException, Request
from pydantic import Field

from reign.tool_contracts import EmptyArgs, MailUnreadArgs

Provider = Literal["gmail", "outlook"]


class MailBegin(EmptyArgs):
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class MailComplete(EmptyArgs):
    code: str = Field(min_length=1, max_length=4096)
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    revision: int = Field(ge=0)


class MailSettings(EmptyArgs):
    client_id: str = Field(min_length=1, max_length=512, pattern=r"^[!-~]+$")
    client_secret: str = Field(min_length=1, max_length=4096, pattern=r"^[!-~]+$")
    mailbox: str = Field(min_length=3, max_length=255)
    origin: str = Field(min_length=9, max_length=255)
    tenant: str = Field(default="", max_length=36)


def install(app, authorize):
    @app.get("/internal/mail/status", dependencies=[Depends(authorize)])
    async def status(request: Request):
        return await request.app.state.mail.status()

    @app.put("/internal/mail/{provider}/settings", dependencies=[Depends(authorize)])
    async def settings(provider: Provider, args: MailSettings, request: Request):
        return await request.app.state.mail.configure(provider, args.model_dump())

    @app.post("/internal/mail/{provider}/begin", dependencies=[Depends(authorize)])
    async def begin(provider: Provider, args: MailBegin, request: Request):
        return await request.app.state.mail.begin(provider, args.state, args.verifier)

    @app.post("/internal/mail/{provider}/complete", dependencies=[Depends(authorize)])
    async def complete(provider: Provider, args: MailComplete, request: Request):
        return await request.app.state.mail.connect(provider, args.code, args.verifier, args.revision)

    @app.delete("/internal/mail/{provider}", dependencies=[Depends(authorize)])
    async def disconnect(provider: Provider, request: Request):
        return await request.app.state.mail.disconnect(provider)

    @app.post("/internal/mail/preview", dependencies=[Depends(authorize)])
    async def preview(args: MailUnreadArgs, request: Request):
        return await request.app.state.mail_mcp.invoke("reign_mail_unread", args.model_dump(), mailbox_owner=True)

    @app.post("/internal/mail/mcp-unread")
    async def mcp_unread(args: MailUnreadArgs, request: Request):
        expected = request.app.state.mail_mcp.mail_key
        token = request.headers.get("authorization", "")
        if (request.client is None or request.client.host != "127.0.0.1"
                or not expected or not secrets.compare_digest(token, "Bearer " + expected)):
            raise HTTPException(403, "Mailbox tool access required")
        return await request.app.state.mail.unread(args.provider, args.limit)
