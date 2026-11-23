import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../services/ax/team-camera.js', import.meta.url), 'utf8');
const {createTeamCamera} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }

function track(kind = 'video') {
  return {kind, readyState: 'live', stops: 0, onended: null,
    stop() { this.stops++; this.readyState = 'ended'; }};
}
function stream(tracks = [track()]) {
  return {getTracks: () => tracks, getVideoTracks: () => tracks.filter(value => value.kind === 'video'),
    getAudioTracks: () => tracks.filter(value => value.kind === 'audio')};
}
function harness({permission, holdVideo = false, holdEncoding = false, sizes = [1000], width = 1920, height = 1080} = {}) {
  const requests = [], statuses = [], timers = new Map(), canvases = [], draws = [], encodes = [], streams = [];
  const events = new Map(), play = deferred(); let timer = 0;
  const video = {dataset: {}, readyState: 0, videoWidth: 0, videoHeight: 0, srcObject: null, paused: true,
    addEventListener(name, handler) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(handler); },
    removeEventListener(name, handler) { events.get(name)?.delete(handler); },
    pause() { this.paused = true; },
    play() { this.paused = false; if (holdVideo) return play.promise; this.readyState = 2; this.videoWidth = width; this.videoHeight = height; return Promise.resolve(); },
  };
  const mediaDevices = {getUserMedia(options) {
    requests.push(options);
    if (permission) return permission(requests.length);
    const value = stream(); streams.push(value); return Promise.resolve(value);
  }};
  const doc = {createElement(tag) {
    assert.equal(tag, 'canvas');
    const canvas = {width: 0, height: 0, getContext: () => ({drawImage(...args) { draws.push(args); }}),
      toBlob(callback, type, quality) {
        encodes.push({callback, type, quality});
        if (!holdEncoding) queueMicrotask(() => callback(new Blob([new Uint8Array(sizes[Math.min(encodes.length - 1, sizes.length - 1)])], {type: 'image/jpeg'})));
      }};
    canvases.push(canvas); return canvas;
  }};
  const camera = createTeamCamera({video, mediaDevices, document: doc, onState: value => statuses.push(value),
    timers: {setTimeout(callback, delay) { const id = ++timer; timers.set(id, {callback, delay}); return id; }, clearTimeout(id) { timers.delete(id); }}});
  return {camera, video, requests, statuses, timers, canvases, draws, encodes, streams, play,
    ready() { video.readyState = 2; video.videoWidth = width; video.videoHeight = height; for (const handler of [...events.get('loadeddata') || []]) handler(); },
    timeout(delay) { const task = [...timers.entries()].find(([, value]) => value.delay === delay); assert.ok(task); timers.delete(task[0]); task[1].callback(); }};
}

test('the private preview starts only on request, asks for video without a microphone, and owns its cleanup', async () => {
  const h = harness();
  assert.equal(h.requests.length, 0); assert.equal(h.video.srcObject, null);
  const starting = h.camera.start();
  assert.equal(h.requests.length, 1, 'camera permission begins within the calling gesture');
  assert.equal(h.requests[0].audio, false);
  assert.equal(h.requests[0].video.facingMode, 'user');
  assert.equal(await starting, true); assert.equal(h.camera.snapshot().active, true);
  assert.equal(h.video.muted, true); assert.equal(h.video.playsInline, true);
  assert.equal(h.encodes.length, 0, 'opening the preview does not capture any image');
  h.camera.stop();
  assert.equal(h.streams[0].getTracks()[0].readyState, 'ended');
  assert.equal(h.video.srcObject, null); assert.equal(h.video.paused, true);
  assert.equal(h.camera.snapshot().active, false); assert.equal(h.timers.size, 0);
});

test('closing during a permission prompt stops a late camera stream instead of reopening preview', async () => {
  const permission = deferred(), value = stream();
  const h = harness({permission: () => permission.promise});
  const starting = h.camera.start(); h.camera.stop(); permission.resolve(value);
  assert.equal(await starting, false);
  assert.equal(value.getTracks()[0].readyState, 'ended'); assert.equal(h.video.srcObject, null);
  assert.equal(h.camera.snapshot().active, false);
});

