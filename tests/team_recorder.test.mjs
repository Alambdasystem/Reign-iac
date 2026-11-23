import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const code = fs.readFileSync(new URL('../services/ax/team-recorder.js', import.meta.url), 'utf8');
const {createTeamRecorder} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const tick = () => new Promise(resolve => setImmediate(resolve));
class Track {
  constructor(enabled = true) { this.enabled = enabled; this.readyState = 'live'; this.stops = 0; this.clones = []; }
  stop() { this.stops++; this.readyState = 'ended'; }
  clone() { const track = new Track(this.enabled); this.clones.push(track); return track; }
}
class Stream {
  constructor(tracks) { this.tracks = tracks; }
  getTracks() { return this.tracks; } getAudioTracks() { return this.tracks; }
}
function harness({holdPermission = false, holdRead = false, detector = false} = {}) {
  const recorders = [], states = [], reads = [], contexts = [], timers = new Map(), granted = [], permission = deferred();
  let acquisitions = 0, timer = 0, now = 0, level = 0;
  class AudioContext {
    state = 'running'; sourceConnections = []; destination = {};
    constructor() { contexts.push(this); }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return {connect: target => this.sourceConnections.push(target), disconnect() {}}; }
    createAnalyser() { return {fftSize: 1024, getFloatTimeDomainData(values) { values.fill(level); }, disconnect() {}}; }
  }
  class Recorder {
    static isTypeSupported(type) { return type === 'audio/webm;codecs=opus'; }
    constructor(stream, options) { this.stream = stream; this.mimeType = options.mimeType; this.state = 'inactive'; recorders.push(this); }
    start(interval) { this.state = 'recording'; this.interval = interval; }
    stop() { this.state = 'inactive'; this.ondataavailable?.({data: new Blob(['synthetic audio'])}); this.onstop?.(); }
  }
  class Reader {
    readAsDataURL(blob) { reads.push(this); if (!holdRead) { this.result = 'data:audio/webm;base64,c3ludGhldGljIGF1ZGlv'; this.onload(); } }
  }
  const recorder = createTeamRecorder({MediaRecorder: Recorder, MediaStream: Stream, FileReader: Reader,
    AudioContext: detector ? AudioContext : undefined, now: () => now,
    mediaDevices: {getUserMedia() { acquisitions++; if (holdPermission) return permission.promise; const stream = new Stream([new Track()]); granted.push(stream); return Promise.resolve(stream); }},
    timers: {setTimeout(fn, delay) { const key = ++timer; timers.set(key, {fn, delay, at: now + delay}); return key; }, clearTimeout(key) { timers.delete(key); }}, onState: state => states.push(state)});
  function advance(ms, value = 0, {freeze = false} = {}) {
    level = value;
    const target = now + ms;
    if (freeze) now = target;
    while (now < target || [...timers.values()].some(task => task.at <= now)) {
      if (!freeze) now = Math.min(target, now + 50);
      for (const [id, task] of [...timers]) if (task.at <= now) { timers.delete(id); task.fn(); }
      if (freeze) break;
    }
  }
  return {recorder, recorders, states, timers, permission, granted, reads, contexts, advance, get acquisitions() { return acquisitions; }};
}

test('dictation is opt-in and recording a call mic owns only a clone', async () => {
  const h = harness(); assert.equal(h.acquisitions, 0); assert.equal(h.recorders.length, 0);
  const original = new Track(), result = h.recorder.start({stream: new Stream([original])}); await tick();
  assert.equal(h.acquisitions, 0); assert.equal(original.clones.length, 1); assert.equal(original.stops, 0);
  assert.equal(h.recorders[0].interval, 250); h.recorder.stop();
  assert.deepEqual(await result, {audio_base64: 'c3ludGhldGljIGF1ZGlv', mime_type: 'audio/webm'});
  assert.equal(original.enabled, true); assert.equal(original.stops, 0); assert.ok(original.clones[0].stops > 0);
});

test('dictation never unmutes or records the disabled call track', async () => {
  const h = harness(), muted = new Track(false), result = h.recorder.start({stream: new Stream([muted])}); await tick();
  assert.equal(h.acquisitions, 1); assert.equal(muted.clones.length, 0); assert.equal(muted.enabled, false);
  h.recorder.stop(); await result; assert.equal(muted.stops, 0); assert.ok(h.granted[0].getTracks()[0].stops > 0);
});

