import os
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
from reign.ax import app, openstl

GAME = 'https://open-stl-3a6l.onrender.com'
AX = 'https://ax.example.com'
SETTINGS = {'AX_SESSION_KEY': 's' * 40, 'AX_ADMIN_PASSWORD': 'p' * 40,
            'CORE_TOKEN': 'c' * 40, 'AX_PUBLIC_ORIGIN': AX}


class OpenSTLTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, SETTINGS)
        self.env.start()
        openstl.pending.clear(); openstl.grants.clear(); openstl.starts.clear()
        self.client = TestClient(app, base_url=AX)
        self.client.__enter__()
        self.core = patch.object(openstl, 'core', new_callable=AsyncMock).start()
        self.sid, self.tid = str(uuid.uuid4()), str(uuid.uuid4())
        self.core.side_effect = self.respond

    async def respond(self, method, path, data=None):
        if path == '/api/sessions':
            return httpx.Response(200, json={'id': self.sid, 'mode': 'agent', 'epoch': 1, 'url': 'https://alambda-forge.onrender.com/'})
        if path == '/api/chat':
            return httpx.Response(200, json={'thread_id': self.tid, 'status': 'running', 'session_id': self.sid})
        if '/frame' in path:
            return httpx.Response(200, content=b'jpeg', headers={'X-Frame-Sequence': '7'})
        if path.startswith('/api/chat/') and method == 'GET':
            return httpx.Response(200, json={'thread_id': self.tid, 'status': 'done', 'session_id': self.sid, 'answer': 'Done'})
        return httpx.Response(200, json={'id': self.sid, 'mode': 'human', 'epoch': 2})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        patch.stopall(); self.env.stop()

    def pair(self, origin=GAME):
        r = self.client.post('/api/openstl/connect', headers={'Origin': origin}, json={})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def sign_in(self):
        self.client.post('/login', json={'password': 'p' * 40})
        return {'Origin': AX, 'X-CSRF-Token': self.client.cookies.get('ax_csrf')}

    def authorize(self, origin=GAME):
        pair = self.pair(origin)
        r = self.client.post('/api/openstl/approve', headers=self.sign_in(), json={'user_code': pair['user_code']})
        self.assertEqual(r.status_code, 200)
        r = self.client.post('/api/openstl/token', headers={'Origin': origin}, json={k: pair[k] for k in ('user_code', 'device_code')})
        self.assertEqual(r.status_code, 200)
        return {'Origin': origin, 'Authorization': 'Bearer ' + r.json()['access_token']}, pair

    def test_requires_real_signin_csrf_and_exact_origin(self):
        self.assertEqual(self.client.post('/api/openstl/connect', headers={'Origin': 'https://evil.test'}, json={}).status_code, 403)
        pair = self.pair()
        self.assertEqual(self.client.post('/api/openstl/approve', json={'user_code': pair['user_code']}).status_code, 401)
        self.sign_in()
        self.assertEqual(self.client.post('/api/openstl/approve', json={'user_code': pair['user_code']}).status_code, 403)
        self.assertEqual(self.client.get('/api/openstl/workspace', headers={'Origin': GAME}).status_code, 401)
        self.core.assert_not_called()
        page = self.client.get('/connect/openstl')
        self.assertEqual(page.headers['x-frame-options'], 'DENY')
        self.assertEqual(page.headers['referrer-policy'], 'no-referrer')

    def test_device_secret_single_use_and_origin_binding(self):
        headers, pair = self.authorize()
        r = self.client.post('/api/openstl/token', headers={'Origin': GAME}, json={k: pair[k] for k in ('device_code', 'user_code')})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get('/api/openstl/workspace', headers={**headers, 'Origin': 'https://localhost'}).status_code, 401)
        self.assertEqual(self.client.get('/api/openstl/workspace', headers=headers).status_code, 200)
        other = self.pair()
        self.assertEqual(self.client.post('/api/openstl/approve', headers=self.sign_in(), json={'user_code': other['user_code']}).status_code, 409)

    def test_wrong_device_secret_cannot_poll_and_pending_never_has_token(self):
        pair = self.pair()
        payload = {k: pair[k] for k in ('device_code', 'user_code')}
        bad = self.client.post('/api/openstl/token', headers={'Origin': GAME}, json={**payload, 'device_code': 'wrong'})
        self.assertEqual(bad.status_code, 401)
        pending = self.client.post('/api/openstl/token', headers={'Origin': GAME}, json=payload)
        self.assertEqual(pending.status_code, 202)
        self.assertEqual(pending.json(), {'status': 'pending'})
        self.assertEqual(self.client.post('/api/openstl/token', headers={'Origin': GAME}, json=payload).status_code, 429)

    def test_browser_chat_frame_control_and_revoke_are_connection_scoped(self):
        headers, pair = self.authorize('https://localhost')
        self.assertEqual(self.client.post('/api/openstl/chat', headers=headers, json={'message': 'hi'}).status_code, 409)
        self.assertEqual(self.client.post('/api/openstl/browser', headers=headers, json={'url': 'https://alambda-forge.onrender.com/'}).status_code, 200)
        self.assertEqual(self.client.post('/api/openstl/chat', headers=headers, json={'message': 'Read this page', 'session_id': str(uuid.uuid4())}).status_code, 422)
        r = self.client.post('/api/openstl/chat', headers=headers, json={'message': 'Read this page'})
        self.assertEqual(r.status_code, 200)
        self.core.assert_any_await('POST', '/api/chat', {'message': 'Read this page', 'thread_id': None, 'session_id': self.sid})
        frame = self.client.get('/api/openstl/frame', headers=headers)
        self.assertEqual(frame.content, b'jpeg')
        self.assertEqual(frame.headers['access-control-allow-origin'], 'https://localhost')
        self.assertEqual(frame.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.post('/api/openstl/browser/control', headers=headers, json={'mode': 'human', 'epoch': 1}).status_code, 200)
        self.assertEqual(self.client.get('/api/openstl/workspace', headers=headers).json()['chat']['answer'], 'Done')
        self.assertEqual(self.client.delete('/api/openstl/workspace', headers=headers).status_code, 200)
        self.core.assert_any_await('DELETE', '/api/chat/' + self.tid)
        self.core.assert_any_await('DELETE', '/api/sessions/' + self.sid)
        self.assertEqual(self.client.get('/api/openstl/frame', headers=headers).status_code, 401)

    def test_expiry_logout_and_no_arbitrary_proxy(self):
        headers, _ = self.authorize()
        self.assertEqual(self.client.post('/api/openstl/browser/accounts', headers=headers, json={}).status_code, 404)
        grant = next(iter(openstl.grants.values()))
        grant.expires = time.monotonic() - 1
        self.assertEqual(self.client.post('/api/openstl/chat', headers=headers, json={'message': 'hi'}).status_code, 401)
        self.assertEqual(self.client.post('/logout', headers=self.sign_in()).status_code, 200)
        self.assertFalse(openstl.grants)

    def test_body_limits_and_preflight(self):
        headers, _ = self.authorize()
        self.assertEqual(self.client.post('/api/openstl/chat', headers=headers, content='x' * 9000).status_code, 413)
        r = self.client.options('/api/openstl/chat', headers={'Origin': GAME, 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(r.status_code, 204)
        self.assertNotIn('access-control-allow-credentials', r.headers)
        self.assertEqual(self.client.options('/api/openstl/chat', headers={'Origin': 'https://evil.test'}).status_code, 403)
