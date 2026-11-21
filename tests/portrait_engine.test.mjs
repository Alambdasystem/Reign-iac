import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../services/ax/portrait-engine.js', import.meta.url), 'utf8');
const {portraitControls} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('untrusted controls stay finite and within the model pose range', () => {
  const {pose, expression} = portraitControls({yaw: 1000, pitch: -1000, roll: NaN, mouth: Infinity, blink: -1, smile: 'invalid'});
  assert.deepEqual([...pose], [0.75, -1, 0]);
  assert.ok([...expression].every(value => value === 0));
  const neutral = portraitControls();
  assert.ok([...neutral.pose, ...neutral.expression].every(value => value === 0));
});

test('a full blink stays at the empirically safe latent strength', () => {
  const {expression} = portraitControls({blink: 1});
  assert.equal(Math.max(...expression.map(Math.abs)), 0.25);
  assert.equal(expression.filter(value => value !== 0).length, 4);
});

// These tests exercise lifecycle/failover against simulated ORT boundaries.
// Separate real-model browser and CPU probes establish rendering behavior.
function workerHarness({webgpu = false, failGpuRender = false, failEncodingAt = 0} = {}) {
  const tasks = new Map();
  const tensors = [];
  const sessions = [];
  const downloads = [];
  const activeInputs = new Set();
  const statuses = [];
  let nextId = 0;
  let activeRuns = 0;
  let maxActiveRuns = 0;
  let encodingRuns = 0;
  let renderFailure = failGpuRender;
  let lastSource = null;

  class Tensor {
    constructor(type, data, dims) {
      Object.assign(this, {type, data, dims, disposed: false});
      tensors.push(this);
    }
    async getData() { assert.equal(this.disposed, false); return this.data; }
    dispose() {
      assert.equal(activeInputs.has(this), false, 'must not dispose an in-flight input');
      assert.equal(this.disposed, false, 'must not dispose a tensor twice');
      this.disposed = true;
    }
  }

  const ort = {
    env: {wasm: {}}, Tensor,
    InferenceSession: {
      async create(buffer, options) {
        const name = buffer[0] === 1 ? 'appearance' : 'distilled';
        const provider = options.executionProviders[0];
        const session = {
          name, provider, released: false,
          async run(feeds) {
            assert.equal(session.released, false);
            activeRuns += 1;
            maxActiveRuns = Math.max(maxActiveRuns, activeRuns);
            for (const tensor of Object.values(feeds)) { assert.equal(tensor.disposed, false); activeInputs.add(tensor); }
            try {
              await new Promise(resolve => setImmediate(resolve));
              if (name === 'appearance') {
                encodingRuns += 1;
                if (encodingRuns === failEncodingAt) throw new Error('Synthetic encoder failure');
                return {output: new Tensor('float32', new Float32Array(32 * 16 * 64 * 64), [1, 32, 16, 64, 64])};
              }
              if (provider === 'webgpu' && renderFailure) { renderFailure = false; throw new Error('Synthetic GPU failure'); }
              lastSource = feeds.input_rgb.data[0];
              return {rgb: new Tensor('float32', new Float32Array(3 * 256 * 256).fill(0.5), [1, 3, 256, 256])};
            } finally {
              for (const tensor of Object.values(feeds)) activeInputs.delete(tensor);
              activeRuns -= 1;
            }
          },
          async release() { assert.equal(activeRuns, 0, 'release follows active inference'); session.released = true; },
        };
        sessions.push(session);
        return session;
      },
    },
  };
  const context = vm.createContext({
    Float32Array, Uint8Array, Uint8ClampedArray, performance,
    navigator: webgpu ? {gpu: {}} : {},
    __loadRuntime: async () => ort,
    async fetch(url, options) {
      downloads.push({url, options});
      return {ok: true, async arrayBuffer() { return new Uint8Array([url.includes('appearance') ? 1 : 2]).buffer; }};
    },
    postMessage(message) {
      if (message.kind === 'status') { statuses.push(message); return; }
      const task = tasks.get(message.id);
      tasks.delete(message.id);
      if (message.error) task.reject(new Error(message.error));
      else task.resolve(message.result);
    },
  });
  // Only replace the ES module loader boundary; execute the production worker
  // queue, tensor handling, provider failover, and conversion unchanged.
  const runnable = source.replace(/^export /gm, '')
    .replace('import.meta.url', '"https://example.test/assets/portrait-engine.js"')
    .replace('await import(RUNTIME_URL)', 'await __loadRuntime()');
  vm.runInContext(`${runnable}\ninstallInferenceWorker();`, context);
  return {
    sessions, tensors, downloads, statuses, ort,
    get maxActiveRuns() { return maxActiveRuns; },
    get lastSource() { return lastSource; },
    send(operation, payload = {}) {
      const id = ++nextId;
      const promise = new Promise((resolve, reject) => tasks.set(id, {resolve, reject}));
      context.onmessage({data: {id, operation, ...payload}});
      return promise;
    },
  };
}

