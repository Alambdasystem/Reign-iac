'use strict';

// Run the complete AX controller with simulated DOM and HTTP boundaries. These
// regressions cover recovery/state transitions, not live rendering or providers.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../services/ax/live.js'), 'utf8');
const SESSION = '00000000-0000-4000-8000-000000000001';
const OLD_SESSION = '00000000-0000-4000-8000-000000000002';
const THREAD = '00000000-0000-4000-8000-000000000003';
const NEW_THREAD = '00000000-0000-4000-8000-000000000004';

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.attributes = new Map();
    this.style = {setProperty() {}}; this.classList = {add() {}, remove() {}, toggle() {}};
    this.hidden = false; this.disabled = false; this.value = ''; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'src') this.src = ''; }
  querySelector() { return this.child || (this.child = new Element()); }
  querySelectorAll() { return []; }
  addEventListener() {}
  reset() { this.value = ''; }
  focus() {}
}

async function eventually(check) {
  for (let attempt = 0; attempt < 50; attempt++) {
    if (check()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.ok(check(), 'AX controller did not reach the expected state');
}

function harness({threadStatus = 404, oldBrowser = false, search = '', initiallyAuthenticated = true,
  role = 'owner', cleanupStatus = 200, logoutFailures = 0, voiceFailure = false, holdVoiceCleanup = false,
  portraitMode = 'ready', voiceConfigured = false} = {}) {
  const nodes = new Map(), calls = [], timers = new Map(), navigations = [], windowEvents = new Map(), announcements = [];
  const portraits = [];
  let voiceOptions, voiceStarts = 0;
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const storage = new Map([['reign-session', SESSION], ['reign-thread', THREAD]]);
  let timer = 0, frame = 0;
  const tabs = ['Chat', 'Work'].map(tab => { const element = new Element(); element.dataset.tab = tab; return element; });
  const document = {
    cookie: 'ax_csrf=synthetic-csrf', body: new Element(), hidden: false,
    getElementById: node, querySelector: selector => node(selector),
    querySelectorAll: selector => selector === '[data-tab]' ? tabs : [],
    createElement: () => new Element(),
    createTextNode: value => { const element = new Element(); element.textContent = value; return element; },
    addEventListener() {}
  };
  const session = {id: SESSION, url: 'https://alambda-forge.onrender.com/', title: 'Recovered browser', mode: 'human', epoch: 7};
  const snapshot = (id, status, sessionId = SESSION) => ({
    thread_id: id, run_id: 'synthetic-run', status, session_id: sessionId,
    events: status === 'completed' ? [{sequence: 1, kind: 'completed'}] : [],
    answer: status === 'completed' ? 'Browser status checked.' : '', error: null,
    usage: {input_tokens: 1, output_tokens: 1}, turns: 1, max_turns: 12,
    memory: 'ephemeral', trace_id: '00000000-0000-4000-8000-000000000005',
    trace_spans: [{service: 'core', operation: 'agent.tool', tool_name: 'workspace_status',
      outcome: 'ok', duration_ms: 12, input_tokens: 1, output_tokens: 0},
      {service: 'core', operation: 'agent.tool\\nprivate-password',
       tool_name: 'password=my-fixture-secret', outcome: 'ok', duration_ms: 1}]
  });
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: {'Content-Type': 'application/json'}
  });
  const profile = {authenticated: true, username: role === 'avatar' ? 'mayur' : 'admin',
    display_name: role === 'avatar' ? 'Mayur' : 'Owner', role,
    landing_path: role === 'avatar' ? '/avatar-lab' : '/', capabilities: {ai_chat: role === 'owner', avatar_lab: true}};
  const fetch = async (url, options) => {
    const request = {path: url, method: options.method, body: options.body ? JSON.parse(options.body) : undefined};
    calls.push(request);
    if (url === '/api/me') return initiallyAuthenticated ? json(profile) : json({}, 401);
    if (url === '/login' && options.method === 'POST') return json(profile);
    if (url === '/logout' && options.method === 'POST') {
      if (logoutFailures-- > 0) throw new Error('Synthetic disconnected network');
      return json({status: 'signed_out'});
    }
    if (options.method === 'DELETE') return json({}, cleanupStatus);
    if (url === '/api/agent') return json({configured: true});
    if (url === '/api/voice') return json({configured: voiceConfigured});
    if (url === '/api/status') return json({status: 'ready'});
    if (url === '/api/sessions/' + SESSION) return json(session);
    if (url === '/api/sessions/' + OLD_SESSION) return json({}, 404);
    if (url === '/api/sessions/' + SESSION + '/frame') return new Response('synthetic-frame', {
      headers: {'Content-Type': 'image/jpeg', 'X-Session-ID': SESSION, 'X-Frame-Sequence': String(++frame)}
    });
    if (url === '/api/chat/' + THREAD) return typeof threadStatus === 'number'
      ? json({}, threadStatus) : json(snapshot(THREAD, threadStatus, oldBrowser ? OLD_SESSION : SESSION));
    if (url === '/api/chat' && options.method === 'POST') return json(snapshot(NEW_THREAD, 'completed'));
    if (url === '/api/chat/' + NEW_THREAD && options.method === 'GET') return json(snapshot(NEW_THREAD, 'completed'));
    throw new Error('Unexpected simulated request: ' + options.method + ' ' + url);
  };
  class TestURL extends URL {
    static createObjectURL() { return 'blob:synthetic-frame-' + frame; }
    static revokeObjectURL() {}
  }
  const context = vm.createContext({
    document, window: {addEventListener: (event, handler) => windowEvents.set(event, handler),
      REIGNAvatar: portraitMode === 'missing' ? undefined : {createReignAvatar(options) {
        const value = {visibleWhenMounted: !node('console').hidden, speaking: false, level: 0, destroyed: false,
          setSpeaking(active) { this.speaking = active; if (!active) this.level = 0; },
          setSpeechLevel(level) { this.level = level; }, stop() { this.speaking = false; this.level = 0; },
          destroy() { this.stop(); this.destroyed = true; }};
        value.ready = Promise.resolve().then(() => {
          if (!value.destroyed) options.onStatus({state: portraitMode === 'error' ? 'error' : 'ready',
            message: portraitMode === 'error' ? 'REIGN’s face could not load.' : 'REIGN is ready.'});
          return portraitMode !== 'error';
        });
        portraits.push(value); return value;
      }},
      location: {search, replace: target => navigations.push(target)}},
    fetch, Response, AbortController, URL: TestURL, URLSearchParams,
    sessionStorage: {getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key)},
    localStorage: {setItem: (key, value) => announcements.push({key, value})},
    setTimeout: (callback, delay) => { timers.set(++timer, {callback, delay}); return timer; },
    clearTimeout: id => timers.delete(id), setInterval() {},
    run: false, voiceState: 'idle', activity: 0, animate() {},
    REIGNVoice: {createVoiceController: options => { voiceOptions = options; return {stop: async () => {
      if (voiceFailure) throw Error('Synthetic voice cleanup failure');
      if (holdVoiceCleanup) await new Promise(() => {});
    }, mute() {}, start() { voiceStarts++; }, enableSpeaker() {}}; }}
  });
  vm.runInContext(source, context, {filename: 'services/ax/live.js'});
  return {node, calls, storage, session, timers, navigations, windowEvents, announcements, portraits,
    get voiceOptions() { return voiceOptions; }, get voiceStarts() { return voiceStarts; },
    ready: () => eventually(() => calls.some(call => call.path === '/api/status') && !node('cancelAgent').disabled)};
}

