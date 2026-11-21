'use strict';

// Execute the actual page controller with controlled image decoding and neural
// worker promises. These tests cover UI ordering, not model quality or inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../services/ax/avatar-lab.js'), 'utf8')
  .replace(/^import[^\n]*\n/gm, '');
const SAMPLE = '/avatar-assets/sample-portrait.png';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
async function eventually(check) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if (check()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.ok(check(), 'Portrait controller did not reach the expected state');
}
class Element {
  constructor(id) {
    this.id = id; this.value = id === 'cropZoom' ? '1' : '0';
    this.disabled = false; this.hidden = false; this.dataset = {};
    this.textContent = ''; this.listeners = new Map(); this.children = new Map();
    this.width = 256; this.height = 256;
  }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  setAttribute(name, value) { this[name] = String(value); }
  removeAttribute(name) { delete this[name]; }
  querySelector(name) {
    if (!this.children.has(name)) this.children.set(name, new Element(name));
    return this.children.get(name);
  }
}
function harness({holdRenders = false, logoutFailures = 0, role = 'owner', sessionStatus = 200} = {}) {
  const nodes = new Map(), images = [], engines = [], encoded = [], rendered = [], drawn = [], revoked = [], windowEvents = new Map();
  const calls = [], navigations = [], announcements = [];
  const animationFrames = new Map(), timers = new Map(), presentations = [], compositors = [];
  let urlId = 0, reloads = 0, activeEncodes = 0, maximumEncodes = 0, clock = 100, nextTimer = 0;
  let activeRenders = 0, maximumRenders = 0;
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  node('sourceCanvas').getContext = () => ({drawImage(image, x, y, side) { node('sourceCanvas').portrait = image.src + (side === 512 ? '' : ':crop-' + side); }});
  node('outputCanvas').getContext = () => ({putImageData(data) { drawn.push(data.portrait); }});
  class FakeImage {
    constructor() { this.naturalWidth = 512; this.naturalHeight = 512; }
    decode() { const pending = deferred(); images.push({image: this, ...pending}); return pending.promise; }
  }
  function createPortraitEngine(options) {
    const engine = {
      provider: 'webgpu', options, portrait: null, disposed: false,
      async init() { return engine; },
      encode(canvas) {
        const pending = deferred(), portrait = canvas.portrait;
        activeEncodes++; maximumEncodes = Math.max(maximumEncodes, activeEncodes);
        encoded.push({portrait, ...pending});
        return pending.promise.then(() => { engine.portrait = portrait; return {provider: engine.provider}; })
          .finally(() => activeEncodes--);
      },
      render(controls) {
        const frame = {imageData: {portrait: engine.portrait}, ms: 12, provider: engine.provider};
        if (!holdRenders) { rendered.push({frame, controls}); return Promise.resolve(frame); }
        const pending = deferred(); rendered.push({frame, controls, ...pending});
        activeRenders++; maximumRenders = Math.max(maximumRenders, activeRenders);
        return pending.promise.finally(() => activeRenders--);
      },
      async dispose(options) { engine.disposed = true; engine.disposeOptions = options; },
    };
    engines.push(engine); return engine;
  }
  function createMotionCompositor() {
    let previous, next, started = 0, duration = 0, active = false, lastProgress = -1;
    const compositor = {
      disposed: false, resets: [], appended: [],
      reset(frame) {
        previous = frame; next = undefined; active = false;
        compositor.resets.push(frame);
        if (frame) drawn.push(frame.portrait);
      },
      append(frame, options) {
        if (active || compositor.disposed) return false;
        next = frame; started = options.now; duration = options.durationMs; active = true; lastProgress = -1;
        compositor.appended.push({frame, ...options}); return true;
      },
      draw(now) {
        if (!active || compositor.disposed) return {active: false, progress: 1, interpolated: false, painted: false};
        const progress = Math.min(1, Math.max(0, (now - started) / duration));
        const painted = progress !== lastProgress && progress > 0;
        lastProgress = progress;
        const interpolated = painted && progress < 1;
        if (painted) presentations.push({from: previous, to: next, progress, now});
        if (progress === 1) { active = false; previous = next; drawn.push(next.portrait); }
        return {active, progress, interpolated, painted};
      },
      dispose() { compositor.disposed = true; previous = next = undefined; active = false; },
    };
    compositors.push(compositor); return compositor;
  }
  const context = vm.createContext({
    document: {getElementById: node, querySelectorAll: () => [], cookie: 'ax_csrf=synthetic-csrf'}, Image: FakeImage,
    window: {addEventListener: (event, handler) => windowEvents.set(event, handler),
      location: {reload: () => reloads++, replace: target => navigations.push(target)}},
    localStorage: {setItem: (key, value) => announcements.push({key, value})},
    AbortController,
    fetch: async (url, options) => {
      calls.push({url, options});
      if (url === '/api/me') return {ok: sessionStatus === 200, status: sessionStatus,
        async json() { return {authenticated: true, role, username: role === 'avatar' ? 'mayur' : 'admin', display_name: role === 'avatar' ? 'Mayur' : 'Owner'}; }};
      if (url === '/logout') {
        if (logoutFailures-- > 0) throw new Error('Synthetic disconnected network');
        return {ok: true, status: 200};
      }
      throw new Error('Unexpected request: ' + url);
    },
    URL: {createObjectURL: () => `blob:portrait-${++urlId}`, revokeObjectURL: url => revoked.push(url)},
    createPortraitEngine, createMotionCompositor, performance: {now: () => clock},
    setTimeout: (callback, delay) => { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    requestAnimationFrame: callback => { const id = ++nextTimer; animationFrames.set(id, callback); return id; },
    cancelAnimationFrame: id => animationFrames.delete(id), console,
  });
  vm.runInContext(source, context, {filename: 'services/ax/avatar-lab.js'});
  return {
    node, images, engines, encoded, rendered, drawn, revoked, windowEvents, calls, navigations, announcements,
    animationFrames, presentations, compositors, timers,
    get maximumEncodes() { return maximumEncodes; }, get reloads() { return reloads; },
    get maximumRenders() { return maximumRenders; },
    setTime(now) { clock = now; },
    paint(now) { clock = now; const callbacks = [...animationFrames.values()]; animationFrames.clear(); callbacks.forEach(callback => callback(now)); },
    input(id, value) { node(id).value = String(value); return node(id).listeners.get('input')(); },
    runTimer(delay) {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, 'expected timer ' + delay); timers.delete(entry[0]); return entry[1].callback();
    },
    decode(url) { const item = images.findLast(value => value.image.src === url); assert.ok(item, url); item.resolve(); },
    upload() { return node('portraitFile').listeners.get('change')({target: {files: [{type: 'image/png', size: 10}]}}); },
    click(id) { return node(id).listeners.get('click')(); },
  };
}

