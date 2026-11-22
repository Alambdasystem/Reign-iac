import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../services/ax/team.js', import.meta.url), 'utf8')
  .replace(/^import[^\n]*\n/gm, '').replace('export function createTeamWorkspace', 'function createTeamWorkspace')
  .replace("if (typeof document !== 'undefined') void createTeamWorkspace().start();", '');
const html = fs.readFileSync(new URL('../services/ax/team.html', import.meta.url), 'utf8');
const pending = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
async function until(check) { for (let i = 0; i < 60; i++) { if (check()) return; await new Promise(resolve => setImmediate(resolve)); } assert.ok(check(), 'workspace did not reach expected state'); }
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = new Map(); this.dataset = {}; this.value = ''; this.textContent = ''; this.disabled = false; this.hidden = false; this.checked = false; this.open = false; this.scrollHeight = 0; this.scrollTop = 0; this.clientHeight = 0; this.queries = new Map(); }
  addEventListener(event, handler) { if (!this.listeners.has(event)) this.listeners.set(event, new Set()); this.listeners.get(event).add(handler); }
  removeEventListener(event, handler) { this.listeners.get(event)?.delete(handler); }
  emit(event, value = {}) { const ev = {preventDefault() {}, target: this, currentTarget: this, ...value}; for (const handler of this.listeners.get(event) || []) handler(ev); }
  setAttribute(key, value) { this[key] = String(value); }
  removeAttribute(key) { delete this[key]; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  querySelector(selector) { if (!this.queries.has(selector)) this.queries.set(selector, new Element(selector)); return this.queries.get(selector); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
  focus() {} click() { this.emit('click'); } reset() {} play() { return Promise.resolve(); } pause() {}
  showModal() { this.open = true; } close() { this.open = false; this.emit('close'); }
  get options() { return this.children; }
}
const profile = (role = 'owner') => ({workspace: {id: 'rain', name: 'Workspace', durable: true}, me: {id: 'user-1', role, username: 'tester', display_name: 'CI Teammate'}, members: [],
  rooms: [{id: 'room-a', title: 'Planning'}, {id: 'room-b', title: 'Research'}], capabilities: {assistant: true, speech: true, calls: true, documents: true, manage_members: role === 'owner', vision: true, transcription: true, live_voice: true}});
const event = (seq, text, extra = {}) => ({id: 'event-' + seq, seq, type: 'message', user_id: 'user-1', display_name: 'CI Teammate', created_at: 1700000000, data: {text}, ...extra});
const snapshot = id => ({room: {id, title: id === 'room-a' ? 'Planning' : 'Research'}, events: [], documents: [{id: 'document-1', title: 'Shared plan', version: 1}], presence: [], assistant: {status: 'idle'}, last_seq: 0});
function harness({handle, role, leaveThrows = false, cameraCapture, injectLayout = false} = {}) {
  const nodes = new Map(), requests = [], timers = new Map(), polls = [], recordings = [], redirects = [], stored = [], media = {joins: 0, leaves: 0, disposed: 0}, avatar = {played: [], stopped: 0, destroyed: 0, unlocked: 0};
  let timer = 0, id = 0, options;
  const camera = {started: 0, stopped: 0, captures: 0, disposed: 0};
  const voice = {starts: [], stops: [], disposed: 0, muted: false, active: false, own: new Set(), accepted: []}; let voiceOptions;
  const layout = {created: [], destroyed: 0, mediaAtDestroy: null};
  const layoutFactory = origin => config => {
    layout.created.push({origin, config});
    return {destroy() {
      layout.destroyed++;
      layout.mediaAtDestroy = {call: media.disposed, avatar: avatar.destroyed, voice: voice.disposed, camera: camera.disposed};
    }};
  };
  const picture = () => ({blob: new Blob([new Uint8Array([255, 216, 255, 217])], {type: 'image/jpeg'}), capturedAt: '2026-11-22T12:00:00.000Z'});
  for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element());
  const node = name => { assert.ok(nodes.has(name), 'unknown DOM ID ' + name); return nodes.get(name); };
  node('askReign').dataset.action = 'ask'; node('sendMessage').dataset.action = 'send';
  const doc = new Element('document'); Object.assign(doc, {getElementById: node, createElement: tag => new Element(tag), body: new Element('body'), cookie: 'ax_csrf=synthetic-token'});
  const win = new Element('window'); Object.assign(win, {location: {origin: 'https://ax.test', replace: target => redirects.push(target), reload() {}}, sessionStorage: {getItem() { return null; }, setItem() {}}, localStorage: {setItem: (key, value) => stored.push({key, value})}, navigator: {clipboard: {writeText: async () => {}}}, confirm: () => true, dispatchEvent() {}});
  const response = (value, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => value});
  const fetcher = async (url, init) => {
    const request = {url, method: init.method, body: init.body === undefined ? undefined : JSON.parse(init.body), init}; requests.push(request);
    const custom = await handle?.(request, {response, snapshot, profile: profile(role)}); if (custom !== undefined) return custom;
    if (url === '/api/team') return response(profile(role));
    if (url === '/logout') return response({ok: true});
    if (url.includes('/events?')) { const task = pending(); polls.push({url, ...task}); return task.promise; }
    if (/\/presence(?:\/|$)/.test(url)) return response({presence: []});
    if (/\/documents\//.test(url) && init.method === 'GET') return response({document: {id: 'document-1', title: 'Shared plan', content: '# Original', version: 1}});
    if (/\/rooms\/room-[ab]$/.test(url)) return response(snapshot(url.split('/').at(-1)));
    throw Error('Unexpected request: ' + url);
  };
  const context = vm.createContext({console, AbortController, Blob, TextEncoder, CustomEvent: class { constructor(type, value) { this.type = type; Object.assign(this, value); } },
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}}, FileReader: class {
      readAsDataURL(blob) { void blob.arrayBuffer().then(data => { this.result = 'data:' + blob.type + ';base64,' + Buffer.from(data).toString('base64'); this.onload?.(); }); }
    },
    createCallspaceWorkspace: layoutFactory('imported'),
    createTeamCall: config => { options = config; return {join() { media.joins++; return Promise.resolve(); }, leave() { media.leaves++; if (leaveThrows) throw Error('cleanup failed'); return Promise.resolve(); }, syncPresence() {}, dispose() { media.disposed++; }, setMicrophone() {}, setCamera() {}, setScreen() {}, captureFrame: async () => picture()}; },
    createTeamAvatar: config => { config.onStatus({state: 'ready', message: 'Ready'}); return {play(room, value) { avatar.played.push({room, value}); }, unlock: async () => { avatar.unlocked++; return true; }, stop() { avatar.stopped++; }, destroy() { avatar.destroyed++; }, startLivePlayback() { avatar.stopped++; }, endLivePlayback() {}, setLivePlayback(value) { avatar.livePlayback = value; }}; },
    createTeamVoice: config => { voiceOptions = config; return {
      start(value) { voice.starts.push(value); voice.active = true; voice.own.add('voice-session-1'); config.onState({active: true, phase: 'listening', muted: false, voiceSessionId: 'voice-session-1', status: 'Microphone on. Speak naturally.'}); return Promise.resolve(); },
      stop(value) { voice.stops.push(value); voice.active = false; config.onState({active: false, phase: 'ended', muted: false, status: value?.reason || 'Microphone off.'}); return Promise.resolve(); },
      mute() { voice.muted = !voice.muted; config.onState({active: true, phase: 'listening', muted: voice.muted, status: voice.muted ? 'Microphone muted.' : 'Microphone on.'}); return voice.muted; },
      acceptEvents(room, events) { voice.accepted.push({room, events}); }, suppressPlayback(room, event) { return voice.own.has(event.data?.voice_session_id); },
      enableSpeaker() { return Promise.resolve(); }, retry() {}, dispose() { voice.disposed++; return Promise.resolve(); },
    }; },
    createTeamRecorder: config => ({start(options) { const value = {...pending(), options}; recordings.push(value); config.onState({state: 'recording', message: 'Recording'}); return value.promise; },
      cancel() { recordings.at(-1)?.reject(Object.assign(Error('cancelled'), {name: 'AbortError'})); config.onState({state: 'idle'}); },
      stop() { const value = recordings.at(-1); value?.resolve({audio_base64: 'c3ludGhldGlj', mime_type: 'audio/webm'}); config.onState({state: 'idle'}); return value?.promise || Promise.resolve(); }, dispose() {}}),
    createTeamBrowser: () => ({setRoom() {}, dispose() {}}),
    createTeamCamera: config => ({
      async start() { camera.started++; config.video.srcObject = {localCamera: true}; config.onState({phase: 'ready', active: true, message: 'Private preview.'}); return true; },
      stop() { camera.stopped++; config.video.srcObject = null; config.onState({phase: 'idle', active: false, message: 'Camera stopped.'}); },
      async capture() { camera.captures++; if (cameraCapture) await cameraCapture.promise; return picture(); },
      dispose() { camera.disposed++; },
    })});
  vm.runInContext(source, context);
  const app = context.createTeamWorkspace({document: doc, window: win, fetch: fetcher, uuid: () => 'test-id-' + ++id,
    ...(injectLayout ? {createLayout: layoutFactory('injected')} : {}),
    timers: {setTimeout(fn, delay) { const key = ++timer; timers.set(key, {fn, delay}); return key; }, clearTimeout(key) { timers.delete(key); }}});
  return {app, node, doc, win, requests, polls, recordings, redirects, media, avatar, camera, voice, layout, stored, timers, get voiceOptions() { return voiceOptions; }, get callOptions() { return options; }, response,
    click: name => node(name).emit('click'), submit(name, submitter) { node(name).emit('submit', {submitter: submitter ? node(submitter) : undefined}); },
    input(name, value) { node(name).value = value; node(name).emit('input'); },
    run(delay) { const task = [...timers.entries()].find(([, value]) => value.delay === delay); assert.ok(task, 'missing timer ' + delay); timers.delete(task[0]); task[1].fn(); }};
}