function assertBrowserPreserved(h) {
  assert.equal(h.node('session').textContent, SESSION);
  assert.equal(h.node('controller').textContent, 'human');
  assert.equal(h.node('epoch').textContent, '7');
  assert.equal(h.node('connection').textContent, 'CONNECTED');
  assert.equal(h.node('screen').hidden, false);
  assert.match(h.node('screen').src, /^blob:synthetic-frame-/);
  assert.equal(h.storage.get('reign-session'), SESSION);
}

test('private REIGN portrait mounts after authenticated UI and only output audio drives it', async () => {
  const h = harness({voiceConfigured: true}); await h.ready();
  assert.equal(h.portraits.length, 1); const portrait = h.portraits[0];
  assert.equal(portrait.visibleWhenMounted, true);
  h.voiceOptions.onState({active: true, phase: 'listening', muted: false, speakerBlocked: false, status: 'Listening'});
  h.voiceOptions.onLevel(.8);
  assert.equal(portrait.speaking, false); assert.equal(portrait.level, 0);
  h.voiceOptions.onState({active: true, phase: 'speaking', muted: false, speakerBlocked: false, status: 'Speaking'});
  assert.equal(portrait.level, 0, 'provider phase alone must not simulate speech');
  h.voiceOptions.onOutputLevel(.4);
  assert.equal(portrait.speaking, true); assert.equal(portrait.level, .4);
  h.voiceOptions.onOutputLevel(0);
  assert.equal(portrait.speaking, false); assert.equal(portrait.level, 0);
  h.voiceOptions.onOutputLevel(.4);
  h.voiceOptions.onState({active: false, phase: 'ended', speakerBlocked: false, status: 'Ended'});
  assert.equal(portrait.speaking, false); assert.equal(portrait.level, 0);
  h.windowEvents.get('pagehide')(); assert.equal(portrait.destroyed, true);
  h.voiceOptions.onOutputLevel(.8); assert.equal(portrait.level, 0, 'late output cannot animate a retired portrait');
});

