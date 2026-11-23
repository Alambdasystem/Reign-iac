import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Exercise the production controller against controlled browser and server
// boundaries. These are lifecycle/signaling regressions, not a claim that real
// microphones, speakers, NAT traversal or a deployed multi-user call passed.
const source = await readFile(new URL('../services/ax/team-call.js', import.meta.url), 'utf8');
const {createTeamCall} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const id = number => `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`;
const defer = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const flush = async () => { for (let i = 0; i < 3; i++) await new Promise(resolve => setImmediate(resolve)); };

class Clock {
  now = 0; next = 0; tasks = new Map();
  setTimeout(fn, delay) { const key = ++this.next; this.tasks.set(key, {fn, due: this.now + delay}); return key; }
  clearTimeout(key) { this.tasks.delete(key); }
  async advance(duration) {
    const end = this.now + duration;
    for (let count = 0; count < 2000; count++) {
      const next = [...this.tasks].filter(([, job]) => job.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) { this.now = end; await flush(); return; }
      this.now = next[1].due; this.tasks.delete(next[0]); next[1].fn(); await flush();
    }
    throw new Error('Timer work exceeded the test bound');
  }
}
class Track {
  constructor(kind, label = kind) { Object.assign(this, {kind, label, enabled: true, readyState: 'live', stopped: 0}); }
  stop() { this.stopped++; this.readyState = 'ended'; }
  end() { this.readyState = 'ended'; this.onended?.(); }
}
class Stream {
  constructor(tracks = []) { this.tracks = [...tracks]; }
  getTracks() { return [...this.tracks]; }
  getAudioTracks() { return this.tracks.filter(track => track.kind === 'audio'); }
  getVideoTracks() { return this.tracks.filter(track => track.kind === 'video'); }
}