test('a newer portrait decoded during encoding is encoded next and replaces the stale result', async () => {
  const h = harness();
  h.decode(SAMPLE); await eventually(() => h.node('sourceCanvas').portrait === SAMPLE);
  const upload = h.upload(); // Decoding began before controls were disabled.
  const start = h.click('startButton');
  await eventually(() => h.encoded.length === 1);
  h.decode('blob:portrait-1'); await upload;
  assert.equal(h.encoded.length, 1, 'encodes must remain serialized');
  h.encoded[0].resolve();
  await eventually(() => h.encoded.length === 2);
  assert.equal(h.encoded[1].portrait, 'blob:portrait-1');
  h.encoded[1].resolve(); await start;
  await eventually(() => h.drawn.includes('blob:portrait-1'));
  assert.deepEqual(h.drawn, ['blob:portrait-1']);
  assert.equal(h.maximumEncodes, 1);
  assert.equal(h.node('motionControls').disabled, false);
  assert.equal(h.node('portraitFile').disabled, false);
});

test('superseded image decodes revoke only their own object URLs', async () => {
  const h = harness();
  const first = h.upload(), second = h.upload();
  h.decode('blob:portrait-2'); await second;
  h.decode('blob:portrait-1'); await first;
  assert.equal(h.node('previewSource').src, 'blob:portrait-2');
  assert.deepEqual(h.revoked, ['blob:portrait-1']);
  h.decode(SAMPLE); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.node('previewSource').src, 'blob:portrait-2', 'initial sample must not overwrite an upload');
  const sample = h.click('sampleButton'); h.decode(SAMPLE); await sample;
  assert.equal(h.node('previewSource').src, SAMPLE);
  assert.deepEqual(h.revoked, ['blob:portrait-1', 'blob:portrait-2']);
});

