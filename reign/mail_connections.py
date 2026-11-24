"""Owner-only, read-only OAuth mailbox adapters for the REIGN pilot."""

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import time
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

PROVIDERS = ("gmail", "outlook")
MAX_SAMPLE = 10
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH = "https://graph.microsoft.com/v1.0/me"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GRAPH_SCOPES = ("User.Read", "Mail.ReadBasic", "offline_access")
EMAIL = re.compile(r"[^@\s]{1,128}@[^@\s]{1,126}")
TENANT = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


@dataclass(frozen=True)
class Settings:
    provider: str
    client_id: str
    secret: str = field(repr=False)
    mailbox: str
    authorize: str
    token: str
    scopes: tuple[str, ...]
    redirect: str


def settings(provider):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown mailbox provider")
    prefix = "REIGN_" + provider.upper()
    client_id = os.getenv(prefix + "_CLIENT_ID", "").strip()
    secret = os.getenv(prefix + "_CLIENT_SECRET", "")
    mailbox = os.getenv(prefix + "_OWNER_EMAIL", "").strip().casefold()
    origin = os.getenv("REIGN_MAIL_ORIGIN", "").rstrip("/")
    if (not client_id or not secret or not EMAIL.fullmatch(mailbox)
            or not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::[0-9]+)?", origin)):
        return None
    if provider == "gmail":
        urls = ("https://accounts.google.com/o/oauth2/v2/auth",
                "https://oauth2.googleapis.com/token")
        scopes = (GOOGLE_SCOPE,)
    else:
        tenant = os.getenv("REIGN_OUTLOOK_TENANT_ID", "")
        if not TENANT.fullmatch(tenant):
            return None
        issuer = "https://login.microsoftonline.com/" + tenant + "/oauth2/v2.0"
        urls = (issuer + "/authorize", issuer + "/token")
        scopes = GRAPH_SCOPES
    return Settings(provider, client_id, secret, mailbox, *urls, scopes,
                    origin + "/connections/mail/" + provider + "/callback")


