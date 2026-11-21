'use strict';

// Execute the actual page controller with controlled image decoding and neural
// worker promises. These tests cover UI ordering, not model quality or inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../services/ax/avatar-lab.js'), 'utf8')
  .replace(/^import[^\n]*\n/, '');
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
  let urlId = 0, reloads = 0, activeEncodes = 0, maximumEncodes = 0;
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  node('sourceCanvas').getContext = () => ({drawImage(image) { node('sourceCanvas').portrait = image.src; }});
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
      render() {
        const frame = {imageData: {portrait: engine.portrait}, ms: 12, provider: engine.provider};
        if (!holdRenders) { rendered.push({frame}); return Promise.resolve(frame); }
        const pending = deferred(); rendered.push({frame, ...pending}); return pending.promise;
      },
      async dispose(options) { engine.disposed = true; engine.disposeOptions = options; },
    };
    engines.push(engine); return engine;
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
    createPortraitEngine, performance: {now: () => 100}, setTimeout: () => 1, clearTimeout() {}, console,
  });
  vm.runInContext(source, context, {filename: 'services/ax/avatar-lab.js'});
  return {
    node, images, engines, encoded, rendered, drawn, revoked, windowEvents, calls, navigations, announcements,
    get maximumEncodes() { return maximumEncodes; }, get reloads() { return reloads; },
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