test('portrait failure or unavailable module does not disable an otherwise available voice call', async () => {
  for (const portraitMode of ['error', 'missing']) {
    const h = harness({portraitMode, voiceConfigured: true}); await h.ready();
    assert.equal(h.node('voiceStart').disabled, false);
    h.node('voiceStart').onclick(); assert.equal(h.voiceStarts, 1);
    if (portraitMode === 'error') assert.match(h.node('portraitStatus').textContent, /face could not load/);
  }
});

test('restricted and signed-out users never mount the private portrait controller', async () => {
  const signedOut = harness({initiallyAuthenticated: false});
  await eventually(() => signedOut.calls.some(call => call.path === '/api/me'));
  signedOut.windowEvents.get('reign:avatar-module-ready')();
  assert.equal(signedOut.portraits.length, 0);
  const restricted = harness({role: 'avatar'});
  await eventually(() => restricted.navigations.length === 1);
  restricted.windowEvents.get('reign:avatar-module-ready')();
  assert.equal(restricted.portraits.length, 0);
});

test('reload clears only an expired chat reference and the next prompt uses the recovered browser', async () => {
  const h = harness();
  await h.ready();
  assertBrowserPreserved(h);
  assert.equal(h.storage.has('reign-thread'), false);
  assert.equal(h.node('sendCommand').disabled, false);
  assert.equal(h.node('cancelAgent').hidden, true);
  assert.match(h.node('agentLabel').textContent, /new conversation.*expired/i);
  assert.match(h.node('feed').textContent, /previous conversation expired/i);
  assert.doesNotMatch(h.node('feed').textContent, /this session has expired|connection interrupted/i);
  assert.equal(h.node('feed').children.some(row => row.className.includes('error')), false);
  assert.ok(h.calls.every(call => call.method === 'GET'), 'recovery must perform no mutations');

  h.node('message').value = 'Use browser_status to inspect the recovered browser.';
  h.node('form').onsubmit({preventDefault() {}});
  await eventually(() => h.node('agentLabel').textContent.startsWith('Ready ·'));
  const writes = h.calls.filter(call => call.method !== 'GET');
  assert.deepEqual(writes, [{path: '/api/chat', method: 'POST', body: {
    message: 'Use browser_status to inspect the recovered browser.', thread_id: null, session_id: SESSION
  }}]);
  assert.equal(h.storage.get('reign-thread'), NEW_THREAD);
  assertBrowserPreserved(h);
});

