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
const profile = (role = 'owner') => ({workspace: {id: 'rain', name: 'Rain', durable: true}, me: {id: 'user-1', role, username: 'tester', display_name: 'CI Teammate'}, members: [],
  rooms: [{id: 'room-a', title: 'Planning'}, {id: 'room-b', title: 'Research'}], capabilities: {assistant: true, speech: true, calls: true, documents: true, manage_members: role === 'owner', vision: true, transcription: true}});
const event = (seq, text, extra = {}) => ({id: 'event-' + seq, seq, type: 'message', user_id: 'user-1', display_name: 'CI Teammate', created_at: 1700000000, data: {text}, ...extra});
const snapshot = id => ({room: {id, title: id === 'room-a' ? 'Planning' : 'Research'}, events: [], documents: [{id: 'document-1', title: 'Shared plan', version: 1}], presence: [], assistant: {status: 'idle'}, last_seq: 0});
function harness({handle, role, leaveThrows = false} = {}) {
  const nodes = new Map(), requests = [], timers = new Map(), polls = [], recordings = [], redirects = [], stored = [], media = {joins: 0, leaves: 0, disposed: 0}, avatar = {played: [], stopped: 0, destroyed: 0};
  let timer = 0, id = 0, options;
  for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element());
  const node = name => { assert.ok(nodes.has(name), 'unknown DOM ID ' + name); return nodes.get(name); };
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
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}}, FileReader: class {},
    createTeamCall: config => { options = config; return {join() { media.joins++; return Promise.resolve(); }, leave() { media.leaves++; if (leaveThrows) throw Error('cleanup failed'); return Promise.resolve(); }, syncPresence() {}, dispose() { media.disposed++; }, setMicrophone() {}, setCamera() {}, setScreen() {}}; },
    createTeamAvatar: config => { config.onStatus({state: 'ready', message: 'Ready'}); return {play(room, value) { avatar.played.push({room, value}); }, unlock: async () => true, stop() { avatar.stopped++; }, destroy() { avatar.destroyed++; }}; },
    createTeamRecorder: config => ({start() { const value = pending(); recordings.push(value); config.onState({state: 'recording', message: 'Recording'}); return value.promise; },
      cancel() { recordings.at(-1)?.reject(Object.assign(Error('cancelled'), {name: 'AbortError'})); config.onState({state: 'idle'}); },
      stop() { const value = recordings.at(-1); value?.resolve({audio_base64: 'c3ludGhldGlj', mime_type: 'audio/webm'}); config.onState({state: 'idle'}); return value?.promise || Promise.resolve(); }, dispose() {}}),
    createTeamBrowser: () => ({setRoom() {}, dispose() {}})});
  vm.runInContext(source, context);
  const app = context.createTeamWorkspace({document: doc, window: win, fetch: fetcher, uuid: () => 'test-id-' + ++id,
    timers: {setTimeout(fn, delay) { const key = ++timer; timers.set(key, {fn, delay}); return key; }, clearTimeout(key) { timers.delete(key); }}});
  return {app, node, doc, win, requests, polls, recordings, redirects, media, avatar, stored, timers, get callOptions() { return options; }, response,
    click: name => node(name).emit('click'), submit(name, submitter) { node(name).emit('submit', {submitter: submitter ? node(submitter) : undefined}); },
    input(name, value) { node(name).value = value; node(name).emit('input'); },
    run(delay) { const task = [...timers.entries()].find(([, value]) => value.delay === delay); assert.ok(task, 'missing timer ' + delay); timers.delete(task[0]); task[1].fn(); }};
}

test('restoring a room loads only team data and leaves all media off', async () => {
  const h = harness({role: 'employee'}); await h.app.start();
  assert.equal(h.node('roomTitle').textContent, 'Planning'); assert.equal(h.media.joins, 0);
  assert.equal(h.node('privateWorkspace').hidden, true); assert.equal(h.node('manageMembersButton').hidden, true);
  const heartbeat = h.requests.find(item => item.method === 'POST' && item.url.endsWith('/presence'));
  assert.deepEqual(heartbeat.body, {connection_id: 'test-id-1', microphone: false, camera: false, screen: false, in_call: false});
  assert.equal(heartbeat.init.headers['X-CSRF-Token'], 'synthetic-token');
  assert.ok(h.requests.every(item => item.url.startsWith('/api/team'))); h.app.destroy();
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
  await h.app.logout(); assert.equal(h.redirects.at(-1), '/?next=%2Fteam'); assert.equal(h.stored[0].key, 'reign:ax:signed-out'); assert.equal(h.media.disposed, 1);
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

test('Talk to REIGN stops playback and explicitly sends one transcribed question with a spoken reply', async () => {
  const h = harness({handle: (request, {response}) => {
    if (request.url.endsWith('/transcriptions')) return response({text: 'What should our team do next?'});
    if (request.url.endsWith('/messages')) return response({event: event(1, request.body.text, {data: {...request.body}}), run_id: 'run-1'});
  }}); await h.app.start(); const stopped = h.avatar.stopped;
  h.click('talkToReign'); assert.ok(h.avatar.stopped > stopped); assert.equal(h.recordings.length, 1); assert.equal(h.node('messageInput').disabled, true);
  assert.equal(h.requests.filter(item => item.url.endsWith('/messages')).length, 0);
  h.click('stopDictation'); await until(() => h.requests.some(item => item.url.endsWith('/messages')));
  const request = h.requests.find(item => item.url.endsWith('/messages'));
  assert.equal(request.body.text, 'What should our team do next?'); assert.equal(request.body.ask_reign, true); assert.equal(request.body.speak, true);
  await until(() => h.node('messageInput').value === ''); h.app.destroy();
});

test('cancelling Talk during transcription cannot submit later or replace an existing typed draft', async () => {
  const held = pending(), h = harness({handle: request => request.url.endsWith('/transcriptions') ? held.promise : undefined}); await h.app.start();
  h.input('messageInput', 'Keep my typed draft'); h.click('talkToReign'); assert.equal(h.recordings.length, 0); assert.equal(h.node('messageInput').value, 'Keep my typed draft');
  h.input('messageInput', ''); h.click('talkToReign'); h.click('stopDictation'); await until(() => h.requests.some(item => item.url.endsWith('/transcriptions')));
  h.click('cancelDictation'); h.input('messageInput', 'A new typed draft'); held.resolve(h.response({text: 'This cancelled voice turn must not send'}));
  await new Promise(resolve => setImmediate(resolve)); assert.equal(h.requests.filter(item => item.url.endsWith('/messages')).length, 0); assert.equal(h.node('messageInput').value, 'A new typed draft'); h.app.destroy();
});

test('Dictate adds a reviewed transcript without sending a room or assistant message', async () => {
  const h = harness({handle: (request, {response}) => request.url.endsWith('/transcriptions') ? response({text: 'A reviewed idea'}) : undefined}); await h.app.start();
  h.input('messageInput', 'Existing notes'); h.click('dictateMessage'); h.click('stopDictation'); await until(() => h.node('messageInput').value.includes('A reviewed idea'));
  assert.equal(h.node('messageInput').value, 'Existing notes\nA reviewed idea'); assert.equal(h.requests.filter(item => item.url.endsWith('/messages')).length, 0); h.app.destroy();
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
