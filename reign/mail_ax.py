"""Bind provider consent to the existing owner session across the OAuth redirect."""

from contextlib import suppress
from dataclasses import dataclass, field
import json
import logging
import os
import re
import secrets
import time
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import QueryParams

from reign.mail_routes import Provider

STATE = re.compile(r"[A-Za-z0-9_-]{43}")
RETURN_PATH = "/connections/mail/"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
TTL = 300


class MailAccessLogFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn includes the query in the request-target positional argument.
        # Never emit callback access entries, regardless of the logger formatter.
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3
                    and isinstance(args[2], str) and args[2].startswith(RETURN_PATH))


@dataclass
class Attempt:
    session: str
    provider: str
    revision: int
    expires: float
    verifier: str = field(repr=False)
    code: str | None = field(default=None, repr=False)


class MailConsent:
    def __init__(self):
        self.pending = {}

    def prune(self):
        now = time.monotonic()
        for state in [state for state, attempt in self.pending.items() if attempt.expires <= now]:
            del self.pending[state]

    def invalidate(self, provider):
        for state in [state for state, attempt in self.pending.items() if attempt.provider == provider]:
            del self.pending[state]


def install(app, principal, csrf, core):
    logging.getLogger("uvicorn.access").addFilter(MailAccessLogFilter())

    def owner(request, write=False):
        session = principal(request)
        if session.identity.role != "owner":
            raise HTTPException(403, "Mailbox access is limited to the owner", headers=HEADERS)
        if write:
            csrf(request, session)
        return session

    @app.get("/api/connections/mail")
    async def status(request: Request):
        owner(request)
        result = (await core("GET", "/internal/mail/status")).json()
        owner(request)
        return JSONResponse(result, headers=HEADERS)

    @app.put("/api/connections/mail/{provider}/settings")
    async def settings(provider: Provider, request: Request):
        owner(request, True)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 16000:
                raise HTTPException(422, "Invalid OAuth settings", headers=HEADERS)
            raw.extend(chunk)
        try:
            values = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Invalid OAuth settings", headers=HEADERS) from None
        if type(values) is not dict or set(values) != {"client_id", "client_secret", "mailbox", "tenant"}:
            raise HTTPException(422, "Invalid OAuth settings", headers=HEADERS)
        owner(request, True)
        request.app.state.mail_consent.invalidate(provider)
        result = await core("PUT", "/internal/mail/" + provider + "/settings",
                            {**values, "origin": os.getenv("AX_PUBLIC_ORIGIN", "")})
        owner(request, True)
        return JSONResponse(result.json(), headers=HEADERS)

    @app.post("/api/connections/mail/{provider}/begin")
    async def begin(provider: Provider, request: Request):
        session = owner(request, True)
        consent = request.app.state.mail_consent
        consent.prune()
        state, verifier = (secrets.token_urlsafe(32) for _ in range(2))
        result = (await core("POST", "/internal/mail/" + provider + "/begin",
                             {"state": state, "verifier": verifier})).json()
        owner(request, True)
        consent.invalidate(provider)
        consent.pending[state] = Attempt(session.nonce, provider, result["revision"],
                                         time.monotonic() + TTL, verifier)
        return JSONResponse({"url": result["url"]}, headers=HEADERS)

    @app.api_route("/connections/mail/{provider}/callback", methods=["GET", "POST"])
    async def callback(provider: Provider, request: Request):
        if request.method != ("GET" if provider == "gmail" else "POST"):
            raise HTTPException(405, "Unexpected mailbox callback method", headers=HEADERS)
        if request.method == "POST":
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > 12000:
                    raise HTTPException(413, "Mailbox callback too large", headers=HEADERS)
                data.extend(chunk)
            params = {}
            with suppress(ValueError, UnicodeError):
                params = QueryParams(data.decode("ascii"))
        else:
            params = request.query_params
        state = params.get("state", "")
        consent = request.app.state.mail_consent
        consent.prune()
        attempt = consent.pending.get(state) if STATE.fullmatch(state) else None
        if not attempt or attempt.provider != provider or attempt.code is not None:
            return RedirectResponse("/#reign-mail-error", 303, headers=HEADERS)
        code = params.get("code", "")
        if not code or len(code) > 4096 or params.get("error"):
            del consent.pending[state]
            return RedirectResponse("/#reign-mail-error", 303, headers=HEADERS)
        attempt.code = code
        return RedirectResponse("/#reign-mail=" + state, 303, headers=HEADERS)

    @app.post("/api/connections/mail/complete")
    async def complete(request: Request):
        session = owner(request, True)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 512:
                raise HTTPException(422, "Invalid mailbox state", headers=HEADERS)
            raw.extend(chunk)
        state = ""
        with suppress(ValueError, UnicodeError):
            params = parse_qs(raw.decode("ascii"), max_num_fields=1)
            state = params.get("state", [""])[0]
        consent = request.app.state.mail_consent
        consent.prune()
        attempt = consent.pending.get(state) if STATE.fullmatch(state) else None
        if not attempt or attempt.session != session.nonce or not attempt.code:
            raise HTTPException(409, "Start mailbox sign-in again from this session", headers=HEADERS)
        del consent.pending[state]
        result = await core("POST", "/internal/mail/" + attempt.provider + "/complete",
                            {"code": attempt.code, "verifier": attempt.verifier, "revision": attempt.revision})
        owner(request, True)
        return JSONResponse(result.json(), headers=HEADERS)

    @app.delete("/api/connections/mail/{provider}")
    async def disconnect(provider: Provider, request: Request):
        owner(request, True)
        request.app.state.mail_consent.invalidate(provider)
        result = await core("DELETE", "/internal/mail/" + provider)
        return JSONResponse(result.json(), headers=HEADERS)

    @app.post("/api/connections/mail/{provider}/preview")
    async def preview(provider: Provider, request: Request):
        owner(request, True)
        result = await core("POST", "/internal/mail/preview", {"provider": provider, "limit": 5})
        owner(request, True)
        return JSONResponse(result.json(), headers=HEADERS)