test('an obsolete render failure cannot stop a newer portrait or its successful frame', async () => {
  const h = harness({holdRenders: true});
  h.decode(SAMPLE); await eventually(() => h.node('sourceCanvas').portrait === SAMPLE);
  const upload = h.upload();
  const start = h.click('startButton');
  await eventually(() => h.encoded.length === 1);
  h.encoded[0].resolve(); await eventually(() => h.rendered.length === 1);
  h.decode('blob:portrait-1'); await upload;
  h.rendered[0].reject(new Error('obsolete source failed'));
  await eventually(() => h.encoded.length === 2);
  h.encoded[1].resolve(); await eventually(() => h.rendered.length === 2);
  h.rendered[1].resolve(h.rendered[1].frame); await start;
  assert.deepEqual(h.drawn, ['blob:portrait-1']);
  assert.equal(h.node('renderBadge').textContent, 'NEURAL OUTPUT');
  assert.equal(h.node('motionControls').disabled, false);
  assert.doesNotMatch(h.node('statusText').textContent, /obsolete|stopped/i);
});

test('provider changes update the label and a cached page restores through a fresh load', async () => {
  const h = harness();
  h.decode(SAMPLE); await eventually(() => h.node('sourceCanvas').portrait === SAMPLE);
  const start = h.click('startButton'); await eventually(() => h.encoded.length === 1);
  h.encoded[0].resolve(); await start;
  h.engines[0].options.onStatus({provider: 'wasm', message: 'CPU inference is ready.'});
  assert.match(h.node('runtimeNote').textContent, /CPU preview/);
  h.windowEvents.get('pagehide')();
  assert.equal(h.engines[0].disposed, true);
  h.windowEvents.get('pageshow')({persisted: false}); assert.equal(h.reloads, 0);
  h.windowEvents.get('pageshow')({persisted: true}); assert.equal(h.reloads, 1);
  const status = h.node('statusText').textContent;
  h.engines[0].options.onStatus({provider: 'webgpu', message: 'obsolete engine status'});
  assert.equal(h.node('statusText').textContent, status);
});

test('an avatar account sees its name and logout without owner navigation or automatic model loading', async () => {
  const h = harness({role: 'avatar'});
  await eventually(() => h.node('accountName').textContent === 'Mayur');
  assert.equal(h.node('workspaceLink').hidden, true);
  assert.equal(h.node('labBrand').href, '/avatar-lab');
  assert.equal(h.engines.length, 0);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
});

test('logout immediately unloads an active model and photo before sending the cookie logout', async () => {
  const h = harness({role: 'avatar'});
  const upload = h.upload(); h.decode('blob:portrait-1'); await upload;
  const start = h.click('startButton'); await eventually(() => h.encoded.length === 1);
  await h.click('logoutButton');
  assert.equal(h.engines[0].disposed, true);
  assert.equal(h.engines[0].disposeOptions.immediate, true);
  assert.deepEqual(h.revoked, ['blob:portrait-1']);
  assert.equal(h.node('sourceCanvas').width, 0);
  assert.equal(h.node('outputCanvas').width, 0);
  assert.equal(h.node('previewSource').src, undefined);
  assert.equal(h.node('portraitFile').disabled, true);
  assert.equal(h.node('startButton').disabled, true);
  const request = h.calls.find(call => call.url === '/logout');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.deepEqual(h.navigations, ['/?next=%2Favatar-lab']);
  assert.equal(h.announcements.length, 1);
  h.encoded[0].resolve(); await start;
  assert.deepEqual(h.drawn, [], 'a completed stale encode must never repaint after logout');
  await h.click('startButton');
  assert.equal(h.engines.length, 1, 'logout must not recreate the model');
});

test('failed logout keeps resources unloaded and exposes a real retry', async () => {
  const h = harness({logoutFailures: 1});
  await eventually(() => h.node('accountName').textContent === 'Owner');
  await h.click('logoutButton');
  assert.deepEqual(h.navigations, []);
  assert.deepEqual(h.announcements, []);
  assert.equal(h.node('logoutButton').disabled, false);
  assert.equal(h.node('logoutButton').textContent, 'Retry log out');
  assert.match(h.node('logoutMessage').textContent, /Couldn’t confirm log out/);
  assert.equal(h.node('startButton').disabled, true);
  await h.click('logoutButton');
  assert.deepEqual(h.navigations, ['/?next=%2Favatar-lab']);
  assert.equal(h.announcements.length, 1);
});

test('cross-tab logout stops the model with no owner actions or second logout request', async () => {
  const h = harness();
  h.decode(SAMPLE); await eventually(() => h.node('sourceCanvas').portrait === SAMPLE);
  const start = h.click('startButton'); await eventually(() => h.encoded.length === 1);
  h.encoded[0].resolve(); await start;
  h.windowEvents.get('storage')({key: 'reign:ax:signed-out', newValue: 'synthetic-event'});
  assert.equal(h.engines[0].disposed, true);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/me']);
  assert.deepEqual(h.navigations, ['/?next=%2Favatar-lab']);
});