test('Workspace owns one layout with its existing document and window across boot and room changes', async () => {
  for (const injectLayout of [false, true]) {
    const h = harness({injectLayout});
    assert.equal(h.layout.created.length, 1);
    assert.equal(h.layout.created[0].origin, injectLayout ? 'injected' : 'imported');
    assert.equal(h.layout.created[0].config.document, h.doc);
    assert.equal(h.layout.created[0].config.window, h.win);
    assert.deepEqual(Object.keys(h.layout.created[0].config).sort(), ['document', 'window']);
    await h.app.start(); await h.app.selectRoom('room-b'); await h.app.start();
    assert.equal(h.layout.created.length, 1, 'room restoration or boot retry created another layout');
    assert.equal(h.layout.destroyed, 0, 'room changes must retain the shared layout');
    h.app.destroy(); h.app.destroy(); h.win.emit('pagehide');
    assert.equal(h.layout.destroyed, 1);
    assert.deepEqual(h.layout.mediaAtDestroy, {call: 1, avatar: 1, voice: 1, camera: 1},
      'the layout must keep original nodes available until media owners dispose');
  }
});

test('the HTML has one Workspace entrypoint and importing its layout has no independent DOM boot', () => {
  const scripts = [...html.matchAll(/<script\b([^>]*)>/gi)].map(([, attributes]) => ({
    type: attributes.match(/\btype=["']([^"']+)["']/i)?.[1],
    src: attributes.match(/\bsrc=["']([^"']+)["']/i)?.[1],
  }));
  assert.deepEqual(scripts, [{type: 'module', src: '/assets/team.js'}]);
  let domReads = 0;
  const untouched = new Proxy({}, {get() { domReads++; throw Error('A layout import accessed the DOM before Workspace requested it'); }});
  const layoutSource = fs.readFileSync(new URL('../services/ax/team-callspace.js', import.meta.url), 'utf8')
    .replace(/^export\s+(?=function\b)/gm, '');
  vm.runInNewContext(layoutSource, {document: untouched, window: untouched});
  assert.equal(domReads, 0);
});

