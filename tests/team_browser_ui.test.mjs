import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../services/ax/team-browser.js', import.meta.url), 'utf8');
const {createTeamBrowser} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const pending = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return {promise, resolve}; };
async function until(check) { for (let i = 0; i < 60; i++) { if (check()) return; await new Promise(resolve => setImmediate(resolve)); } assert.ok(check()); }
class Element {
  constructor() { this.listeners = new Map(); this.dataset = {}; this.value = ''; this.textContent = ''; this.hidden = false; this.open = false; this.disabled = false; this.classList = {toggle() {}}; }
  addEventListener(type, listener) { this.listeners.set(type, listener); } removeEventListener(type) { this.listeners.delete(type); }
  emit(type, payload = {}) { this.listeners.get(type)?.({preventDefault() {}, ...payload}); }
  removeAttribute(key) { delete this[key]; }
  getBoundingClientRect() { return {left: 10, top: 20, width: 640, height: 400}; }
}
const room = id => ({roomId: id, connectionId: 'viewer-1', me: {id: 'user-1'}, enabled: true});
const status = (mode = 'agent', holder = 'viewer-1') => ({status: 'active', session: {id: 'session-1', epoch: 7, mode, title: 'Allowed site', url: 'https://allowed.test', viewport: {width: 1280, height: 800}},
  control: mode === 'human' ? {connection_id: holder, user_id: 'user-1', display_name: 'Teammate', epoch: 7} : null, allowed_hosts: ['allowed.test']});
function harness(handler) {
  const nodes = new Map(), calls = [], timers = new Map(), urls = [], revoked = []; let id = 0;
  const node = key => { if (!nodes.has(key)) nodes.set(key, new Element()); return nodes.get(key); };
  const document = new Element(); document.getElementById = node;
  const browser = createTeamBrowser({document, window: {confirm: () => true},
    api: async (path, method = 'GET', body, options) => { const request = {path, method, body, options}; calls.push(request); return handler(request); },
    URL: {createObjectURL: () => { const value = 'blob:' + urls.length; urls.push(value); return value; }, revokeObjectURL: value => revoked.push(value)},
    timers: {setTimeout(fn, delay) { timers.set(++id, {fn, delay}); return id; }, clearTimeout(key) { timers.delete(key); }}});
  return {browser, node, calls, urls, revoked, timers,
    expand() { node('browserPanel').open = true; node('browserPanel').emit('toggle'); },
    collapse() { node('browserPanel').open = false; node('browserPanel').emit('toggle'); }};
}

test('restoring a room never allocates a browser, including when the panel is expanded', async () => {
  const h = harness(async () => ({status: 'none', session: null, allowed_hosts: ['allowed.test']}));
  h.browser.setRoom(room('room-a')); assert.equal(h.calls.length, 0); h.expand(); await until(() => h.calls.length === 1);
  assert.ok(h.calls.every(item => item.method === 'GET'));
  h.node('browserUrl').value = 'https://allowed.test'; h.node('browserForm').emit('submit'); await until(() => h.calls.some(item => item.method === 'POST'));
  assert.deepEqual(h.calls.find(item => item.method === 'POST').body, {url: 'https://allowed.test'}); h.browser.dispose();
});

test('closing a panel aborts discovery and ignores a late response without fetching a frame', async () => {
  const held = pending(), h = harness(() => held.promise); h.browser.setRoom(room('room-a')); h.expand(); h.collapse();
  held.resolve(status()); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 1); assert.equal(h.urls.length, 0); assert.equal(h.timers.size, 0); h.browser.dispose();
});

test('an old room frame cannot appear after the person switches to another room', async () => {
  const frame = pending(), h = harness(request => request.path.endsWith('/frame') ? frame.promise : Promise.resolve(request.path.includes('/room-a/') ? status() : {status: 'none', session: null}));
  h.browser.setRoom(room('room-a')); h.expand(); await until(() => h.calls.some(item => item.path.endsWith('/frame')));
  h.browser.setRoom(room('room-b')); frame.resolve(new Blob(['old room'])); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.urls.length, 0); assert.equal(h.node('browserFrame').hidden, true); h.browser.dispose();
});

test('only this connection holder can send scaled pointer input with the current epoch', async () => {
  let value = status('human', 'another-viewer');
  const h = harness(request => request.path.endsWith('/frame') ? Promise.resolve(new Blob(['frame'])) : Promise.resolve(value));
  h.browser.setRoom(room('room-a')); h.expand(); await until(() => h.urls.length === 1);
  h.node('browserFrame').emit('click', {clientX: 330, clientY: 220}); assert.equal(h.calls.filter(item => item.method === 'POST').length, 0);
  value = status('human'); h.node('refreshBrowser').emit('click'); await until(() => h.urls.length === 2);
  h.node('browserFrame').emit('click', {clientX: 330, clientY: 220}); await until(() => h.calls.some(item => item.path.endsWith('/input')));
  assert.deepEqual(h.calls.find(item => item.path.endsWith('/input')).body, {connection_id: 'viewer-1', epoch: 7, kind: 'click', x: 640, y: 400});
  h.browser.dispose(); assert.equal(h.revoked.length, h.urls.length);
});

test('disposal releases frame buffers and stops polling without deleting the shared browser', async () => {
  const h = harness(request => Promise.resolve(request.path.endsWith('/frame') ? new Blob(['frame']) : status()));
  h.browser.setRoom(room('room-a')); h.expand(); await until(() => h.urls.length === 1); h.browser.dispose();
  assert.equal(h.revoked.length, 1); assert.equal(h.timers.size, 0); assert.equal(h.node('browserFrame').hidden, true);
  assert.ok(h.calls.every(item => item.method === 'GET'));
});