for (const status of ['running', 'paused']) {
  test('reload preserves a ' + status + ' task even when its older browser has expired', async () => {
    const h = harness({threadStatus: status, oldBrowser: true});
    await h.ready();
    assertBrowserPreserved(h);
    assert.equal(h.storage.get('reign-thread'), THREAD);
    assert.equal(h.node('sendCommand').disabled, true);
    assert.equal(h.node('cancelAgent').hidden, false);
    assert.equal(h.node('resumeAgent').hidden, status !== 'paused');
    if (status === 'paused') assert.equal(h.node('resumeAgent').disabled, true);
    assert.match(h.node('agentLabel').textContent, status === 'paused' ? /waiting for you/i : /working/i);
    assert.match(h.node('feed').textContent, /run remains tracked/i);
    assert.ok(h.calls.some(call => call.path === '/api/sessions/' + OLD_SESSION));
    assert.ok(h.calls.every(call => call.method === 'GET'), 'recovery must not cancel, resume or mutate a task');
  });
}

test('a temporary chat lookup failure keeps the thread and reports an interrupted connection', async () => {
  const h = harness({threadStatus: 503});
  await h.ready();
  assertBrowserPreserved(h);
  assert.equal(h.storage.get('reign-thread'), THREAD);
  assert.equal(h.node('sendCommand').disabled, true);
  assert.equal(h.node('agentLabel').textContent, 'Agent connection interrupted');
  assert.doesNotMatch(h.node('feed').textContent, /previous conversation expired/i);
  assert.ok(h.calls.every(call => call.method === 'GET'));
});

test('current-run trace inspector shows allowlisted metadata but no untrusted tool text', async () => {
  const h = harness();
  await h.ready();
  h.node('message').value = 'Show the workspace status.';
  h.node('form').onsubmit({preventDefault() {}});
  await eventually(() => h.node('agentLabel').textContent.startsWith('Ready ·'));
  const text = h.node('traceOutput').textContent;
  assert.match(text, /Ephemeral.*Core restart/);
  assert.match(text, /core \/ agent\.tool · workspace_status · ok · 12 ms/);
  assert.doesNotMatch(text, /my-fixture-secret|private-password|password=/);
  assert.match(text, /Trace events: 2/);
});

test('a restored session follows the Avatar Lab return path without touching a workspace', async () => {
  const h = harness({search: '?next=%2Favatar-lab'});
  await eventually(() => h.navigations.length === 1);
  assert.deepEqual(h.navigations, ['/avatar-lab']);
  assert.deepEqual(h.calls.map(call => call.path), ['/api/me']);
  assert.equal(h.storage.get('reign-session'), SESSION);
  assert.equal(h.storage.get('reign-thread'), THREAD);
});

test('sign-in follows the same return path only after successful authentication', async () => {
  const h = harness({search: '?next=%2Favatar-lab', initiallyAuthenticated: false});
  await eventually(() => h.node('connection').textContent === 'SIGN IN');
  assert.deepEqual(h.navigations, []);
  h.node('password').value = 'disposable-test-password';
  await h.node('loginForm').onsubmit({preventDefault() {}, currentTarget: h.node('loginForm')});
  assert.deepEqual(h.navigations, ['/avatar-lab']);
  assert.deepEqual(h.calls.map(call => call.path), ['/api/me', '/login']);
  assert.equal(h.node('password').value, '');
});

test('untrusted or duplicate return paths never become redirects', async () => {
  for (const search of [
    '?next=https%3A%2F%2Fevil.example%2F', '?next=%2F%2Fevil.example',
    '?next=javascript%3Aalert(1)', '?next=%252Favatar-lab',
    '?next=%2Favatar-lab%2F', '?next=%2Favatar-lab%3Fnext%3Dhttps%3A%2F%2Fevil.example',
    '?next=%2Favatar-lab%23fragment', '?next=%2Favatar-lab%00',
    '?next=%2Favatar-lab&next=%2Favatar-lab', '?next=%2Favatar-lab&next=https%3A%2F%2Fevil.example',
  ]) {
    const h = harness({search});
    await h.ready();
    assert.deepEqual(h.navigations, [], search);
    assertBrowserPreserved(h);
  }
});

test('a restored avatar account goes straight to the lab without owner workspace reads', async () => {
  const h = harness({role: 'avatar', search: '?next=https%3A%2F%2Fevil.example'});
  await eventually(() => h.navigations.length === 1);
  assert.deepEqual(h.navigations, ['/avatar-lab']);
  assert.deepEqual(h.calls.map(call => call.path), ['/api/me']);
  assert.equal(h.storage.get('reign-session'), SESSION);
  assert.equal(h.storage.get('reign-thread'), THREAD);
});