test('page exit and cross-tab sign-out dispose the owned layout once with the other controllers', async () => {
  for (const [event, value] of [['pagehide', {}], ['storage', {key: 'reign:ax:signed-out', newValue: 'another-tab'}]]) {
    const h = harness(); await h.app.start();
    h.win.emit(event, value); h.win.emit(event, value); h.app.destroy();
    assert.equal(h.layout.destroyed, 1, event);
    assert.deepEqual(h.layout.mediaAtDestroy, {call: 1, avatar: 1, voice: 1, camera: 1});
  }
});

test('access loss retains the layout for account recovery until Workspace is actually disposed', async () => {
  const h = harness({handle: (request, {response}) => request.url === '/api/team'
    ? response({detail: 'Access changed'}, 403) : undefined});
  await h.app.start();
  assert.equal(h.node('workspace').hidden, true);
  assert.equal(h.layout.created.length, 1); assert.equal(h.layout.destroyed, 0);
  await h.app.logout(); h.app.destroy();
  assert.equal(h.layout.destroyed, 1);
  assert.equal(h.redirects.at(-1), '/?next=%2Fteam');
});

test('restoring a room loads only team data and leaves all media off', async () => {
  const h = harness({role: 'employee'}); await h.app.start();
  assert.equal(h.node('roomTitle').textContent, 'Planning'); assert.equal(h.media.joins, 0);
  assert.equal(h.node('privateWorkspace').hidden, true); assert.equal(h.node('manageMembersButton').hidden, true);
  const heartbeat = h.requests.find(item => item.method === 'POST' && item.url.endsWith('/presence'));
  assert.deepEqual(heartbeat.body, {connection_id: 'test-id-1', microphone: false, camera: false, screen: false, in_call: false});
  assert.equal(heartbeat.init.headers['X-CSRF-Token'], 'synthetic-token');
  assert.ok(h.requests.every(item => item.url.startsWith('/api/team'))); h.app.destroy();
});

test('the profile and copied employee login agree on the Workspace name', async () => {
  let copied = '';
  const h = harness({handle: (request, {response}) => request.url === '/api/team/members'
    ? response({member: {id: 'new-employee', username: 'ci-tester', display_name: 'Synthetic Employee', role: 'employee', active: true}, temporary_password: 'synthetic-test-only'}) : undefined});
  h.win.navigator.clipboard.writeText = async value => { copied = value; };
  await h.app.start();
  assert.equal(h.node('workspaceName').textContent, 'Workspace');
  h.input('memberUsername', 'ci-tester'); h.input('memberDisplayName', 'Synthetic Employee'); h.submit('memberCreateForm');
  await until(() => h.node('credentialDialog').open); h.click('copyCredential');
  await until(() => Boolean(copied));
  assert.equal(copied.split('\n')[0], h.node('workspaceName').textContent + ' login');
  assert.ok(copied.includes('Sign in: https://ax.test/\nUsername: ci-tester\nTemporary password: synthetic-test-only'));
  h.app.destroy();
});