const portrait = value => new Float32Array(3 * 256 * 256).fill(value);

test('queued inference, source replacement and disposal never overlap or leak tensors', async () => {
  const h = workerHarness();
  await h.send('init');
  const operations = [
    h.send('encode', {rgb: portrait(0.1)}),
    h.send('render', {controls: {mouth: 0.6}}),
    h.send('encode', {rgb: portrait(0.2)}),
    h.send('render', {controls: {blink: 1}}),
    h.send('dispose'),
  ];
  const results = await Promise.all(operations);
  assert.equal(h.maxActiveRuns, 1);
  assert.equal(results[1].pixels.length, 256 * 256 * 4);
  assert.equal(results[1].pixels[3], 255);
  assert.equal(results[3].provider, 'wasm');
  assert.ok(Math.abs(h.lastSource - 0.2) < 1e-6);
  assert.equal(h.ort.env.wasm.numThreads, 1);
  assert.equal(h.ort.env.wasm.proxy, false);
  assert.ok(h.tensors.every(tensor => tensor.disposed));
  assert.ok(h.sessions.every(session => session.released));
  await assert.rejects(h.send('render'), /disposed/);
});

test('a failed GPU run retries the same portrait on CPU without downloading again', async () => {
  const h = workerHarness({webgpu: true, failGpuRender: true});
  assert.equal((await h.send('init')).provider, 'webgpu');
  await h.send('encode', {rgb: portrait(0.35)});
  const frame = await h.send('render', {controls: {yaw: 7}});
  assert.equal(frame.provider, 'wasm');
  assert.ok(Math.abs(h.lastSource - 0.35) < 1e-6);
  assert.equal(h.downloads.length, 2);
  assert.ok(h.downloads.every(({url, options}) => url.startsWith('/avatar-assets/models/') && !options.body));
  assert.equal(h.sessions.length, 4);
  assert.ok(h.sessions.slice(0, 2).every(session => session.released));
  assert.ok(h.statuses.some(status => status.phase === 'fallback'));
  await h.send('dispose');
  assert.ok(h.tensors.every(tensor => tensor.disposed));
});

test('an encoding failure preserves the previous portrait and frees failed inputs', async () => {
  const h = workerHarness({failEncodingAt: 2});
  await h.send('encode', {rgb: portrait(0.4)});
  await assert.rejects(h.send('encode', {rgb: portrait(0.8)}), /encoder failure/);
  await h.send('render', {controls: {}});
  assert.ok(Math.abs(h.lastSource - 0.4) < 1e-6);
  await h.send('dispose');
  assert.ok(h.tensors.every(tensor => tensor.disposed));
});

test('immediate disposal terminates a stalled worker and rejects its pending work', async () => {
  const workers = [];
  class Worker {
    constructor() { this.messages = []; this.terminated = 0; workers.push(this); }
    postMessage(message) { this.messages.push(message); }
    terminate() { this.terminated++; }
  }
  const context = vm.createContext({Worker, URL, setTimeout, clearTimeout});
  const runnable = source.replace(/^export /gm, '')
    .replace('import.meta.url', '"https://example.test/assets/portrait-engine.js"');
  vm.runInContext(runnable + '\nglobalThis.engine = createPortraitEngine();', context);
  const init = context.engine.init();
  const rejected = assert.rejects(init, /disposed/);
  await context.engine.dispose({immediate: true});
  await rejected;
  assert.equal(workers.length, 1);
  assert.equal(workers[0].terminated, 1);
  assert.deepEqual(workers[0].messages.map(message => message.operation), ['init']);
  await assert.rejects(context.engine.init(), /disposed/);
  await context.engine.dispose({immediate: true});
  assert.equal(workers[0].terminated, 1, 'repeated logout cleanup is idempotent');
});