test('late microphone permission after cancel stops the newly granted tracks', async () => {
  const h = harness({holdPermission: true}), result = h.recorder.start();
  const rejected = assert.rejects(result, {name: 'AbortError'}); h.recorder.cancel(); await rejected;
  const track = new Track(); h.permission.resolve(new Stream([track])); await tick();
  assert.ok(track.stops > 0); assert.equal(h.recorders.length, 0); assert.equal(h.states.at(-1).state, 'idle');
});

test('twenty seconds stops capture and produces one bounded recording', async () => {
  const h = harness(), result = h.recorder.start(); await tick();
  const timeout = [...h.timers.values()].find(value => value.delay === 20000); assert.ok(timeout); timeout.fn();
  assert.equal((await result).mime_type, 'audio/webm'); assert.equal(h.recorders[0].state, 'inactive'); assert.equal(h.timers.size, 0);
});

test('oversized capture fails without returning or retaining an audio payload', async () => {
  const h = harness(), result = h.recorder.start(); const rejected = assert.rejects(result, /too large/); await tick();
  h.recorders[0].ondataavailable({data: new Blob([new Uint8Array(512 * 1024 + 1)])}); await rejected;
  assert.equal(h.recorders[0].state, 'inactive'); assert.equal(h.reads.length, 0); assert.ok(h.granted[0].getTracks()[0].stops > 0);
});

test('disposal during encoding cannot return a cancelled voice recording', async () => {
  const h = harness({holdRead: true}), result = h.recorder.start(); const rejected = assert.rejects(result, {name: 'AbortError'}); await tick();
  h.recorder.stop(); assert.equal(h.reads.length, 1); h.recorder.dispose(); await rejected;
  h.reads[0].result = 'data:audio/webm;base64,c3ludGhldGlj'; h.reads[0].onload();
  await assert.rejects(h.recorder.start(), {name: 'AbortError'}); assert.equal(h.states.at(-1).state, 'idle');
});

test('Talk sends after real trailing silence without stopping on leading silence or a short speaking pause', async () => {
  const h = harness({detector: true}), result = h.recorder.start({autoStop: true}); await tick();
  h.advance(1800); assert.equal(h.recorders[0].state, 'recording', 'leading silence is not a completed question');
  h.advance(300, .04); h.advance(500); assert.equal(h.recorders[0].state, 'recording', 'a short pause preserves the same recording');
  h.advance(250, .008); h.advance(700); assert.equal(h.recorders[0].state, 'recording', 'quiet subsequent words extend the turn');
  h.advance(50); assert.equal((await result).mime_type, 'audio/webm');
  assert.equal(h.recorders.length, 1); assert.equal(h.recorders[0].state, 'inactive');
  assert.equal(h.contexts[0].state, 'closed'); assert.equal(h.timers.size, 0);
  assert.ok(h.granted[0].getTracks()[0].stops > 0);
  assert.ok(h.contexts[0].sourceConnections.every(target => target !== h.contexts[0].destination), 'microphone energy never connects to the speakers');
});

test('a brief input click or background noise cannot arm the end-of-question detector', async () => {
  const h = harness({detector: true}), result = h.recorder.start({autoStop: true}); await tick();
  h.advance(50, .2); h.advance(1500, .004);
  assert.equal(h.recorders[0].state, 'recording'); h.recorder.stop(); await result;
  assert.equal(h.contexts[0].state, 'closed');
});

test('suspended and throttled microphone analysis cannot mistake missing samples for silence', async () => {
  const h = harness({detector: true}), result = h.recorder.start({autoStop: true}); await tick();
  h.advance(300, .04); h.contexts[0].state = 'suspended'; h.advance(1600);
  assert.equal(h.recorders[0].state, 'recording'); h.contexts[0].state = 'running';
  h.advance(300, .04); h.advance(2500, 0, {freeze: true});
  assert.equal(h.recorders[0].state, 'recording', 'a frozen tab requires fresh silence samples');
  h.advance(700); assert.equal(h.recorders[0].state, 'recording'); h.advance(50); await result;
  assert.equal(h.recorders.length, 1);
});

test('cancelling automatic capture closes its local analyser and a late permission cannot restart it', async () => {
  const h = harness({detector: true, holdPermission: true}), result = h.recorder.start({autoStop: true});
  const rejected = assert.rejects(result, {name: 'AbortError'}); h.recorder.cancel(); await rejected;
  assert.equal(h.contexts[0].state, 'closed');
  const track = new Track(); h.permission.resolve(new Stream([track])); await tick();
  assert.ok(track.stops > 0); assert.equal(h.recorders.length, 0); assert.equal(h.timers.size, 0);
});