test('a late room response cannot restore the room the person already left', async () => {
  const held = pending();
  const h = harness({handle: request => request.url === '/api/team/rooms/room-a' ? held.promise : undefined});
  const boot = h.app.start(); await until(() => h.requests.some(item => item.url.endsWith('/room-a')));
  await h.app.selectRoom('room-b'); held.resolve(h.response(snapshot('room-a'))); await boot;
  assert.equal(h.node('roomTitle').textContent, 'Research'); assert.equal(h.node('roomView').hidden, false); h.app.destroy();
});

test('a stale document save preserves exact local edits and original version for review', async () => {
  const h = harness({handle: (request, {response}) => request.method === 'PUT' ? response({detail: 'Version conflict'}, 409) : undefined});
  await h.app.start(); h.node('documentList').children[0].click(); await until(() => h.node('documentContent').value === '# Original');
  h.input('documentContent', '# My unsaved decisions\nKeep these.'); h.submit('documentForm');
  await until(() => !h.node('documentConflict').hidden);
  assert.equal(h.node('documentContent').value, '# My unsaved decisions\nKeep these.'); assert.equal(h.node('documentVersion').textContent, 'Saved version 1');
  const sent = h.requests.find(item => item.method === 'PUT'); assert.equal(sent.body.expected_version, 1); assert.equal(sent.body.content, '# My unsaved decisions\nKeep these.');
  assert.equal(h.node('saveDocument').disabled, true); h.app.destroy();
});

test('an uncertain message retry keeps its id and ordered event replay does not duplicate it', async () => {
  let sent = 0;
  const h = harness({handle: (request, {response}) => {
    if (request.url.endsWith('/messages')) { if (++sent === 1) throw Error('connection interrupted'); return response({event: event(4, request.body.text, {data: {...request.body}})}); }
  }});
  await h.app.start(); h.input('messageInput', 'A single durable message'); h.submit('chatForm');
  await until(() => !h.node('retryMessage').hidden); assert.equal(h.node('messageInput').value, 'A single durable message');
  h.click('retryMessage'); await until(() => h.node('messageInput').value === '');
  const messages = h.requests.filter(item => item.url.endsWith('/messages')); assert.equal(messages.length, 2); assert.equal(messages[0].body.client_message_id, messages[1].body.client_message_id);
  await until(() => h.polls.length === 1); h.polls[0].resolve(h.response({events: [event(4, 'A single durable message'), event(3, 'Earlier message'), event(4, 'Duplicate')], presence: [], assistant: {status: 'idle'}}));
  await until(() => h.node('eventFeed').children.filter(item => item.dataset.sequence).length === 2);
  assert.deepEqual(h.node('eventFeed').children.filter(item => item.dataset.sequence).map(item => item.dataset.sequence), ['3', '4']); h.app.destroy();
});

test('archives do not autoplay REIGN, while a new live assistant event is delivered once', async () => {
  const archived = event(1, 'Archived reply', {type: 'assistant_message'});
  const h = harness({handle: (request, {response}) => request.url.endsWith('/room-a') ? response({...snapshot('room-a'), events: [archived]}) : undefined});
  await h.app.start(); assert.equal(h.avatar.played.length, 0); await until(() => h.polls.length === 1);
  const live = event(2, 'New reply', {type: 'assistant_message'}); h.polls[0].resolve(h.response({events: [archived, live, live], presence: [], assistant: {status: 'completed'}}));
  await until(() => h.avatar.played.length === 1); assert.equal(h.avatar.played[0].value.id, 'event-2'); h.app.destroy();
});

test('sign out reaches the server even if local media cleanup throws and a failed attempt stays retryable', async () => {
  let failures = 1;
  const h = harness({handle: request => { if (request.url === '/logout' && failures-- > 0) throw Error('network down'); }}); await h.app.start();
  h.callOptions.onState({active: true, phase: 'active'}); await h.app.logout();
  assert.equal(h.redirects.length, 0); assert.equal(h.node('logoutButton').textContent, 'Retry log out'); assert.equal(h.node('sendMessage').disabled, true); assert.ok(h.avatar.stopped > 0);
  assert.equal(h.layout.destroyed, 0, 'an unconfirmed logout must keep the recovery controls owned');
  await h.app.logout(); assert.equal(h.redirects.at(-1), '/?next=%2Fteam'); assert.equal(h.stored[0].key, 'reign:ax:signed-out'); assert.equal(h.media.disposed, 1);
  h.win.emit('pagehide'); h.app.destroy(); assert.equal(h.layout.destroyed, 1);
  const broken = harness({leaveThrows: true}); await broken.app.logout(); assert.equal(broken.requests.filter(item => item.url === '/logout').length, 1); assert.equal(broken.redirects.length, 1);
});