function environment() {
  const clock = new Clock(), people = new Map(), signals = [], requests = [], pcs = [], clients = [];
  let sequence = 0, peerId = 0;
  const error = status => Object.assign(new Error(`synthetic ${status}`), {status});
  const rows = room => [...people.values()].filter(row => row.room === room).map(({room, ...row}) => ({...row}));
  const sdp = peer => `v=0\r\na=x-test-peer:${peer.number}\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n`;
  class Peer {
    constructor(config) { Object.assign(this, {config, number: ++peerId, transceivers: [], connectionState: 'new', iceConnectionState: 'new', closed: false, acceptedIce: []}); pcs.push(this); }
    addTransceiver(kind, options) {
      const sender = {track: null, replacements: [], async replaceTrack(track) { this.track = track; this.replacements.push(track); }};
      const tx = {sender, receiver: {track: new Track(kind, `remote-${this.number}-${kind}`)}, direction: options.direction};
      this.transceivers.push(tx); return tx;
    }
    getTransceivers() { return [...this.transceivers]; }
    async createOffer() { return {type: 'offer', sdp: sdp(this)}; }
    async createAnswer() { return {type: 'answer', sdp: sdp(this)}; }
    async setLocalDescription(value) {
      this.localDescription = value;
      // ICE can arrive before the authenticated SDP POST finishes.
      this.onicecandidate?.({candidate: {candidate: `candidate:test-${this.number}`, sdpMid: '0', sdpMLineIndex: 0}});
      this.onicecandidate?.({candidate: null});
    }
    async setRemoteDescription(value) {
      this.remoteDescription = value;
      if (value.type === 'offer') {
        // Real addTransceiver-created slots are not reusable by a remote offer:
        // a premature answerer allocation would produce six slots here.
        for (const kind of ['audio', 'video', 'video']) this.addTransceiver(kind, {direction: 'recvonly'});
      }
      for (const transceiver of this.transceivers) this.ontrack?.({track: transceiver.receiver.track, transceiver, streams: []});
      if (value.type === 'answer') {
        const other = pcs.find(peer => peer.number === Number(value.sdp.match(/a=x-test-peer:(\d+)/)[1]));
        for (const peer of [this, other]) { peer.connectionState = 'connected'; peer.onconnectionstatechange?.(); }
      }
    }
    async addIceCandidate(value) { assert.ok(this.remoteDescription, 'ICE must wait for SDP'); this.acceptedIce.push(value); }
    close() { this.closed = true; this.connectionState = 'closed'; }
  }
  function client(number, options = {}) {
    const states = [], streamStates = [], devices = [], tracks = [], apiCalls = [];
    let nextId = number, controller;
    const mediaDevices = {
      getUserMedia(constraints) {
        devices.push({kind: constraints.audio ? 'microphone' : 'camera', constraints});
        if (options.getUserMedia) return options.getUserMedia(constraints);
        const track = new Track(constraints.audio ? 'audio' : 'video', `${number}-${constraints.audio ? 'mic' : 'camera'}`);
        tracks.push(track); return Promise.resolve(new Stream([track]));
      },
      getDisplayMedia(constraints) {
        devices.push({kind: 'screen', constraints});
        if (options.getDisplayMedia) return options.getDisplayMedia(constraints);
        const track = new Track('video', `${number}-screen`); tracks.push(track); return Promise.resolve(new Stream([track]));
      },
    };
    async function api(path, method = 'GET', body, requestOptions) {
      const record = {client: number, path, method, body, options: requestOptions}; requests.push(record); apiCalls.push(record);
      if (options.api) { const custom = options.api(record); if (custom !== undefined) return await custom; }
      if (options.revoked) throw error(403);
      if (path === '/api/team/call-config') return {ice_servers: [], relay_configured: false, max_participants: 4};
      const url = new URL(path, 'https://test.invalid');
      const match = url.pathname.match(/^\/api\/team\/rooms\/([^/]+)\/(presence|signals)(?:\/([^/]+))?$/);
      assert.ok(match, `unexpected endpoint ${path}`);
      const [, room, resource, target] = match;
      if (resource === 'presence' && method === 'POST') {
        const others = rows(room).filter(row => row.connection_id !== body.connection_id && row.in_call);
        if (body.in_call && others.length >= 4) throw error(409);
        people.set(body.connection_id, {...body, room, user_id: `user-${number}`, display_name: `Person ${number}`});
        return {presence: rows(room)};
      }
      if (resource === 'presence' && method === 'DELETE') { people.delete(target); return {presence: rows(room)}; }
      if (resource === 'signals' && method === 'POST') {
        const sender = people.get(body.connection_id), recipient = people.get(body.to);
        if (!sender?.in_call || !recipient?.in_call || sender.room !== room || recipient.room !== room) throw error(403);
        signals.push({seq: ++sequence, room, from: body.connection_id, to: body.to, kind: body.kind, payload: body.payload});
        return {ok: true};
      }
      const mine = signals.filter(signal => signal.room === room && signal.to === url.searchParams.get('connection_id') && signal.seq > Number(url.searchParams.get('after')));
      return {signals: mine, last_seq: mine.at(-1)?.seq || Number(url.searchParams.get('after'))};
    }
    controller = createTeamCall({api, mediaDevices, Peer, MediaStream: Stream, timers: clock, document: options.document,
      me: {id: `user-${number}`, display_name: `Person ${number}`}, makeId: () => id(nextId++),
      onState: value => states.push(value), onStreams: value => streamStates.push(value),
      onPresence: presence => { if (options.echoPresence) controller.syncPresence({roomId: controller.snapshot().roomId, presence}); }});
    const result = {call: controller, states, streamStates, devices, tracks, apiCalls, options}; clients.push(result); return result;
  }
  return {clock, client, requests, pcs, people, signals, rows,
    async close() { await Promise.all(clients.map(client => client.call.dispose())); await flush(); }};
}

async function connectedPair(env) {
  const a = env.client(1), b = env.client(100);
  await a.call.join({roomId: 'room'}); await b.call.join({roomId: 'room'});
  a.call.syncPresence({roomId: 'room', presence: env.rows('room')});
  await flush(); await env.clock.advance(1500);
  assert.equal(a.call.snapshot().phase, 'connected'); assert.equal(b.call.snapshot().phase, 'connected');
  return [a, b];
}

