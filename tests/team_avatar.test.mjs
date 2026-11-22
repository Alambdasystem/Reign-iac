import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import vm from 'node:vm';

const portraitCode = fs.readFileSync(new URL('../services/ax/reign-avatar.js', import.meta.url), 'utf8')
  .replaceAll('export function ', 'function ').replaceAll('export const ', 'const ');
const code = fs.readFileSync(new URL('../services/ax/team-avatar.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '').replace(/^export \{.*;$/gm, '').replaceAll('export function ', 'function ');
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; }
function harness({hold = false, holdPortrait = false, portraitFails = false, holdDecode = false, resumeFails = 0, outputLatency = 0} = {}) {
  const sources = [], calls = [], statuses = [], frames = new Map(), pending = deferred();
  const portraitPending = deferred(), decodePending = deferred(), contexts = [], timers = new Map();
  let now = 1000;
  const drawing = [];
  const ctx = {fillRect() {}, drawImage(...args) { drawing.push(args); }};
  const canvas = {width: 0, height: 0, dataset: {}, getContext: () => ctx};
  class AudioContext {
    state = 'suspended'; destination = {}; closed = false; currentTime = 1; sampleRate = 24000; listeners = new Map();
    constructor() { contexts.push(this); }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    removeEventListener(name) { this.listeners.delete(name); }
    async resume() { if (resumeFails-- > 0) throw Error('Autoplay blocked'); this.state = 'running'; this.listeners.get('statechange')?.(); }
    async suspend() { this.state = 'suspended'; this.listeners.get('statechange')?.(); }
    async close() { this.closed = true; this.state = 'closed'; }
    getOutputTimestamp() { return {contextTime: Math.max(0, this.currentTime - outputLatency), performanceTime: now}; }
    async decodeAudioData() {
      if (holdDecode) await decodePending.promise;
      const pcm = new Float32Array(24000);
      for (let i = 4800; i < 19200; i++) pcm[i] = Math.sin(i / 24000 * 220 * Math.PI * 2) * .24;
      return {duration: 1, numberOfChannels: 1, sampleRate: 24000, getChannelData: () => pcm};
    }
    createAnalyser() { return {connect() {}, disconnect() {}, getFloatTimeDomainData(values) { values.fill(.08); }}; }
    createBufferSource() {
      const source = {started: false, stopped: false, disconnected: false, ended: false, context: this, connect() {},
        disconnect() { this.disconnected = true; }, start(when) { this.started = true; this.startTime = when; },
        stop() { this.stopped = true; }, onended: null};
      sources.push(source); return source;
    }
  }
  class Image { naturalWidth = 768; naturalHeight = 4096;
    async decode() { if (holdPortrait) await portraitPending.promise; if (portraitFails) throw Error('Unavailable'); }
  }
  let timer = 0;
  const realm = vm.createContext({Image, Float32Array, Math, AbortController, performance: {now: () => now},
    document: {hidden: false, addEventListener() {}, removeEventListener() {}},
    window: {AudioContext}, setTimeout: (callback, delay) => { const id = ++timer; timers.set(id, {callback, at: now + delay}); return id; },
    clearTimeout: id => timers.delete(id),
    requestAnimationFrame: cb => { frames.set(++timer, cb); return timer; }, cancelAnimationFrame: id => frames.delete(id)});
  vm.runInContext(portraitCode, realm);
  vm.runInContext(code, realm);
  const player = realm.createTeamAvatar({canvas, onStatus: value => statuses.push(value),
    api: async (...args) => { calls.push(args); if (hold) await pending.promise; return {size: 1000, arrayBuffer: async () => new ArrayBuffer(1000)}; }});
  function advance(ms, {audio = true} = {}) {
    now += ms;
    for (const context of contexts) if (audio && context.state === 'running') context.currentTime += ms / 1000;
    for (const source of sources) if (source.started && !source.ended && !source.stopped && source.context.currentTime >= source.startTime + source.buffer.duration) {
      source.ended = true; source.onended?.();
    }
    for (const [id, value] of [...timers]) if (value.at <= now) { timers.delete(id); value.callback(); }
    for (const [id, callback] of [...frames]) { frames.delete(id); callback(now); }
  }
  return {player, canvas, sources, calls, statuses, frames, pending, portraitPending, realm, contexts, advance, decodePending, timers};
}
const event = {data: {speech_status: 'ready', segments: [{text: 'Hello team.', expression: 'warm', audio_id: '123e4567-e89b-42d3-a456-426614174000'}]}};

test('portrait atlas is small, pinned, and explicitly separate from live inference', () => {
  const base = new URL('../services/ax/team-avatar-assets/', import.meta.url);
  const manifest = JSON.parse(fs.readFileSync(new URL('manifest.json', base)));
  const data = fs.readFileSync(new URL('portrait-atlas.webp', base));
  assert.equal(crypto.createHash('sha256').update(data).digest('hex'), manifest.atlas_sha256);
  assert.equal(data.length, manifest.bytes); assert.ok(data.length < 512 * 1024);
  assert.equal(manifest.frames.length, 48); assert.equal(manifest.mode, 'precomputed_neural_portrait');
  assert.doesNotMatch(code, /new Worker|\.onnx|createPortraitEngine/);
});

test('mouth follows measured audio energy, including silence and bounded peaks', () => {
  const {realm, player} = harness();
  assert.equal(realm.speechEnvelope(new Float32Array(256)), 0);
  assert.ok(realm.speechEnvelope(new Float32Array(256).fill(.1)) > .4);
  assert.equal(realm.speechEnvelope(new Float32Array(256).fill(1)), .9);
  assert.equal(realm.mixAxis(-999, -3, 3, 3)[0][1], 1);
  assert.equal(realm.mixAxis(999, -3, 3, 3)[1][1], 1);
  player.destroy();
});

test('a response waits for a listening gesture before requesting or playing audio', async () => {
  const h = harness(); await flush();
  await h.player.play('room', event);
  assert.equal(h.calls.length, 0); assert.equal(h.sources.length, 0);
  assert.equal(h.statuses.at(-1).state, 'paused');
  await h.player.unlock(); await flush();
  h.advance(50);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0][3].responseType, 'blob');
  assert.equal(h.sources.length, 1); assert.equal(h.sources[0].started, true);
  assert.equal(h.statuses.at(-1).state, 'speaking');
  h.player.destroy(); assert.equal(h.sources[0].stopped, true); assert.equal(h.canvas.width, 0);
});