test('a generated password arriving after logout cannot reopen its credential dialog', async () => {
  const held = pending(); const h = harness({handle: request => request.url === '/api/team/members' ? held.promise : undefined}); await h.app.start();
  h.input('memberUsername', 'ci-tester'); h.input('memberDisplayName', 'Synthetic Employee'); h.submit('memberCreateForm');
  await until(() => h.requests.some(item => item.url === '/api/team/members')); await h.app.logout();
  held.resolve(h.response({member: {id: 'new-employee', username: 'ci-tester', display_name: 'Synthetic Employee'}, temporary_password: 'synthetic-test-only'}));
  await new Promise(resolve => setImmediate(resolve)); assert.equal(h.node('credentialDialog').open, false); assert.equal(h.node('credentialPassword').value, '');
});

test('disabled remote camera and screen tracks never leave stale visible tiles', async () => {
  const h = harness(); await h.app.start(); const stream = {};
  h.callOptions.onStreams({local: {}, remote: [{connectionId: 'peer', displayName: 'Teammate', camera: stream, cameraEnabled: true, screen: stream, screenEnabled: true}]});
  assert.equal(h.node('callParticipants').children.length, 2);
  h.callOptions.onStreams({local: {}, remote: [{connectionId: 'peer', displayName: 'Teammate', camera: stream, cameraEnabled: false, screen: stream, screenEnabled: false}]});
  assert.equal(h.node('callParticipants').children.length, 0); h.app.destroy();
});

test('Microphone on starts one persistent live session, keeps the draft, and never records a question', async () => {
  const h = harness(); await h.app.start(); h.input('messageInput', 'Keep my draft while I talk.');
  const stopped = h.avatar.stopped; h.click('talkToReign');
  assert.equal(h.voice.starts.length, 1); assert.equal(h.voice.starts[0].roomId, 'room-a'); assert.equal(h.voice.starts[0].connectionId, 'test-id-1');
  assert.equal(h.recordings.length, 0); assert.ok(h.avatar.stopped > stopped);
  assert.equal(h.node('messageInput').value, 'Keep my draft while I talk.'); assert.equal(h.node('messageInput').disabled, false);
  assert.equal(h.node('talkToReign').hidden, true); assert.equal(h.node('stopReignMic').hidden, false);
  assert.equal(h.node('dictateMessage').disabled, true); h.click('dictateMessage'); assert.equal(h.recordings.length, 0);
  h.click('muteReignMic'); assert.equal(h.voice.muted, true); assert.equal(h.node('muteReignMic').textContent, 'Unmute mic');
  h.click('muteReignMic'); assert.equal(h.voice.muted, false); assert.equal(h.voice.starts.length, 1);
  h.click('stopReignMic'); assert.equal(h.voice.active, false); assert.equal(h.node('talkToReign').hidden, false);
  assert.equal(h.node('messageInput').value, 'Keep my draft while I talk.');
  assert.ok(h.requests.every(request => !request.url.endsWith('/transcriptions'))); h.app.destroy();
});

test('cancelling Dictate during transcription aborts it and cannot overwrite a newer draft', async () => {
  const held = pending(), h = harness({handle: request => request.url.endsWith('/transcriptions') ? held.promise : undefined}); await h.app.start();
  h.input('messageInput', 'Keep my typed draft'); h.click('dictateMessage'); h.click('stopDictation');
  await until(() => h.requests.some(item => item.url.endsWith('/transcriptions')));
  h.click('cancelDictation'); assert.equal(h.requests.find(item => item.url.endsWith('/transcriptions')).init.signal.aborted, true);
  h.input('messageInput', 'A new typed draft'); held.resolve(h.response({text: 'This cancelled dictation must not send'}));
  await new Promise(resolve => setImmediate(resolve)); assert.equal(h.requests.filter(item => item.url.endsWith('/messages')).length, 0);
  assert.equal(h.node('messageInput').value, 'A new typed draft'); h.app.destroy();
});

test('Dictate adds a reviewed transcript without sending a room or assistant message', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/transcriptions') ? response({text: 'A reviewed idea'}) : undefined}); await h.app.start();
  h.input('messageInput', 'Existing notes'); h.click('dictateMessage'); assert.equal(h.recordings[0].options.autoStop, false); h.click('stopDictation'); await until(() => h.node('messageInput').value.includes('A reviewed idea'));
  assert.equal(h.node('messageInput').value, 'Existing notes\nA reviewed idea'); assert.equal(h.requests.filter(item => item.url.endsWith('/messages')).length, 0); h.app.destroy();
});