test('room resume and room presence alone never request devices or create peers', async () => {
  const env = environment(), client = env.client(1);
  client.call.syncPresence({roomId: 'room', presence: [{connection_id: id(2), in_call: true}]});
  assert.equal(await client.call.setCamera(true), false); assert.equal(await client.call.setScreen(true), false);
  assert.equal(client.devices.length, 0); assert.equal(env.requests.length, 0); assert.equal(env.pcs.length, 0);
  assert.equal(client.call.snapshot().active, false); await env.close();
});

test('explicit join requests only a microphone; mute stays in the call and advertises false', async () => {
  const env = environment(), client = env.client(1, {echoPresence: true});
  await client.call.join({roomId: 'room'});
  assert.deepEqual(client.devices.map(value => value.kind), ['microphone']);
  assert.equal(client.call.snapshot().phase, 'waiting');
  assert.match(client.call.snapshot().status, /No relay.*may fail/);
  client.call.setMicrophone(false); await env.clock.advance(0);
  assert.equal(client.tracks[0].enabled, false);
  assert.equal(env.rows('room')[0].in_call, true); assert.equal(env.rows('room')[0].microphone, false);
  assert.ok(client.states.length < 20, 'presence echo must not recurse'); await env.close();
});

test('leaving an unresolved microphone prompt settles join and stops a late grant', async () => {
  const env = environment(), pending = defer(); let attempts = 0;
  const client = env.client(1, {getUserMedia: () => ++attempts === 1 ? pending.promise : Promise.resolve(new Stream([new Track('audio', 'new')]))});
  const oldJoin = client.call.join({roomId: 'old'}); await flush();
  assert.equal(attempts, 1); await client.call.leave(); await oldJoin;
  await client.call.join({roomId: 'new'});
  assert.equal(client.call.snapshot().roomId, 'new'); assert.equal(attempts, 2);
  const late = new Track('audio', 'late'); pending.resolve(new Stream([late])); await flush();
  assert.equal(late.readyState, 'ended'); assert.equal(env.rows('old').length, 0);
  assert.equal(env.rows('new').length, 1); await env.close();
});

test('a queued room switch can be cancelled before the preceding DELETE completes', async () => {
  const env = environment(), pending = defer();
  const client = env.client(1, {api: record => record.method === 'DELETE' ? pending.promise : undefined});
  await client.call.join({roomId: 'old'});
  const switching = client.call.join({roomId: 'new'});
  await client.call.leave(); pending.resolve({}); await switching;
  assert.equal(client.call.snapshot().active, false); assert.equal(client.devices.length, 1);
  assert.equal(client.tracks[0].readyState, 'ended'); await env.close();
});

test('two controllers negotiate one offer and three fixed slots; ICE follows SDP', async () => {
  const env = environment(); const [a, b] = await connectedPair(env);
  assert.equal(env.pcs.length, 2); assert.ok(env.pcs.every(peer => peer.transceivers.length === 3));
  assert.equal(env.signals.filter(signal => signal.kind === 'offer').length, 1);
  assert.equal(env.signals.filter(signal => signal.kind === 'answer').length, 1);
  for (const client of [a, b]) {
    const outbound = env.signals.filter(signal => signal.from === client.call.snapshot().connectionId);
    assert.ok(['offer', 'answer'].includes(outbound[0].kind));
    assert.ok(outbound.some(signal => signal.kind === 'ice' && signal.payload === null));
    assert.ok(client.streamStates.at(-1).remote[0].audio);
  }
  await env.close(); assert.ok(env.pcs.every(peer => peer.closed));
});

