"""Short-lived, owner-approved Open STL connection to the existing MCP agent.

This is a single-administrator pilot, not a multi-tenant OAuth provider. Device
codes avoid transferring AX cookies/passwords to the game (including WebViews).
Only the thread and browser created by this connection can be addressed.
"""
import asyncio
import hashlib
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

ORIGINS = frozenset({'https://open-stl-3a6l.onrender.com', 'https://localhost'})
PREFIX = '/api/openstl'
PAIR_TTL = 300
GRANT_TTL = 3600


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def reply(value, status=200):
    return JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'})


async def body(request, fields):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8192:
            raise HTTPException(413, 'Request too large')
    import json
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Invalid request') from None
    if not isinstance(value, dict) or set(value) - set(fields):
        raise HTTPException(422, 'Invalid fields')
    return value


def text(value, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HTTPException(422, 'Invalid value')
    return value


@dataclass
class Grant:
    origin: str
    expires: float
    thread: str | None = None
    session: str | None = None
    revoked: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class OpenSTLBridge:
    def __init__(self, core, user, csrf):
        self.core, self.user, self.csrf = core, user, csrf
        self.pending, self.grants, self.starts = {}, {}, {}

    def origin(self, request):
        origin = request.headers.get('origin', '')
        if origin not in ORIGINS:
            raise HTTPException(403, 'Open STL origin required')
        return origin

    def grant(self, request):
        origin = self.origin(request)
        token = request.headers.get('authorization', '')
        if not token.startswith('Bearer ') or len(token) > 100:
            raise HTTPException(401, 'Reconnect through REIGN')
        grant = self.grants.get(digest(token[7:]))
        if not grant or grant.revoked or grant.expires <= time.monotonic() or grant.origin != origin:
            raise HTTPException(401, 'Reconnect through REIGN')
        return grant

    async def snapshot(self, grant):
        if not grant.thread:
            return None
        result = (await self.core('GET', '/api/chat/' + grant.thread)).json()
        if result.get('session_id'):
            sid = str(uuid.UUID(result['session_id']))
            if grant.session and grant.session != sid:
                raise HTTPException(409, 'Browser ownership changed')
            grant.session = sid
        return result

    async def cleanup(self, grant):
        # Mark first: no new actions while cancellation/close are in flight.
        grant.revoked = True
        async with grant.lock:
            if grant.thread:
                try:
                    await self.snapshot(grant)
                    await self.core('DELETE', '/api/chat/' + grant.thread)
                except HTTPException as error:
                    if error.status_code != 404:
                        return False
                grant.thread = None
            if grant.session:
                try:
                    await self.core('DELETE', '/api/sessions/' + grant.session)
                except HTTPException as error:
                    if error.status_code != 404:
                        return False
                grant.session = None
        return True

    async def reap(self):
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > now}
        self.starts = {k: v for k, v in self.starts.items() if v[0] > now - 60}
        for key, grant in list(self.grants.items()):
            if grant.revoked or grant.expires <= now:
                if await self.cleanup(grant):
                    self.grants.pop(key, None)

    async def sweeper(self):
        while True:
            await asyncio.sleep(15)
            try:
                await self.reap()
            except (HTTPException, ValueError):
                # Fail closed; retry cleanup next tick without logging secrets.
                pass

    def install(self, app):
        @app.middleware('http')
        async def cors(request, call_next):
            if not request.url.path.startswith(PREFIX):
                return await call_next(request)
            origin = request.headers.get('origin', '')
            if request.method == 'OPTIONS':
                response = Response(status_code=204 if origin in ORIGINS else 403)
            else:
                response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Vary'] = 'Origin'
            if origin in ORIGINS:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Allow-Headers'] = 'authorization,content-type'
                response.headers['Access-Control-Allow-Methods'] = 'GET,POST,DELETE,OPTIONS'
                response.headers['Access-Control-Expose-Headers'] = 'X-Frame-Sequence,X-Session-ID'
            return response

        @app.post(PREFIX + '/connect')
        async def connect(request: Request):
            origin = self.origin(request)
            # No IPs or connection codes are logged or persisted.
            now = time.monotonic()
            key = digest(request.client.host if request.client else 'unknown')
            start, count = self.starts.get(key, (now, 0))
            if start <= now - 60:
                start, count = now, 0
            if count >= 8 or len(self.pending) >= 100:
                raise HTTPException(429, 'Wait a minute before connecting again')
            self.starts[key] = (start, count + 1)
            code = '-'.join(''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(4)) for _ in range(2))
            device = secrets.token_urlsafe(32)
            self.pending[code] = {'device': digest(device), 'origin': origin, 'expires': now + PAIR_TTL, 'approved': False, 'poll': 0}
            return reply({'device_code': device, 'user_code': code, 'expires_in': PAIR_TTL, 'interval': 3})

        @app.post(PREFIX + '/token')
        async def token(request: Request):
            origin = self.origin(request)
            value = await body(request, {'device_code', 'user_code'})
            code = text(value.get('user_code'), 9)
            pending = self.pending.get(code)
            supplied = digest(text(value.get('device_code'), 64))
            if not pending or pending['expires'] <= time.monotonic() or pending['origin'] != origin or not secrets.compare_digest(supplied, pending['device']):
                raise HTTPException(401, 'Connection code expired or invalid')
            now = time.monotonic()
            if now - pending['poll'] < 2:
                raise HTTPException(429, 'Poll every three seconds')
            pending['poll'] = now
            if not pending['approved']:
                return reply({'status': 'pending'}, 202)
            self.pending.pop(code)
            access = secrets.token_urlsafe(32)
            self.grants[digest(access)] = Grant(origin, now + GRANT_TTL)
            return reply({'access_token': access, 'expires_in': GRANT_TTL, 'status': 'connected'})

        @app.post(PREFIX + '/approve')
        async def approve(request: Request):
            self.csrf(request)
            value = await body(request, {'user_code'})
            code = text(value.get('user_code'), 9).upper()
            pending = self.pending.get(code)
            if not pending or pending['expires'] <= time.monotonic():
                raise HTTPException(404, 'Connection code expired; start again in Open STL')
            if self.grants or any(p['approved'] for p in self.pending.values()):
                raise HTTPException(409, 'Disconnect the existing Open STL connection first')
            pending['approved'] = True
            return reply({'status': 'approved'})

        @app.post(PREFIX + '/disconnect-all')
        async def disconnect_all(request: Request):
            self.csrf(request)
            self.pending.clear()
            for grant in list(self.grants.values()):
                grant.revoked = True
            await self.reap()
            return reply({'status': 'disconnected', 'cleanup_pending': bool(self.grants)})

        @app.delete(PREFIX + '/workspace')
        async def disconnect(request: Request):
            grant = self.grant(request)
            await self.cleanup(grant)
            await self.reap()
            return reply({'status': 'disconnected'})

        @app.get(PREFIX + '/workspace')
        async def workspace(request: Request):
            grant = self.grant(request)
            async with grant.lock:
                self.grant(request)
                chat = await self.snapshot(grant)
                session = (await self.core('GET', '/api/sessions/' + grant.session)).json() if grant.session else None
                return reply({'chat': chat, 'session': session, 'expires_in': max(0, int(grant.expires - time.monotonic()))})

        @app.post(PREFIX + '/chat')
        async def chat(request: Request):
            grant = self.grant(request)
            value = await body(request, {'message'})
            message = text(value.get('message'))
            async with grant.lock:
                self.grant(request)  # Recheck after waiting for another mutation.
                await self.snapshot(grant)
                if not grant.session:
                    raise HTTPException(409, 'Open your workspace browser before starting a task')
                data = {'message': message, 'thread_id': grant.thread, 'session_id': grant.session}
                result = (await self.core('POST', '/api/chat', data)).json()
                grant.thread = str(uuid.UUID(result['thread_id']))
                return reply(result)

        @app.post(PREFIX + '/resume')
        async def resume(request: Request):
            grant = self.grant(request)
            async with grant.lock:
                self.grant(request)
                if not grant.thread:
                    raise HTTPException(409, 'Start a task first')
                return reply((await self.core('POST', '/api/chat/' + grant.thread + '/resume')).json())

        @app.post(PREFIX + '/browser')
        async def browser(request: Request):
            grant = self.grant(request)
            value = await body(request, {'url'})
            url = text(value.get('url'), 2000)
            async with grant.lock:
                self.grant(request)
                await self.snapshot(grant)
                if grant.session:
                    raise HTTPException(409, 'This workspace already has a browser')
                result = (await self.core('POST', '/api/sessions', {'url': url})).json()
                grant.session = str(uuid.UUID(result['id']))
                return reply(result)

        @app.get(PREFIX + '/frame')
        async def frame(request: Request):
            grant = self.grant(request)
            if not grant.session:
                raise HTTPException(404, 'Open a browser first')
            result = await self.core('GET', '/api/sessions/' + grant.session + '/frame')
            self.grant(request)
            return Response(result.content, media_type='image/jpeg', headers={
                'Cache-Control': 'no-store', 'X-Frame-Sequence': result.headers.get('X-Frame-Sequence', ''),
                'X-Session-ID': grant.session})

        @app.post(PREFIX + '/browser/{action}')
        async def action(action: str, request: Request):
            grant = self.grant(request)
            fields = {'navigate': {'url', 'epoch'}, 'control': {'mode', 'epoch'},
                      'input': {'kind', 'epoch', 'x', 'y', 'text', 'key', 'dy'}}
            if action not in fields:
                raise HTTPException(404)
            value = await body(request, fields[action])
            async with grant.lock:
                self.grant(request)
                if not grant.session:
                    raise HTTPException(404, 'Open a browser first')
                return reply((await self.core('POST', '/api/sessions/' + grant.session + '/' + action, value)).json())