test('native live replies are archived once without duplicate cached audio, including late replies after off', async () => {
  const h = harness(); await h.app.start(); h.click('talkToReign'); await until(() => h.polls.length === 1);
  const own = event(2, 'Live answer', {type: 'assistant_message', data: {text: 'Live answer', voice_session_id: 'voice-session-1'}});
  const other = event(3, 'Another teammate’s answer', {type: 'assistant_message', data: {text: 'Another teammate’s answer', voice_session_id: 'other-session'}});
  h.click('stopReignMic');
  h.polls[0].resolve(h.response({events: [own, other], presence: [], assistant: {status: 'completed'}}));
  await until(() => h.avatar.played.length === 1);
  assert.equal(h.avatar.played[0].value.id, other.id);
  assert.equal(h.node('eventFeed').children.filter(node => node.dataset.eventId).length, 2);
  assert.ok(h.voice.accepted.some(item => item.events[0]?.id === own.id)); h.app.destroy();
});

test('shared clip failure copy does not label the initiating live narration unavailable', async () => {
  const h = harness(); await h.app.start(); h.click('talkToReign'); await until(() => h.polls.length === 1);
  const own = event(2, 'Live answer', {type: 'assistant_message', data: {
    text: 'Live answer', voice_session_id: 'voice-session-1', speech_status: 'unavailable'}});
  h.polls[0].resolve(h.response({events: [own], presence: [], assistant: {status: 'completed'}}));
  await until(() => h.node('eventFeed').children.some(node => node.dataset.eventId === own.id));
  const row = h.node('eventFeed').children.find(node => node.dataset.eventId === own.id);
  assert.match(row.children[1].children.at(-1).textContent, /Shared audio playback is unavailable/);
  assert.equal(h.voice.active, true); assert.equal(h.avatar.played.length, 0); h.app.destroy();
});

test('room changes and logout stop live microphone ownership without closing another human call track', async () => {
  const held = pending(), h = harness({handle: request => request.url === '/logout' ? held.promise : undefined}); await h.app.start();
  h.click('talkToReign'); await h.app.selectRoom('room-b');
  assert.equal(h.voice.active, false); assert.equal(h.node('talkToReign').hidden, false);
  assert.equal(h.voice.starts.length, 1); h.click('talkToReign');
  const logout = h.app.logout(); await until(() => h.requests.some(request => request.url === '/logout'));
  assert.equal(h.voice.active, false, 'mic cleanup begins before logout acknowledgement');
  assert.equal(h.voice.starts.length, 2); held.resolve(h.response({ok: true})); await logout;
  assert.equal(h.voice.disposed, 1);
});

test('a departed call connection stops that operation without treating room membership as revoked', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/signals') ? response({code: 'team_connection_required', detail: 'The peer left.'}, 403) : undefined}); await h.app.start();
  await assert.rejects(h.callOptions.api('/api/team/rooms/room-a/signals', 'POST', {}), error => error.status === 403);
  assert.equal(h.node('workspace').hidden, false); assert.equal(h.node('sendMessage').disabled, false); assert.equal(h.redirects.length, 0); h.app.destroy();
});

test('local mute and leave clear presence flags immediately and stale rows cannot resurrect them', async () => {
  const h = harness(); await h.app.start();
  const viewer = {connection_id: 'viewer-local', user_id: 'user-1', display_name: 'CI Teammate', in_call: false};
  const local = {...viewer, connection_id: 'call-local', in_call: true, microphone: true, camera: true, screen: true};
  const remote = {connection_id: 'call-remote', user_id: 'user-2', display_name: 'Other teammate', in_call: true, microphone: true, camera: true};
  h.callOptions.onState({active: true, connectionId: 'call-local', microphone: true, camera: true, screen: true});
  h.callOptions.onPresence([viewer, local, remote]);
  const localText = () => h.node('presenceList').children.find(node => node.textContent.startsWith('CI Teammate')).textContent;
  assert.match(localText(), /mic on, camera, screen/);
  h.callOptions.onState({microphone: false, camera: false, screen: false}); assert.equal(localText(), 'CI Teammate');
  h.callOptions.onState({active: false, connectionId: null}); h.callOptions.onPresence([viewer, local, remote]);
  assert.equal(localText(), 'CI Teammate');
  assert.match(h.node('presenceList').children.find(node => node.textContent.startsWith('Other teammate')).textContent, /mic on, camera/);
  h.app.destroy();
});

test('the confirmed leave response replaces remote flags with the current room presence', async () => {
  const viewer = {connection_id: 'viewer-local', user_id: 'user-1', display_name: 'CI Teammate', in_call: false};
  const remoteViewer = {connection_id: 'viewer-remote', user_id: 'user-2', display_name: 'Other teammate', in_call: false};
  const h = harness({handle: (request, {response}) => request.method === 'DELETE' && request.url.endsWith('/call-local') ? response({presence: [viewer, remoteViewer]}) : undefined}); await h.app.start();
  h.callOptions.onPresence([viewer, {...remoteViewer, connection_id: 'call-remote', in_call: true, microphone: true, camera: true}]);
  await h.callOptions.api('/api/team/rooms/room-a/presence/call-local', 'DELETE');
  assert.deepEqual(h.node('presenceList').children.map(node => node.textContent), ['CI Teammate', 'Other teammate']); h.app.destroy();
});