test('an expired session releases the page and logout remains safe to request', async () => {
  const h = harness({sessionStatus: 401});
  await eventually(() => h.navigations.length === 1);
  assert.equal(h.node('startButton').disabled, true);
  assert.equal(h.engines.length, 0);
  await h.click('logoutButton');
  assert.equal(h.calls.filter(call => call.url === '/logout').length, 1);
  assert.equal(h.node('logoutButton').disabled, false);
});

test('logout revokes a photo that is still decoding without accepting it later', async () => {
  const h = harness();
  const upload = h.upload();
  await h.click('logoutButton');
  assert.deepEqual(h.revoked, ['blob:portrait-1']);
  h.decode('blob:portrait-1'); await upload;
  assert.deepEqual(h.revoked, ['blob:portrait-1']);
  assert.equal(h.node('previewSource').src, undefined);
  assert.equal(h.engines.length, 0);
});

async function readyWithHeldRenders(h) {
  h.decode(SAMPLE); await eventually(() => h.node('sourceCanvas').portrait === SAMPLE);
  const start = h.click('startButton');
  await eventually(() => h.encoded.length === 1);
  h.encoded[0].resolve(); await eventually(() => h.rendered.length === 1);
  h.rendered[0].resolve(h.rendered[0].frame); await start;
  assert.equal(h.node('frameStat').dataset.settled, 'true');
}

test('display interpolation advances on its own clock without adding neural frames', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.input('yaw', 5); const pose = h.runTimer(120);
  await eventually(() => h.rendered.length === 2);
  h.rendered[1].resolve(h.rendered[1].frame); await pose;
  assert.equal(h.compositors[0].appended[0].easing, 'smoothstep', 'manual poses ease into their destination');
  const stats = h.node('frameStat').dataset;
  assert.equal(stats.frames, '2');
  assert.equal(stats.displayFrames, '1');
  assert.equal(stats.transitioning, 'true');
  assert.equal(stats.settled, 'false');
  h.paint(100); assert.equal(stats.displayFrames, '1', 'no synthetic frame at the unchanged starting time');
  h.paint(140); assert.equal(stats.interpolatedFrames, '1');
  h.paint(150); assert.equal(stats.interpolatedFrames, '1', 'display clock stays bounded to 30 Hz');
  h.paint(180); assert.equal(stats.interpolatedFrames, '2');
  h.paint(225);
  assert.equal(stats.transitioning, 'false');
  assert.equal(stats.settled, 'true');
  assert.equal(stats.frames, '2', 'presentation frames must not count as model inference');
  assert.equal(h.rendered.length, 2);
  assert.deepEqual(h.presentations.map(frame => Math.round(frame.progress * 100)), [33, 67, 100]);
  assert.equal(h.animationFrames.size, 0, 'settled previews do not run an endless redraw loop');
});

test('slow inference advances bounded logical poses and holds only one next keyframe', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.setTime(10_000_000); h.click('motionButton');
  await eventually(() => h.rendered.length === 2);
  const firstPose = h.rendered[1].controls;
  assert.ok(Math.abs(firstPose.yaw) < .1, 'motion eases in rather than sampling a huge wall-clock phase');
  h.setTime(10_010_000); h.rendered[1].resolve({...h.rendered[1].frame, ms: 10_000});
  await eventually(() => h.rendered.length === 3);
  const secondPose = h.rendered[2].controls;
  assert.ok(Math.abs(secondPose.yaw - firstPose.yaw) <= 1.2);
  assert.ok(Math.abs(secondPose.pitch - firstPose.pitch) <= .8);
  assert.ok(Math.abs(secondPose.mouth - firstPose.mouth) <= .08);
  assert.equal(h.compositors[0].appended[0].durationMs, 10_500);
  assert.equal(h.compositors[0].appended[0].easing, 'linear', 'continuous motion must not stop at each neural keyframe');
  h.rendered[2].resolve({...h.rendered[2].frame, ms: 10_000});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.rendered.length, 3, 'only one new result may wait for the active transition');
  assert.equal(h.node('frameStat').dataset.frames, '2');
  assert.equal(h.maximumRenders, 1);
  h.paint(10_015_000);
  assert.ok(h.presentations.at(-1).progress > 0 && h.presentations.at(-1).progress < 1);
  h.click('motionButton'); // Pause invalidates the held automatic keyframe.
  await new Promise(resolve => setImmediate(resolve));
  h.paint(10_020_501);
  assert.equal(h.node('frameStat').dataset.frames, '2');
  assert.equal(h.node('frameStat').dataset.settled, 'true');
  assert.equal(h.rendered.length, 3);
  assert.equal(h.animationFrames.size, 0);
});

