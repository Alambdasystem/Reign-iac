import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import vm from 'node:vm';

const code = fs.readFileSync(new URL('../services/ax/team-avatar.js', import.meta.url), 'utf8').replaceAll('export function ', 'function ');
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; }
function harness({hold = false} = {}) {
  const sources = [], calls = [], statuses = [], frames = new Map(), pending = deferred();
  const drawing = [];
  const ctx = {fillRect() {}, drawImage(...args) { drawing.push(args); }};
  const canvas = {width: 0, height: 0, dataset: {}, getContext: () => ctx};
  class AudioContext {
    state = 'suspended'; destination = {}; closed = false;
    async resume() { this.state = 'running'; }
    async close() { this.closed = true; this.state = 'closed'; }
    async decodeAudioData() { return {duration: 1, numberOfChannels: 1}; }
    createAnalyser() { return {connect() {}, disconnect() {}, getFloatTimeDomainData(values) { values.fill(.08); }}; }
    createBufferSource() {
      const source = {started: false, stopped: false, disconnected: false, connect() {},
        disconnect() { this.disconnected = true; }, start() { this.started = true; }, stop() { this.stopped = true; }, onended: null};
      sources.push(source); return source;
    }
  }
  class Image { naturalWidth = 768; naturalHeight = 4096; async decode() {} }
  let timer = 0;
  const realm = vm.createContext({Image, Float32Array, Math, performance: {now: () => 1000},
    document: {hidden: false, addEventListener() {}, removeEventListener() {}},
    window: {AudioContext}, requestAnimationFrame: cb => { frames.set(++timer, cb); return timer; }, cancelAnimationFrame: id => frames.delete(id)});
  vm.runInContext(code, realm);
  const player = realm.createTeamAvatar({canvas, onStatus: value => statuses.push(value),
    api: async (...args) => { calls.push(args); if (hold) await pending.promise; return {size: 1000, arrayBuffer: async () => new ArrayBuffer(1000)}; }});
  return {player, canvas, sources, calls, statuses, frames, pending, realm};
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