test('a remote leave between room polls receives a bounded idle presence refresh without a chat event', async () => {
  const h = harness(); await h.app.start(); await until(() => h.polls.length === 1);
  const viewer = {connection_id: 'viewer-remote', user_id: 'user-2', display_name: 'Other teammate', in_call: false};
  const call = {...viewer, connection_id: 'call-remote', in_call: true, microphone: true, camera: true};
  h.polls[0].resolve(h.response({events: [], presence: [viewer, call], assistant: {status: 'idle'}}));
  await until(() => [...h.timers.values()].some(timer => timer.delay === 120));
  assert.match(h.node('presenceList').children[0].textContent, /mic on, camera/);
  // The remote connection disappears during this gap. The next server wait
  // sees only its new baseline, so no presence-change wakeup is guaranteed.
  h.run(120); await until(() => h.polls.length === 2);
  const wait = Number(new URL(h.polls[1].url, 'https://ax.test').searchParams.get('wait'));
  assert.ok(wait > 0 && wait <= 5, 'idle room polling must refresh presence within five seconds');
  h.polls[1].resolve(h.response({events: [], presence: [viewer], assistant: {status: 'idle'}}));
  await until(() => h.node('presenceList').children[0].textContent === 'Other teammate');
  assert.equal(h.node('eventFeed').children.filter(node => node.dataset.sequence).length, 0);
  h.app.destroy();
});

test('typed Ask requests voice by default and unlocks output synchronously within its submit gesture', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/messages')
    ? response({event: event(1, request.body.text, {data: request.body}), run_id: 'run-1'}) : undefined});
  await h.app.start();
  assert.equal(h.node('speakReply').checked, true); assert.equal(h.avatar.unlocked, 0);
  h.input('messageInput', 'Please speak this reply.'); h.submit('chatForm', 'askReign');
  assert.equal(h.avatar.unlocked, 1, 'unlock begins before the submit gesture returns');
  await until(() => h.requests.some(request => request.url.endsWith('/messages')));
  const request = h.requests.find(request => request.url.endsWith('/messages'));
  assert.equal(request.body.ask_reign, true); assert.equal(request.body.speak, true);
  assert.equal(request.body.input_source, undefined); assert.equal(request.body.transcription_id, undefined);
  h.app.destroy();
});

test('an explicit voice opt-out survives room switches and avoids output unlock for text-only Ask', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/messages')
    ? response({event: event(1, request.body.text, {data: request.body})}) : undefined});
  await h.app.start();
  h.node('speakReply').checked = false; h.node('speakReply').emit('change');
  await h.app.selectRoom('room-b'); assert.equal(h.node('speakReply').checked, true);
  await h.app.selectRoom('room-a'); assert.equal(h.node('speakReply').checked, false);
  h.input('messageInput', 'Keep this reply text-only.'); h.submit('chatForm', 'askReign');
  await until(() => h.requests.some(request => request.url.endsWith('/messages')));
  assert.equal(h.requests.find(request => request.url.endsWith('/messages')).body.speak, false);
  assert.equal(h.avatar.unlocked, 0);
  h.node('speakReply').checked = true; h.node('speakReply').emit('change');
  assert.equal(h.avatar.unlocked, 1, 'checking voice is also an explicit audio gesture');
  h.app.destroy();
});

test('Camera is a local preview until Describe sends one still and a spoken question while preserving the draft', async () => {
  const h = harness({handle: (request, {response}) => {
    if (request.url.endsWith('/attachments')) return response({attachment: {id: 'camera-receipt', source: 'camera_preview'}});
    if (request.url.endsWith('/messages')) return response({event: event(1, request.body.text, {data: request.body}), run_id: 'camera-run'});
  }});
  await h.app.start(); h.input('messageInput', 'Keep my unfinished typed question.');
  h.click('openCamera');
  assert.equal(h.camera.started, 1); assert.equal(h.node('cameraDialog').open, true);
  assert.ok(h.node('cameraPreview').srcObject); assert.equal(h.media.joins, 0);
  assert.equal(h.camera.captures, 0); assert.equal(h.requests.some(request => request.url.endsWith('/attachments')), false);
  h.click('describeCamera');
  assert.equal(h.avatar.unlocked, 1, 'Describe starts audio activation in the same gesture');
  await until(() => !h.node('cameraDialog').open);
  const attachment = h.requests.find(request => request.url.endsWith('/attachments'));
  const question = h.requests.find(request => request.url.endsWith('/messages'));
  assert.equal(attachment.body.source, 'camera_preview');
  assert.equal(attachment.body.connection_id, 'test-id-1');
  assert.match(attachment.body.captured_at, /^\d{4}-\d\d-\d\dT/);
  assert.match(attachment.body.data_url, /^data:image\/jpeg;base64,/);
  const lease = h.requests.filter(request => request.url.endsWith('/presence')).at(-1);
  assert.equal(lease.body.in_call, false); assert.equal(lease.body.camera, false); assert.equal(lease.body.microphone, false);
  assert.equal(question.body.ask_reign, true); assert.equal(question.body.speak, true);
  assert.deepEqual(question.body.attachment_ids, ['camera-receipt']);
  assert.equal(h.node('messageInput').value, 'Keep my unfinished typed question.');
  assert.equal(h.node('cameraPreview').srcObject, null);
  assert.equal(h.requests.filter(request => request.url.endsWith('/messages')).length, 1);
  h.app.destroy();
});