test('named sign-in sends the username and honors the returned avatar role', async () => {
  const h = harness({role: 'avatar', initiallyAuthenticated: false});
  await eventually(() => h.node('connection').textContent === 'SIGN IN');
  h.node('username').value = ' mayur ';
  h.node('password').value = 'synthetic-named-password';
  await h.node('loginForm').onsubmit({preventDefault() {}, currentTarget: h.node('loginForm')});
  assert.deepEqual(h.calls.map(call => call.path), ['/api/me', '/login']);
  assert.deepEqual(h.calls[1].body, {username: 'mayur', password: 'synthetic-named-password'});
  assert.deepEqual(h.navigations, ['/avatar-lab']);
  assert.equal(h.node('password').value, '');
});

test('an unrecognized account role never opens owner surfaces', async () => {
  const h = harness({role: 'unexpected'});
  await eventually(() => h.node('connection').textContent === 'SIGN IN');
  assert.deepEqual(h.calls.map(call => call.path), ['/api/me']);
  assert.deepEqual(h.navigations, []);
});

test('logout is still sent after voice and owner cleanup fail', async () => {
  const h = harness({threadStatus: 'completed', voiceFailure: true, cleanupStatus: 503});
  await h.ready();
  await h.node('logout').onclick();
  assert.ok(h.calls.some(call => call.path === '/api/chat/' + THREAD && call.method === 'DELETE'));
  assert.ok(h.calls.some(call => call.path === '/api/sessions/' + SESSION && call.method === 'DELETE'));
  assert.equal(h.calls.filter(call => call.path === '/logout').length, 1);
  assert.deepEqual(h.navigations, ['/']);
  assert.equal(h.announcements.length, 1);
});

test('expired-session cleanup cannot prevent the idempotent logout request', async () => {
  const h = harness({threadStatus: 'completed', cleanupStatus: 401});
  await h.ready();
  await h.node('logout').onclick();
  assert.equal(h.calls.filter(call => call.path === '/logout').length, 1);
  assert.deepEqual(h.navigations, ['/']);
  assert.equal(h.node('loginPanel').hidden, false);
});

test('a stalled cleanup has a bounded wait before logout', async () => {
  const h = harness({holdVoiceCleanup: true});
  await h.ready();
  const logout = h.node('logout').onclick();
  await eventually(() => [...h.timers.values()].some(timer => timer.delay === 2500));
  [...h.timers.values()].find(timer => timer.delay === 2500).callback();
  await logout;
  assert.equal(h.calls.filter(call => call.path === '/logout').length, 1);
  assert.deepEqual(h.navigations, ['/']);
});

test('an unconfirmed logout stays visible and can be retried without a false success', async () => {
  const h = harness({logoutFailures: 1});
  await h.ready();
  await h.node('logout').onclick();
  assert.deepEqual(h.navigations, []);
  assert.deepEqual(h.announcements, []);
  assert.equal(h.node('logout').hidden, false);
  assert.equal(h.node('logout').disabled, false);
  assert.equal(h.node('logout').textContent, 'Retry log out');
  assert.equal(h.node('console').hidden, false);
  assert.match(h.node('status').textContent, /Couldn’t confirm log out/);
  await h.node('logout').onclick();
  assert.deepEqual(h.navigations, ['/']);
  assert.equal(h.announcements.length, 1);
});

test('another tab’s signout clears this surface without owner server mutations', async () => {
  const h = harness();
  await h.ready();
  h.windowEvents.get('storage')({key: 'unrelated', newValue: '1'});
  assert.deepEqual(h.navigations, []);
  h.windowEvents.get('storage')({key: 'reign:ax:signed-out', newValue: 'synthetic-event'});
  assert.deepEqual(h.navigations, ['/']);
  assert.ok(h.calls.every(call => call.method === 'GET'));
  assert.equal(h.node('console').hidden, true);
  assert.equal(h.storage.has('reign-session'), false);
});
