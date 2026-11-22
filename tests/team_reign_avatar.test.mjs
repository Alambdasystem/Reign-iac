import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../services/ax/reign-avatar.js', import.meta.url), 'utf8')
  .replaceAll('export function ', 'function ').replaceAll('export const ', 'const ');
function harness({hold = false} = {}) {
  let resolve, now = 1000, next = 0, images = 0;
  const pending = new Promise(yes => { resolve = yes; });
  const frames = new Map(), listeners = new Map(), urls = [], statuses = [];
  const document = {hidden: false, addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); }};
  class Image {
    constructor() { images++; this.naturalWidth = 768; this.naturalHeight = 4096; }
    set src(value) { urls.push(value); }
    async decode() { if (hold) await pending; }
  }
  const realm = vm.createContext({Image, document, performance: {now: () => now}, setTimeout, clearTimeout,
    requestAnimationFrame(callback) { frames.set(++next, callback); return next; },
    cancelAnimationFrame(id) { frames.delete(id); }});
  vm.runInContext(source, realm);
  function create() {
    const draws = [], ctx = {fillRect() {}, drawImage(...args) { draws.push(args); }};
    const canvas = {width: 0, height: 0, dataset: {}, getContext: () => ctx};
    const avatar = realm.createReignAvatar({canvas, onStatus: value => statuses.push(value)});
    return {canvas, avatar, draws};
  }
  function advance(ms = 50) {
    now += ms;
    for (const [id, callback] of [...frames]) { frames.delete(id); callback(now); }
  }
  return {create, advance, frames, document, listeners, urls, statuses, resolve, get images() { return images; }};
}

test('multiple portraits share one versioned image decode without an audio or model runtime', async () => {
  const h = harness(), first = h.create(), second = h.create();
  assert.equal(await first.avatar.ready, true); assert.equal(await second.avatar.ready, true);
  assert.equal(h.images, 1); assert.equal(h.urls.length, 1);
  assert.match(h.urls[0], /portrait-atlas\.webp\?v=[a-f0-9]{64}$/);
  assert.equal(first.canvas.dataset.ready, 'true'); assert.ok(first.draws.length > 0);
  first.avatar.destroy(); h.advance();
  assert.equal(second.canvas.dataset.ready, 'true'); assert.ok(second.draws.length > first.draws.length);
  const third = h.create(); await third.avatar.ready; assert.equal(h.images, 1);
  assert.doesNotMatch(source, /new Worker|\.onnx|new Audio|AudioContext|fetch\(/);
  second.avatar.destroy(); third.avatar.destroy();
});

test('idle never talks and active output closes immediately on stop or interruption', async () => {
  const h = harness(), {avatar, canvas} = h.create(); await avatar.ready;
  avatar.setSpeechLevel(.8); h.advance();
  assert.equal(canvas.dataset.mouth, '0.000'); assert.equal(canvas.dataset.speaking, 'false');
  avatar.setSpeaking(true); h.advance();
  assert.equal(canvas.dataset.mouth, '0.000', 'an inactive level must not leak into the next turn');
  avatar.setSpeechLevel(.8); h.advance(100);
  assert.ok(Number(canvas.dataset.mouth) > .5); assert.equal(canvas.dataset.speaking, 'true');
  avatar.setSpeaking(false);
  assert.equal(canvas.dataset.mouth, '0.000'); assert.equal(canvas.dataset.speaking, 'false');
  avatar.setSpeaking(true); avatar.setSpeechLevel(.9); avatar.setExpression('warm'); h.advance(100);
  assert.equal(canvas.dataset.expression, 'warm');
  avatar.stop(); assert.equal(canvas.dataset.mouth, '0.000'); assert.equal(canvas.dataset.expression, 'neutral');
  avatar.destroy(); assert.equal(canvas.width, 0); assert.equal(canvas.dataset.state, 'destroyed');
  assert.equal(h.frames.size, 0);
});

test('page visibility pauses presentation and resumes from current speech state', async () => {
  const h = harness(), {avatar, canvas} = h.create(); await avatar.ready;
  avatar.setSpeaking(true); avatar.setSpeechLevel(.8); h.advance(100);
  h.document.hidden = true; h.listeners.get('visibilitychange')();
  assert.equal(h.frames.size, 0);
  avatar.stop(); assert.equal(canvas.dataset.mouth, '0.000');
  h.document.hidden = false; h.listeners.get('visibilitychange')(); h.advance(50);
  assert.equal(canvas.dataset.speaking, 'false'); assert.equal(canvas.dataset.mouth, '0.000');
  avatar.destroy();
});

test('destroy before decoding cannot resurrect or paint a retired canvas', async () => {
  const h = harness({hold: true}), {avatar, canvas, draws} = h.create();
  avatar.destroy(); h.resolve();
  assert.equal(await avatar.ready, false); assert.equal(canvas.width, 0);
  assert.equal(canvas.dataset.ready, 'false'); assert.equal(draws.length, 0); assert.equal(h.frames.size, 0);
});

test('verified playback separates activity from silence and blends expressions on its media clock', async () => {
  const h = harness(), {avatar, canvas} = h.create(); await avatar.ready;
  const state = {active: true, level: .8, expression: 'warm', clockMs: 50, cueMs: 0, expressionSource: 'response-segment', segment: 1};
  avatar.setPlaybackState(state); h.advance(50);
  avatar.setPlaybackState({...state, clockMs: 150}); h.advance(100);
  assert.ok(Number(canvas.dataset.mouth) > .5);
  const progress = canvas.dataset.expressionProgress, mouth = canvas.dataset.mouth;
  h.advance(500);
  assert.equal(canvas.dataset.expressionProgress, progress); assert.equal(canvas.dataset.mouth, mouth);
  avatar.setPlaybackState({...state, level: 0, clockMs: 180}); h.advance(50);
  assert.equal(canvas.dataset.speaking, 'true');
  assert.ok(Number(canvas.dataset.mouth) > 0 && Number(canvas.dataset.mouth) < Number(mouth), 'silent PCM eases rather than resets a live mouth');
  assert.equal(canvas.dataset.expressionSource, 'response-segment'); assert.equal(canvas.dataset.audioTime, '0.180');
  avatar.destroy();
});

test('an explicit inactive neutral state clears an interrupted sentence and its model cue immediately', async () => {
  const h = harness(), {avatar, canvas} = h.create(); await avatar.ready;
  avatar.setPlaybackState({active: true, level: .8, expression: 'warm', clockMs: 1000, cueMs: 900, expressionSource: 'response-segment', segment: 2});
  h.advance(50);
  assert.equal(canvas.dataset.expression, 'warm');
  avatar.setPlaybackState({active: false, level: 0, expression: 'neutral', segment: 0});
  assert.equal(canvas.dataset.expression, 'neutral'); assert.equal(canvas.dataset.expressionSource, 'neutral');
  assert.equal(canvas.dataset.mouth, '0.000'); assert.equal(canvas.dataset.speaking, 'false');
  assert.equal(canvas.dataset.segment, '0'); assert.equal(canvas.dataset.audioTime, '0.000');
  h.advance(500); assert.equal(canvas.dataset.expression, 'neutral');
  avatar.destroy();
});