test('leaving a room discards a late audio response and never starts it', async () => {
  const h = harness({hold: true}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  h.player.stop(); h.pending.resolve(); await play;
  assert.equal(h.sources.length, 0); h.player.destroy();
});

test('unavailable speech leaves the saved text usable without synthetic talking', async () => {
  const h = harness(); await h.player.unlock();
  await h.player.play('room', {data: {text: 'Saved reply', speech_status: 'unavailable', segments: [{text: 'Saved reply', expression: 'neutral'}]}});
  assert.equal(h.calls.length, 0); assert.equal(h.sources.length, 0);
  assert.equal(h.statuses.at(-1).state, 'error'); h.player.destroy();
});

test('a reply remains audible while the portrait loads and the portrait joins the current output', async () => {
  const h = harness({holdPortrait: true}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  h.advance(50);
  assert.equal(h.calls.length, 1); assert.equal(h.sources.length, 1);
  assert.equal(h.canvas.dataset.ready, 'false'); assert.equal(h.canvas.dataset.mouth, '0.000');
  h.portraitPending.resolve(); await flush();
  assert.equal(h.canvas.dataset.ready, 'true');
  assert.equal(h.sources.length, 1); assert.equal(h.sources[0].started, true);
  assert.equal(h.statuses.at(-1).state, 'speaking', 'late image readiness must not replace active speech status');
  h.player.stop(); await play; h.player.destroy();
});

test('portrait loading failure does not disable valid speech or invent mouth pixels', async () => {
  const h = harness({portraitFails: true}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  assert.equal(h.sources.length, 1); assert.equal(h.calls.length, 1);
  assert.equal(h.sources[0].started, true);
  assert.equal(h.canvas.dataset.state, 'error');
  assert.equal(h.canvas.dataset.ready, 'false'); assert.equal(h.canvas.dataset.mouth, '0.000');
  assert.ok(h.statuses.some(value => /face could not load/.test(value.message)));
  h.player.stop(); await play; assert.equal(h.canvas.dataset.speaking, 'false'); h.player.destroy();
});

test('the mouth and sentence expression follow output PCM time rather than the render clock', async () => {
  const h = harness({outputLatency: .1}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  h.advance(50);
  assert.equal(h.canvas.dataset.expression, 'neutral', 'a scheduled sentence has not reached the output device yet');
  assert.equal(h.canvas.dataset.speaking, 'false');
  h.advance(150);
  assert.equal(h.canvas.dataset.expression, 'warm');
  assert.equal(h.canvas.dataset.expressionSource, 'response-segment');
  assert.equal(h.canvas.dataset.segment, '1');
  assert.equal(h.canvas.dataset.mouth, '0.000', 'the clip begins with actual silence');
  h.advance(250);
  assert.ok(Number(h.canvas.dataset.mouth) > .4);
  const mouth = h.canvas.dataset.mouth, progress = h.canvas.dataset.expressionProgress;
  h.advance(500, {audio: false});
  assert.equal(h.canvas.dataset.mouth, mouth, 'screen time cannot advance a frozen audio envelope');
  assert.equal(h.canvas.dataset.expressionProgress, progress);
  h.advance(450); h.advance(100);
  assert.ok(Number(h.canvas.dataset.mouth) < .35, 'silent trailing PCM eases the mouth closed');
  h.advance(200); await play;
  assert.equal(h.canvas.dataset.speaking, 'false'); assert.equal(h.canvas.dataset.expression, 'neutral');
  assert.equal(h.timers.size, 0, 'natural completion releases the playback timers');
  h.player.destroy();
});

test('suspension closes the mouth and resumes the same source without fetching or replaying a sentence', async () => {
  const h = harness(); await h.player.unlock();
  const play = h.player.play('room', event); await flush(); h.advance(50); h.advance(300);
  assert.ok(Number(h.canvas.dataset.mouth) > .4);
  const context = h.contexts[0], at = context.currentTime;
  await context.suspend();
  assert.equal(h.canvas.dataset.speaking, 'false'); assert.equal(h.canvas.dataset.mouth, '0.000');
  assert.match(h.statuses.at(-1).message, /audio is paused/i); assert.equal(h.statuses.at(-1).audioEnabled, false);
  h.advance(1000); assert.equal(context.currentTime, at);
  await h.player.unlock(); h.advance(80);
  assert.equal(h.sources.length, 1); assert.equal(h.calls.length, 1);
  assert.ok(Number(h.canvas.dataset.mouth) > .3);
  h.player.stop(); await play; h.player.destroy();
});

test('a context suspended during decoding waits for a new gesture before scheduling audio', async () => {
  const h = harness({holdDecode: true}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  await h.contexts[0].suspend(); h.decodePending.resolve(); await flush();
  assert.equal(h.sources.length, 0); assert.equal(h.canvas.dataset.speaking, 'false');
  assert.equal(h.statuses.at(-1).state, 'paused');
  await h.player.unlock(); await flush();
  assert.equal(h.sources.length, 1); assert.equal(h.calls.length, 1);
  h.player.stop(); await play; h.player.destroy();
});

test('leaving while a decoded clip waits for audio prevents a late resume from playing it', async () => {
  const h = harness({holdDecode: true}); await h.player.unlock();
  const play = h.player.play('room', event); await flush();
  await h.contexts[0].suspend(); h.decodePending.resolve(); await flush();
  h.player.stop(); await play;
  assert.equal(h.calls[0][3].signal.aborted, true);
  await h.player.unlock(); await flush();
  assert.equal(h.sources.length, 0); assert.equal(h.canvas.dataset.mouth, '0.000');
  h.player.destroy();
});

test('a second expression starts at its sentence audio cue and both buffers complete once', async () => {
  const h = harness(); await h.player.unlock();
  const second = {text: 'A concerned sentence.', expression: 'concerned', audio_id: '123e4567-e89b-42d3-a456-426614174001'};
  const play = h.player.play('room', {data: {speech_status: 'ready', segments: [event.data.segments[0], second]}});
  await flush(); h.advance(400);
  assert.equal(h.canvas.dataset.expression, 'warm');
  h.advance(650); await flush();
  assert.equal(h.sources.length, 2);
  assert.equal(h.canvas.dataset.expression, 'warm', 'fetching the next sentence does not select its expression early');
  h.advance(50);
  assert.equal(h.canvas.dataset.expression, 'concerned'); assert.equal(h.canvas.dataset.segment, '2');
  h.advance(1100); await play;
  assert.equal(h.calls.length, 2); assert.equal(h.sources.length, 2);
  assert.equal(h.canvas.dataset.expression, 'neutral'); assert.equal(h.canvas.dataset.speaking, 'false');
  h.player.destroy();
});

test('an autoplay denial keeps one pending reply and a later listening gesture can enable it', async () => {
  const h = harness({resumeFails: 1});
  assert.equal(await h.player.unlock(), false);
  await h.player.play('room', event);
  assert.equal(h.calls.length, 0); assert.equal(h.sources.length, 0);
  assert.equal(await h.player.unlock(), true); await flush(); h.advance(350);
  assert.equal(h.sources.length, 1); assert.equal(h.canvas.dataset.speaking, 'true');
  h.player.destroy();
});

test('output clock uses device timestamps with a bounded latency fallback', () => {
  const h = harness();
  assert.equal(h.realm.audioOutputTime({currentTime: 2, getOutputTimestamp: () => ({contextTime: 1.7, performanceTime: 1000})}, 1020), 1.72);
  assert.equal(h.realm.audioOutputTime({currentTime: 2, baseLatency: .02, outputLatency: .15}, 1000), 1.83);
  assert.equal(h.realm.audioOutputTime({currentTime: 2, getOutputTimestamp: () => ({contextTime: 3, performanceTime: 1000})}, 1020), 2);
  assert.throws(() => h.realm.pcmEnvelope({duration: 31, numberOfChannels: 1, sampleRate: 24000}));
  assert.throws(() => h.realm.pcmEnvelope({duration: 1, numberOfChannels: 2, sampleRate: 24000}));
  h.player.destroy();
});

test('a long suspension does not consume the bounded output-tail drain interval', async () => {
  const h = harness({outputLatency: .2}); await h.player.unlock();
  const next = {...event.data.segments[0], expression: 'concerned', audio_id: '123e4567-e89b-42d3-a456-426614174001'};
  const play = h.player.play('room', {data: {speech_status: 'ready', segments: [event.data.segments[0], next]}});
  await flush(); h.advance(1050); await flush();
  assert.equal(h.sources[0].ended, true); assert.equal(h.sources.length, 1);
  await h.contexts[0].suspend(); h.advance(5000);
  await h.player.unlock(); await flush();
  assert.equal(h.sources.length, 1, 'resume must preserve the still-unheard tail rather than expire it with wall time');
  h.advance(250); await flush();
  assert.equal(h.sources.length, 2);
  h.player.stop(); await play; h.player.destroy();
});