test('camera and screen occupy independent senders, and screen permission starts in the gesture', async () => {
  const env = environment(); const [a, b] = await connectedPair(env);
  await a.call.setCamera(true);
  const before = a.devices.length, permission = a.call.setScreen(true);
  assert.equal(a.devices.length, before + 1, 'screen prompt is invoked before returning a promise');
  assert.equal(await permission, true);
  const peer = env.pcs.find(pc => pc.transceivers[0].sender.track === a.tracks[0]);
  const camera = a.tracks.find(track => track.label.endsWith('camera')), screen = a.tracks.find(track => track.label.endsWith('screen'));
  assert.equal(peer.transceivers[1].sender.track, camera); assert.equal(peer.transceivers[2].sender.track, screen);
  screen.end(); await flush();
  assert.equal(a.call.snapshot().screen, false); assert.equal(a.call.snapshot().camera, true);
  assert.equal(peer.transceivers[1].sender.track, camera); assert.equal(peer.transceivers[2].sender.track, null);
  assert.equal(env.signals.filter(signal => signal.kind === 'offer').length, 1, 'toggling does not renegotiate');
  await env.close(); assert.equal(camera.readyState, 'ended');
});

test('cancelled camera permission cannot resurrect video or block a later permission request', async () => {
  const env = environment(), old = defer(), recent = defer(); let cameras = 0;
  const client = env.client(1, {getUserMedia: c => c.audio ? Promise.resolve(new Stream([new Track('audio')])) : ++cameras === 1 ? old.promise : recent.promise});
  await client.call.join({roomId: 'room'});
  const first = client.call.setCamera(true); await client.call.setCamera(false); assert.equal(await first, false);
  const second = client.call.setCamera(true), current = new Track('video', 'current'); recent.resolve(new Stream([current]));
  assert.equal(await second, true);
  const late = new Track('video', 'late'); old.resolve(new Stream([late])); await flush();
  assert.equal(late.readyState, 'ended'); assert.equal(current.readyState, 'live'); assert.equal(client.call.snapshot().camera, true);
  await env.close();
});

test('viewers and other rooms cannot become peers; an overfull response stops bounded media', async () => {
  const env = environment(), client = env.client(1); await client.call.join({roomId: 'room'});
  client.call.syncPresence({roomId: 'other', presence: [{connection_id: id(2), in_call: true}]});
  client.call.syncPresence({roomId: 'room', presence: [{connection_id: id(2), in_call: false}, ...env.rows('room')]});
  assert.equal(env.pcs.length, 0);
  const participants = [1, 2, 3, 4].map(number => ({connection_id: id(number), in_call: true, display_name: 'Person'}));
  client.call.syncPresence({roomId: 'room', presence: participants}); await flush();
  assert.equal(env.pcs.length, 3);
  client.call.syncPresence({roomId: 'room', presence: [...participants, {connection_id: id(5), in_call: true}]}); await flush();
  assert.equal(client.call.snapshot().active, false); assert.equal(env.pcs.length, 3); assert.ok(env.pcs.every(peer => peer.closed));
  assert.equal(client.tracks[0].readyState, 'ended'); await env.close();
});

test('membership denial stops tracks and peer connections before another poll', async () => {
  const env = environment(); const [a, b] = await connectedPair(env);
  await a.call.setCamera(true); a.options.revoked = true;
  await env.clock.advance(700);
  assert.equal(a.call.snapshot().active, false); assert.match(a.call.snapshot().status, /access changed/);
  assert.ok(a.tracks.every(track => track.readyState === 'ended'));
  assert.equal(a.streamStates.at(-1).remote.length, 0);
  const calls = a.apiCalls.length; await env.clock.advance(3000); assert.equal(a.apiCalls.length, calls);
  await env.close();
});

test('actual peer failure is surfaced and closed without an automatic reconnect loop', async () => {
  const env = environment(); const [a] = await connectedPair(env);
  const peer = env.pcs.find(pc => pc.transceivers[0].sender.track === a.tracks[0]);
  peer.connectionState = 'failed'; peer.onconnectionstatechange();
  assert.equal(peer.closed, true); assert.equal(a.call.snapshot().phase, 'degraded');
  assert.match(a.call.snapshot().status, /could not connect/); assert.match(a.call.snapshot().status, /No relay/);
  a.call.syncPresence({roomId: 'room', presence: env.rows('room')}); await flush();
  assert.equal(env.pcs.length, 2); await env.close();
});