test('stopping or closing a camera preview never uploads a frame and a late capture cannot submit', async () => {
  const held = pending(), h = harness({cameraCapture: held}); await h.app.start();
  h.click('openCamera'); h.click('stopCamera');
  assert.equal(h.node('cameraDialog').open, true); assert.equal(h.node('describeCamera').disabled, true);
  assert.equal(h.node('cameraPreview').srcObject, null);
  h.click('restartCamera'); h.click('describeCamera'); h.click('closeCamera');
  held.resolve(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.node('cameraDialog').open, false); assert.equal(h.node('cameraPreview').srcObject, null);
  assert.equal(h.requests.some(request => /\/(attachments|messages)$/.test(request.url)), false);
  h.app.destroy();
});

test('a late camera upload cannot ask in the room the person left', async () => {
  const held = pending(), h = harness({handle: request => request.url.endsWith('/attachments') ? held.promise : undefined});
  await h.app.start(); h.click('openCamera'); h.click('describeCamera');
  await until(() => h.requests.some(request => request.url.endsWith('/attachments')));
  await h.app.selectRoom('room-b');
  held.resolve(h.response({attachment: {id: 'late-receipt', source: 'camera_preview'}}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.node('cameraDialog').open, false); assert.equal(h.node('cameraPreview').srcObject, null);
  assert.equal(h.requests.some(request => request.url.endsWith('/messages')), false);
  h.app.destroy();
});

test('an uncertain camera question retries the same message and receipt without recapturing or replacing the draft', async () => {
  let sends = 0;
  const h = harness({handle: (request, {response}) => {
    if (request.url.endsWith('/attachments')) return response({attachment: {id: 'one-camera-receipt', source: 'camera_preview'}});
    if (request.url.endsWith('/messages')) {
      if (++sends === 1) return response({detail: 'Assistant unavailable.'}, 503);
      return response({event: event(1, request.body.text, {data: request.body})});
    }
  }});
  await h.app.start(); h.input('messageInput', 'My draft stays here.'); h.click('openCamera'); h.click('describeCamera');
  await until(() => h.node('cameraStatus').dataset.error === 'true');
  assert.equal(h.node('cameraDialog').open, true); assert.equal(h.node('describeCamera').disabled, true);
  h.click('closeCamera'); h.click('retryMessage');
  await until(() => sends === 2);
  const messages = h.requests.filter(request => request.url.endsWith('/messages'));
  assert.deepEqual(messages[0].body, messages[1].body);
  assert.equal(h.camera.captures, 1); assert.equal(h.requests.filter(request => request.url.endsWith('/attachments')).length, 1);
  assert.equal(h.node('messageInput').value, 'My draft stays here.');
  h.app.destroy();
});

test('a local camera preview remains usable when the model is unavailable and logout stops it before the server responds', async () => {
  const logout = pending();
  const h = harness({handle: (request, {response, profile}) => {
    if (request.url === '/api/team') return response({...profile, capabilities: {...profile.capabilities, assistant: false, vision: false}});
    if (request.url === '/logout') return logout.promise;
  }});
  await h.app.start(); h.click('openCamera');
  assert.ok(h.node('cameraPreview').srcObject); assert.equal(h.node('describeCamera').disabled, true);
  assert.match(h.node('cameraAvailability').textContent, /vision connection is unavailable/);
  const ending = h.app.logout();
  assert.equal(h.node('cameraPreview').srcObject, null); assert.equal(h.node('cameraDialog').open, false);
  logout.resolve(h.response({ok: true})); await ending;
  assert.equal(h.camera.disposed, 1);
});

test('existing group camera stills send an ISO capture timestamp through the same protected attachment route', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/attachments')
    ? response({attachment: {id: 'shared-call-receipt', source: 'camera'}}) : undefined});
  await h.app.start();
  h.callOptions.onState({active: true, phase: 'active', camera: true, connectionId: 'call-owned'});
  h.callOptions.onStreams({local: {camera: {}}, remote: []});
  h.click('shareWithReign'); h.node('shareSource').value = 'camera'; h.click('captureShare');
  await until(() => h.node('confirmShare').disabled === false); h.click('confirmShare');
  await until(() => h.requests.some(request => request.url.endsWith('/attachments')));
  const request = h.requests.find(request => request.url.endsWith('/attachments'));
  assert.equal(request.body.source, 'camera'); assert.equal(request.body.connection_id, 'call-owned');
  assert.equal(typeof request.body.captured_at, 'string'); assert.ok(Number.isFinite(Date.parse(request.body.captured_at)));
  assert.equal(h.camera.started, 0, 'shared-call capture does not start a second camera');
  h.app.destroy();
});