test('an old permission response cannot replace or stop a newer explicitly started camera', async () => {
  const old = deferred(), previous = stream(), latest = stream();
  const h = harness({permission: count => count === 1 ? old.promise : Promise.resolve(latest)});
  const first = h.camera.start(); h.camera.stop();
  assert.equal(await h.camera.start(), true);
  old.resolve(previous); assert.equal(await first, false);
  assert.equal(h.video.srcObject, latest); assert.equal(latest.getTracks()[0].readyState, 'live');
  assert.equal(previous.getTracks()[0].readyState, 'ended'); h.camera.dispose();
});

test('permission denial is visible and requires a new explicit attempt', async () => {
  const h = harness({permission: count => count === 1
    ? Promise.reject(Object.assign(Error('denied'), {name: 'NotAllowedError'})) : Promise.resolve(stream())});
  assert.equal(await h.camera.start(), false);
  assert.equal(h.statuses.at(-1).phase, 'error'); assert.match(h.statuses.at(-1).message, /permission was not granted/);
  await flush(); assert.equal(h.requests.length, 1);
  assert.equal(await h.camera.start(), true); assert.equal(h.requests.length, 2);
  h.camera.dispose(); assert.equal(await h.camera.start(), false);
});

test('Describe captures the same unmirrored video into a bounded JPEG and releases its canvas', async () => {
  const h = harness({width: 1920, height: 1080}); await h.camera.start();
  const capture = await h.camera.capture();
  assert.equal(capture.width, 960); assert.equal(capture.height, 540);
  assert.equal(capture.blob.type, 'image/jpeg'); assert.ok(capture.blob.size <= 512 * 1024);
  assert.match(capture.capturedAt, /^\d{4}-\d\d-\d\dT/); assert.ok(Number.isFinite(Date.parse(capture.capturedAt)));
  assert.equal(h.draws[0][0], h.video); assert.deepEqual(h.draws[0].slice(1), [0, 0, 960, 540]);
  assert.equal(h.encodes[0].type, 'image/jpeg');
  assert.equal(h.canvases[0].width, 0); assert.equal(h.canvases[0].height, 0);
  assert.equal(h.video.srcObject.getTracks()[0].readyState, 'live', 'capturing does not silently close the preview');
  h.camera.dispose();
});

test('portrait video fits within both image limits and oversized encodes have a finite fallback', async () => {
  const h = harness({width: 720, height: 1280, sizes: [600000, 550000, 400000]}); await h.camera.start();
  const capture = await h.camera.capture();
  assert.equal(capture.width, 304); assert.equal(capture.height, 540); assert.equal(capture.blob.size, 400000);
  assert.deepEqual(h.encodes.map(value => value.quality), [.84, .7, .55]); h.camera.dispose();
  const tooLarge = harness({sizes: [600000]}); await tooLarge.camera.start();
  await assert.rejects(tooLarge.camera.capture(), /too large/);
  assert.equal(tooLarge.encodes.length, 3); assert.equal(tooLarge.canvases[0].width, 0); tooLarge.camera.dispose();
});

test('stopping during an image encode discards its result and frees the captured backing buffer', async () => {
  const h = harness({holdEncoding: true}); await h.camera.start();
  const capture = h.camera.capture(); h.camera.stop();
  h.encodes[0].callback(new Blob([new Uint8Array(1000)], {type: 'image/jpeg'}));
  await assert.rejects(capture, error => error.name === 'AbortError');
  assert.equal(h.video.srcObject, null); assert.equal(h.canvases[0].width, 0); assert.equal(h.timers.size, 0);
});

test('a missing video frame times out, and a disconnected track immediately stops the preview', async () => {
  const missing = harness({holdVideo: true}); const start = missing.camera.start(); await flush();
  missing.timeout(8000); assert.equal(await start, false);
  assert.equal(missing.streams[0].getTracks()[0].readyState, 'ended');
  assert.match(missing.statuses.at(-1).message, /no current frame/);
  const h = harness(); await h.camera.start();
  h.streams[0].getTracks()[0].onended();
  assert.equal(h.camera.snapshot().active, false); assert.equal(h.video.srcObject, null);
  assert.match(h.statuses.at(-1).message, /disconnected/);
});