test('a departed signal recipient fails that peer without treating it as workspace revocation', async () => {
  const env = environment(); const [a] = await connectedPair(env);
  a.options.api = request => request.method === 'POST' && request.path.endsWith('/signals') ?
    Promise.reject(Object.assign(new Error('Peer left'), {status: 403, code: 'team_connection_required'})) : undefined;
  const peer = env.pcs.find(pc => pc.transceivers[0].sender.track === a.tracks[0]);
  peer.onicecandidate({candidate: {candidate: 'candidate:late', sdpMid: '0', sdpMLineIndex: 0}});
  await flush();
  assert.equal(peer.closed, true); assert.equal(a.call.snapshot().active, true);
  assert.equal(a.call.snapshot().phase, 'degraded'); assert.equal(a.tracks[0].readyState, 'live');
  await env.close();
});

test('leave synchronously stops all media and invalidates a late screen permission', async () => {
  const env = environment(), pending = defer(), a = env.client(1, {getDisplayMedia: () => pending.promise}), b = env.client(100);
  await a.call.join({roomId: 'room'}); await b.call.join({roomId: 'room'});
  a.call.syncPresence({roomId: 'room', presence: env.rows('room')}); await flush(); await env.clock.advance(1500);
  await a.call.setCamera(true); const sharing = a.call.setScreen(true);
  const leaving = a.call.leave();
  assert.equal(a.call.snapshot().active, false); assert.ok(a.tracks.every(track => track.readyState === 'ended'));
  assert.equal(a.streamStates.at(-1).remote.length, 0);
  await leaving; assert.equal(await sharing, false);
  const late = new Track('video', 'late-screen'); pending.resolve(new Stream([late])); await flush();
  assert.equal(late.readyState, 'ended'); assert.equal(a.call.snapshot().screen, false);
  await env.close(); assert.equal(env.clock.tasks.size, 0);
});

test('snapshots use only an existing local source and never start devices or upload', async () => {
  const env = environment(), blobReady = defer(), nodes = [];
  const document = {createElement(tag) {
    const node = tag === 'video' ? {readyState: 2, videoWidth: 1920, videoHeight: 1080, play: async () => {}, pause() { this.paused = true; }} :
      {getContext: () => ({drawImage() {}}), toBlob(callback) { blobReady.promise.then(callback); }};
    nodes.push(node); return node;
  }};
  const client = env.client(1, {document}); await client.call.join({roomId: 'room'});
  await assert.rejects(client.call.captureFrame({source: 'camera'}), /not currently shared/);
  await client.call.setCamera(true); const before = client.devices.length, requests = env.requests.length;
  await assert.rejects(client.call.captureFrame({connectionId: id(2), source: 'camera'}), /only from your own/);
  const capture = client.call.captureFrame({source: 'camera'}); await flush();
  blobReady.resolve(new Blob(['synthetic'], {type: 'image/jpeg'}));
  const value = await capture;
  assert.equal(value.width, 1024); assert.equal(value.height, 576); assert.equal(value.connectionId, client.call.snapshot().connectionId);
  assert.equal(client.devices.length, before); assert.equal(env.requests.length, requests);
  assert.equal(nodes[0].srcObject, null); assert.equal(nodes[0].paused, true); await env.close();
});

test('turning off the selected source invalidates a snapshot still being encoded', async () => {
  const env = environment(), pending = defer();
  const document = {createElement: tag => tag === 'video' ? {readyState: 2, videoWidth: 10, videoHeight: 10, play: async () => {}, pause() {}} :
    {getContext: () => ({drawImage() {}}), toBlob(callback) { pending.promise.then(callback); }}};
  const client = env.client(1, {document}); await client.call.join({roomId: 'room'}); await client.call.setCamera(true);
  const capture = client.call.captureFrame({source: 'camera'}); await flush();
  await client.call.setCamera(false); pending.resolve(new Blob(['old frame']));
  await assert.rejects(capture, /could not be prepared/); await env.close();
});