test('very slow inference has a finite transition and holds its exact endpoint without repeated draws', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.click('motionButton'); await eventually(() => h.rendered.length === 2);
  h.setTime(30_100); h.rendered[1].resolve({...h.rendered[1].frame, ms: 30_000});
  await eventually(() => h.rendered.length === 3);
  assert.equal(h.compositors[0].appended[0].durationMs, 15_000);
  h.paint(45_101);
  assert.equal(h.node('frameStat').dataset.transitioning, 'false');
  assert.equal(h.node('frameStat').dataset.frames, '2');
  assert.equal(h.animationFrames.size, 0);
  const displays = h.node('frameStat').dataset.displayFrames;
  h.paint(99_000);
  assert.equal(h.node('frameStat').dataset.displayFrames, displays);
  assert.match(h.node('statusText').textContent, /last frame stays visible/);
  h.click('motionButton');
  h.rendered[2].resolve(h.rendered[2].frame);
  await new Promise(resolve => setImmediate(resolve));
});

test('reset requested during inference discards the automatic pose and renders the new neutral target', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.click('motionButton'); await eventually(() => h.rendered.length === 2);
  h.click('resetButton');
  h.rendered[1].resolve(h.rendered[1].frame);
  await eventually(() => h.rendered.length === 3);
  assert.ok(Object.values(h.rendered[2].controls).every(value => value === 0));
  assert.equal(h.node('frameStat').dataset.frames, '1', 'cancelled motion result must not enter the compositor');
  h.rendered[2].resolve(h.rendered[2].frame);
  await eventually(() => h.node('frameStat').dataset.frames === '2');
  assert.equal(h.compositors[0].appended[0].easing, 'smoothstep', 'reset after automatic motion restores manual easing');
  h.paint(225); await eventually(() => h.node('frameStat').dataset.settled === 'true');
  assert.equal(h.rendered.length, 3);
  assert.equal(h.maximumRenders, 1);
});

test('a crop replaces the interpolation source and its first result is an exact fresh frame', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.input('yaw', 5); const pose = h.runTimer(120);
  await eventually(() => h.rendered.length === 2);
  h.rendered[1].resolve(h.rendered[1].frame); await pose;
  h.paint(140);
  const oldRevision = Number(h.node('frameStat').dataset.sourceRevision);
  h.input('cropZoom', 2); const crop = h.click('applyCrop');
  await eventually(() => h.encoded.length === 2);
  assert.equal(h.node('frameStat').dataset.transitioning, 'false');
  assert.equal(h.animationFrames.size, 0);
  assert.equal(h.node('frameStat').dataset.interpolatedFrames, '0');
  assert.ok(Number(h.node('frameStat').dataset.sourceRevision) > oldRevision);
  h.encoded[1].resolve(); await eventually(() => h.rendered.length === 3);
  h.rendered[2].resolve(h.rendered[2].frame); await crop;
  assert.equal(h.node('frameStat').dataset.frames, '1');
  assert.equal(h.node('frameStat').dataset.interpolatedFrames, '0');
  assert.equal(h.node('frameStat').dataset.settled, 'true');
  assert.equal(h.drawn.at(-1), SAMPLE + ':crop-256');
});

test('logout cancels display callbacks and a late model result cannot recreate them', async () => {
  const h = harness({holdRenders: true}); await readyWithHeldRenders(h);
  h.click('motionButton'); await eventually(() => h.rendered.length === 2);
  h.rendered[1].resolve(h.rendered[1].frame); await eventually(() => h.rendered.length === 3);
  h.paint(140);
  await h.click('logoutButton');
  assert.equal(h.node('frameStat').dataset.transitioning, 'false');
  assert.equal(h.animationFrames.size, 0);
  assert.equal(h.compositors[0].disposed, true);
  const count = h.presentations.length;
  h.rendered[2].resolve(h.rendered[2].frame);
  await new Promise(resolve => setImmediate(resolve)); h.paint(100_000);
  assert.equal(h.presentations.length, count);
  assert.equal(h.node('outputCanvas').width, 0);
  assert.equal(h.node('frameStat').dataset.frames, '0');
  assert.equal(h.animationFrames.size, 0);
});