def consent_url(config, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    params = dict(client_id=config.client_id, redirect_uri=config.redirect, response_type="code",
                  scope=" ".join(config.scopes), state=state, code_challenge=challenge,
                  code_challenge_method="S256", login_hint=config.mailbox)
    if config.provider == "gmail":
        params.update(access_type="offline", prompt="consent")
    else:
        params.update(response_mode="form_post", prompt="select_account")
    return config.authorize + "?" + urlencode(params)


def safe_text(value, size):
    return " ".join(value.split())[:size] if isinstance(value, str) else ""


class MailConnections:
    def __init__(self, store, http):
        self.store = store
        self.http = http
        self.locks = {name: asyncio.Lock() for name in PROVIDERS}

    async def _record(self, provider):
        return await asyncio.to_thread(self.store.owner_mail, provider)

    async def _save(self, provider, revision, value):
        return await asyncio.to_thread(self.store.owner_mail, provider, (revision, value))

    def _config(self, provider):
        value = settings(provider)
        if value is None or self.store is None:
            raise HTTPException(503, "Mailbox provider needs setup")
        return value

    async def status(self):
        rows = {}
        for provider in PROVIDERS:
            config = settings(provider)
            if config is None or self.store is None:
                continue
            revision, value = await self._record(provider)
            active = value and value["mailbox"] == config.mailbox
            rows[provider] = {"status": "connected" if active else "available", "revision": revision}
            if active:
                rows[provider]["email"] = value["mailbox"]
        return rows

    async def begin(self, provider, state, verifier):
        config = self._config(provider)
        revision, _ = await self._record(provider)
        return {"url": consent_url(config, state, verifier), "revision": revision}

    async def _json(self, method, url, **kwargs):
        try:
            async with self.http.stream(method, url, timeout=12, follow_redirects=False, **kwargs) as response:
                if response.status_code in (401, 403):
                    raise HTTPException(409, "Mailbox authorization needs attention")
                if response.status_code != 200:
                    raise HTTPException(502, "Mailbox provider could not complete the request")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 128_000:
                        raise HTTPException(502, "Mailbox provider response is too large")
                    data.extend(chunk)
                value = json.loads(data)
                if type(value) is not dict:
                    raise ValueError()
                return value
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise HTTPException(502, "Mailbox provider temporarily unavailable") from None

    async def _token(self, config, params, previous=None):
        body = await self._json("POST", config.token,
                                data={**params, "client_id": config.client_id, "client_secret": config.secret})
        access, refresh = body.get("access_token"), body.get("refresh_token", previous)
        expires = body.get("expires_in")
        scope = body.get("scope")
        if not (isinstance(access, str) and 0 < len(access) <= 12000 and access.isascii()
                and isinstance(refresh, str) and 0 < len(refresh) <= 12000 and refresh.isascii()
                and re.fullmatch(r"[!-~]+", access) and re.fullmatch(r"[!-~]+", refresh)
                and safe_text(body.get("token_type"), 20).casefold() == "bearer"
                and type(expires) is int and 60 <= expires <= 86400):
            raise HTTPException(502, "Mailbox provider returned an invalid grant")
        if scope is not None or previous is None:
            actual = {s.lower().removeprefix("https://graph.microsoft.com/") for s in
                      (scope.split() if isinstance(scope, str) else [])}
            required = {s.lower() for s in config.scopes if s != "offline_access"}
            if not required <= actual or actual - required - {"offline_access", "openid", "profile", "email"}:
                raise HTTPException(409, "The mailbox grant does not match the read-only permissions")
        return {"access": access, "refresh": refresh, "expires": time.time() + expires}

    async def _profile(self, config, token):
        google = config.provider == "gmail"
        profile = await self._json("GET", GMAIL + "/profile" if google else GRAPH,
                                   headers={"Authorization": "Bearer " + token},
                                   params=None if google else {"$select": "id,mail,userPrincipalName"})
        mailbox = safe_text(profile.get("emailAddress") if google else
                            profile.get("mail") or profile.get("userPrincipalName"), 255).casefold()
        subject = mailbox if google else safe_text(profile.get("id"), 255)
        if mailbox != config.mailbox or not subject:
            raise HTTPException(403, "This mailbox is outside the owner pilot")
        return {"mailbox": mailbox, "subject": subject}

    async def connect(self, provider, code, verifier, revision):
        config = self._config(provider)
        async with self.locks[provider]:
            current, _ = await self._record(provider)
            if current != revision:
                raise HTTPException(409, "Connection changed. Start sign-in again.")
            tokens = await self._token(config, {"grant_type": "authorization_code", "code": code,
                                                  "redirect_uri": config.redirect, "code_verifier": verifier})
            identity = await self._profile(config, tokens["access"])
            await self._save(provider, revision, {**identity, **tokens})
            return {"status": "connected", "email": identity["mailbox"]}

    async def disconnect(self, provider):
        if provider not in PROVIDERS or self.store is None:
            raise HTTPException(404, "Unknown mailbox provider")
        async with self.locks[provider]:
            revision, _ = await self._record(provider)
            await self._save(provider, revision, None)
        return {"status": "disconnected"}

    async def unread(self, provider, limit):
        config = self._config(provider)
        async with self.locks[provider]:
            revision, grant = await self._record(provider)
            if not grant or grant["mailbox"] != config.mailbox:
                raise HTTPException(409, "Connect your verified mailbox first")
            if grant["expires"] <= time.time() + 60:
                params = {"grant_type": "refresh_token", "refresh_token": grant["refresh"]}
                if provider == "outlook":
                    params["scope"] = " ".join(config.scopes)
                refreshed = await self._token(config, params, grant["refresh"])
                identity = await self._profile(config, refreshed["access"])
                if identity["subject"] != grant["subject"]:
                    raise HTTPException(403, "Mailbox identity changed")
                grant = {**identity, **refreshed}
                await self._save(provider, revision, grant)
            headers = {"Authorization": "Bearer " + grant["access"]}
            if provider == "gmail":
                result = await self._gmail(headers, limit)
            else:
                result = await self._outlook(headers, limit)
            return {"provider": provider, "email": grant["mailbox"], "sampled_unread": len(result),
                    "count_kind": "sample", "messages": result}

    async def _gmail(self, headers, limit):
        result = await self._json("GET", GMAIL + "/messages", headers=headers,
                                  params={"labelIds": ["INBOX", "UNREAD"], "maxResults": limit,
                                          "fields": "messages(id)"})

        async def message(row):
            identifier = row.get("id") if isinstance(row, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                raise HTTPException(502, "Invalid Gmail message reference")
            details = await self._json("GET", GMAIL + "/messages/" + identifier, headers=headers,
                                       params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"],
                                               "fields": "payload(headers)"})
            values = {item["name"].lower(): item.get("value") for item in details.get("payload", {}).get("headers", [])
                      if isinstance(item, dict) and isinstance(item.get("name"), str)}
            return {"subject": safe_text(values.get("subject"), 180), "from": safe_text(values.get("from"), 180),
                    "date": safe_text(values.get("date"), 80)}
        rows = result.get("messages", [])
        if not isinstance(rows, list):
            raise HTTPException(502, "Invalid Gmail unread response")
        results = await asyncio.gather(*(message(row) for row in rows[:limit]), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def _outlook(self, headers, limit):
        result = await self._json("GET", GRAPH + "/mailFolders/inbox/messages", headers=headers,
                                  params={"$top": limit, "$filter": "isRead eq false",
                                          "$select": "subject,from,receivedDateTime,isRead"})
        rows = result.get("value")
        if not isinstance(rows, list):
            raise HTTPException(502, "Invalid Outlook unread response")
        messages = []
        for row in rows[:limit]:
            if not isinstance(row, dict) or row.get("isRead") is not False:
                continue
            sender = row.get("from") or {}
            address = (sender.get("emailAddress") or {}) if isinstance(sender, dict) else {}
            messages.append({"subject": safe_text(row.get("subject"), 180),
                             "from": safe_text(address.get("address") if isinstance(address, dict) else None, 180),
                             "date": safe_text(row.get("receivedDateTime"), 80)})
        return messages
