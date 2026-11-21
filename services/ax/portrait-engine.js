/*
 * Neural portrait renderer. All inference and portrait pixels stay on the device.
 *
 * Model: slperez/LP-Distilled0.1-ONNX, with the LivePortrait appearance encoder.
 * Expression mappings adapted from Stephen Perez's MIT-licensed lp-distilled-js:
 * https://github.com/stephenperez/lp-distilled-js
 * No face detector, face-landmark model, or synthetic mesh is used here.
 * See the model notices shipped with the Avatar Lab for model provenance.
 */

export const PORTRAIT_SIZE = 256;
export const CONTROL_LIMITS = Object.freeze({yaw: 15, pitch: 14, roll: 14});

const PIXELS = PORTRAIT_SIZE * PORTRAIT_SIZE;
const RUNTIME_URL = '/avatar-assets/ort/ort.webgpu.min.mjs';
const WASM_PATH = '/avatar-assets/ort/';
const MODEL_URLS = Object.freeze({
  appearance: '/avatar-assets/models/appearance.onnx',
  distilled: '/avatar-assets/models/distilled.onnx',
});
const MODEL_BYTES = Object.freeze({appearance: 3355896, distilled: 74573121});
const TOTAL_MODEL_BYTES = MODEL_BYTES.appearance + MODEL_BYTES.distilled;

// This order is part of the pretrained model's input contract.
const EXPRESSION_NAMES = [
  'kp1.x', 'kp1.y', 'kp1.z', 'kp2.x', 'kp2.y', 'kp2.z', 'kp3.y',
  'kp5.x', 'kp5.y', 'kp5.z', 'kp6.y', 'kp6.z', 'kp11.x', 'kp11.y',
  'kp13.y', 'kp14.x', 'kp14.y', 'kp15.x', 'kp15.y', 'kp16.x',
  'kp16.y', 'kp17.x', 'kp17.y', 'kp17.z', 'kp19.x', 'kp19.y',
  'kp19.z', 'kp20.x', 'kp20.y', 'kp20.z',
];

function bounded(value, minimum, maximum) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(minimum, Math.min(maximum, number)) : 0;
}

/** Convert bounded UI controls to the actual pretrained model's latent controls. */
export function portraitControls({yaw = 0, pitch = 0, roll = 0, mouth = 0, blink = 0, smile = 0} = {}) {
  const pose = new Float32Array([
    bounded(yaw, -CONTROL_LIMITS.yaw, CONTROL_LIMITS.yaw) / 20,
    bounded(pitch, -CONTROL_LIMITS.pitch, CONTROL_LIMITS.pitch) / 14,
    bounded(roll, -CONTROL_LIMITS.roll, CONTROL_LIMITS.roll) / 14,
  ]);
  const expression = new Float32Array(EXPRESSION_NAMES.length);
  const add = (name, value) => {
    const index = EXPRESSION_NAMES.indexOf(name);
    expression[index] = bounded(expression[index] + value, -1, 1);
  };
  const mouthAmount = bounded(mouth, 0, 1);
  // AA viseme: values are already normalized to the model's +/-0.08 delta range.
  add('kp19.y', 0.4375 * mouthAmount);
  add('kp20.y', -0.2875 * mouthAmount);
  add('kp17.z', 0.1125 * mouthAmount);
  add('kp14.y', -0.0625 * mouthAmount);

  // The author's animation peaks at 0.25; a normalized delta of 1 produces
  // out-of-distribution eye artifacts instead of a stronger blink.
  const blinkAmount = bounded(blink, 0, 1) * 0.25;
  add('kp11.y', blinkAmount);
  add('kp13.y', -0.7 * blinkAmount);
  add('kp15.y', blinkAmount);
  add('kp16.y', -0.7 * blinkAmount);

  const smileAmount = bounded(smile, 0, 1);
  // Blend the upstream happy preset, including its mild cheek and brow motion.
  add('kp14.y', -0.260475 * smileAmount);
  add('kp17.z', -0.1134375 * smileAmount);
  add('kp11.y', 0.0364 * smileAmount);
  add('kp13.y', -0.0105 * smileAmount);
  add('kp15.y', 0.0364 * smileAmount);
  add('kp16.y', -0.0105 * smileAmount);
  add('kp2.y', -0.0306 * smileAmount);
  return {pose, expression};
}

function providerLabel(provider) {
  if (provider === 'webgpu') return 'WebGPU on your device';
  if (provider === 'wasm') return 'CPU on your device (slow)';
  return 'Not loaded';
}

function rgbaToChw(rgba) {
  const rgb = new Float32Array(PIXELS * 3);
  for (let i = 0; i < PIXELS; i += 1) {
    // Composite transparent source pixels over white rather than black.
    const alpha = rgba[i * 4 + 3] / 255;
    for (let channel = 0; channel < 3; channel += 1) {
      rgb[channel * PIXELS + i] = (rgba[i * 4 + channel] / 255) * alpha + 1 - alpha;
    }
  }
  return rgb;
}

/**
 * Create a worker-backed engine. Await each render before scheduling the next
 * animation frame; slow devices must not accumulate an unbounded frame queue.
 *
 * init() -> engine
 * encode(canvas256) -> {width, height, provider}
 * render({yaw,pitch,roll,mouth,blink,smile}) -> {imageData,width,height,ms,provider}
 * dispose({immediate?: boolean}) -> Promise<void>
 *
 * onStatus receives {phase,message,provider,runtimeLabel,progress?}; download
 * progress is 0..100, measured from bytes received across both models.
 * encode() accepts only
 * a manually aligned 256px square canvas. The application owns cropping and UI.
 */
export function createPortraitEngine({onStatus = () => {}} = {}) {
  let worker = null;
  let nextId = 0;
  let provider = null;
  let disposed = false;
  let initPromise = null;
  let disposePromise = null;
  const pending = new Map();

  function failPending(error) {
    for (const task of pending.values()) task.reject(error);
    pending.clear();
  }

  function startWorker() {
    if (worker) return;
    if (typeof Worker !== 'function') throw new Error('This browser does not support the portrait inference worker.');
    worker = new Worker(new URL(import.meta.url), {type: 'module', name: 'reign-neural-portrait'});
    worker.onmessage = ({data}) => {
      if (data.kind === 'status') {
        if (data.provider) provider = data.provider;
        try { onStatus({...data, runtimeLabel: providerLabel(provider)}); } catch { /* UI callbacks do not interrupt inference. */ }
        return;
      }
      const task = pending.get(data.id);
      if (!task) return;
      pending.delete(data.id);
      if (data.error) task.reject(new Error(data.error));
      else {
        if (data.result?.provider) provider = data.result.provider;
        task.resolve(data.result);
      }
    };
    worker.onerror = () => {
      failPending(new Error('The portrait worker stopped. Reload the lab and try again.'));
      worker?.terminate();
      worker = null;
      initPromise = null;
      provider = null;
    };
    worker.onmessageerror = () => {
      failPending(new Error('The portrait worker returned an unreadable result.'));
    };
  }

  function request(operation, payload = {}, transfer = []) {
    if (disposed && operation !== 'dispose') return Promise.reject(new Error('The portrait engine has been disposed.'));
    startWorker();
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, {resolve, reject});
      try { worker.postMessage({id, operation, ...payload}, transfer); }
      catch (error) { pending.delete(id); reject(error); }
    });
  }

  const engine = {
    get provider() { return provider; },
    get runtimeLabel() { return providerLabel(provider); },
    async init() {
      if (disposed) throw new Error('The portrait engine has been disposed.');
      if (!initPromise) initPromise = request('init').catch(error => { initPromise = null; throw error; });
      await initPromise;
      return engine;
    },
    async encode(canvas256) {
      if (canvas256?.width !== PORTRAIT_SIZE || canvas256?.height !== PORTRAIT_SIZE) {
        throw new Error('Align the portrait in a 256 by 256 pixel square before encoding.');
      }
      const context = canvas256.getContext('2d', {willReadFrequently: true});
      if (!context) throw new Error('This browser could not read the portrait canvas.');
      // Snapshot now so subsequent crop adjustments do not change this request.
      const rgb = rgbaToChw(context.getImageData(0, 0, PORTRAIT_SIZE, PORTRAIT_SIZE).data);
      await engine.init();
      return request('encode', {rgb}, [rgb.buffer]);
    },
    async render(controls = {}) {
      await engine.init();
      const result = await request('render', {controls});
      const imageData = new ImageData(result.pixels, PORTRAIT_SIZE, PORTRAIT_SIZE);
      return {imageData, width: PORTRAIT_SIZE, height: PORTRAIT_SIZE, ms: result.ms, provider: result.provider};
    },
    dispose({immediate = false} = {}) {
      if (immediate) {
        // Logout/unmount must stop active inference now, without waiting behind
        // the worker queue. Terminating its realm also releases model memory.
        disposed = true;
        worker?.terminate();
        worker = null;
        provider = null;
        initPromise = null;
        failPending(new Error('The portrait engine has been disposed.'));
        disposePromise = Promise.resolve();
        return disposePromise;
      }
      if (disposePromise) return disposePromise;
      disposed = true;
      if (!worker) { disposePromise = Promise.resolve(); return disposePromise; }
      disposePromise = (async () => {
        // Orderly release follows active inference. Terminating a stuck worker
        // releases its whole realm without disposing a tensor during a run.
        let timeout;
        try {
          await Promise.race([
            request('dispose'),
            new Promise(resolve => { timeout = setTimeout(resolve, 15000); }),
          ]);
        } catch {
          // A crashed worker is already unusable; termination below still frees
          // the realm, and cleanup must not create an unhandled UI rejection.
        } finally {
          clearTimeout(timeout);
          worker?.terminate();
          worker = null;
          provider = null;
          failPending(new Error('The portrait engine has been disposed.'));
        }
      })();
      return disposePromise;
    },
  };
  return engine;
}

// The same module is the dedicated worker entry point. No blob scripts, external
// CDN code, or shared-memory/cross-origin-isolation requirements are introduced.
if (typeof WorkerGlobalScope !== 'undefined' && globalThis instanceof WorkerGlobalScope) {
  installInferenceWorker();
}

function installInferenceWorker() {
  let ort;
  let provider = null;
  let sessions = {};
  let sourceTensor = null;
  let featureTensor = null;
  let queue = Promise.resolve();
  let initialized = false;
  let disposed = false;
  const buffers = new Map();

  function status(phase, message, details = {}) {
    globalThis.postMessage({kind: 'status', phase, message, provider, ...details});
  }

  function disposeTensor(tensor) {
    if (tensor && typeof tensor.dispose === 'function') tensor.dispose();
  }

  function disposeOutputs(outputs) {
    for (const tensor of new Set(Object.values(outputs || {}))) disposeTensor(tensor);
  }

  async function releaseSessions() {
    const oldSessions = Object.values(sessions);
    sessions = {};
    for (const session of oldSessions) {
      try { await session.release(); } catch { /* Continue releasing the other session. */ }
    }
  }

  async function modelBuffer(name) {
    if (!buffers.has(name)) {
      const message = name === 'appearance' ? 'Downloading the portrait encoder…' : 'Downloading the neural renderer (71 MB)…';
      const completed = [...buffers.values()].reduce((sum, buffer) => sum + buffer.byteLength, 0);
      let previousPercent = -1;
      const reportBytes = received => {
        const progress = Math.min(100, Math.floor((completed + received) * 100 / TOTAL_MODEL_BYTES));
        if (progress !== previousPercent) {
          previousPercent = progress;
          status('download', message, {progress, receivedBytes: completed + received, totalBytes: TOTAL_MODEL_BYTES});
        }
      };
      reportBytes(0);
      const response = await fetch(MODEL_URLS[name], {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`The ${name} model could not be downloaded (HTTP ${response.status}).`);
      if (response.body?.getReader) {
        // Preallocate the pinned file size instead of retaining a second full
        // model in a list of chunks. Progress reflects actual response bytes.
        const bytes = new Uint8Array(MODEL_BYTES[name]);
        const reader = response.body.getReader();
        let received = 0;
        try {
          while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            if (received + value.byteLength > bytes.byteLength) {
              await reader.cancel();
              throw new Error('The portrait model download does not match this version of the lab.');
            }
            bytes.set(value, received);
            received += value.byteLength;
            reportBytes(received);
          }
        } finally { reader.releaseLock(); }
        if (received !== bytes.byteLength) throw new Error('The portrait model download was incomplete. Please try again.');
        buffers.set(name, bytes);
      } else {
        const bytes = new Uint8Array(await response.arrayBuffer());
        buffers.set(name, bytes);
        reportBytes(bytes.byteLength);
      }
    }
    return buffers.get(name);
  }

  async function createSessions(nextProvider) {
    await releaseSessions();
    try {
      for (const name of ['appearance', 'distilled']) {
        status('compile', `Preparing the ${name === 'appearance' ? 'portrait encoder' : 'neural renderer'}…`);
        sessions[name] = await ort.InferenceSession.create(await modelBuffer(name), {
          executionProviders: [nextProvider],
          graphOptimizationLevel: 'disabled',
        });
      }
      provider = nextProvider;
    } catch (error) {
      await releaseSessions();
      throw error;
    }
  }

  async function fallbackToCpu() {
    status('fallback', 'WebGPU could not run this model. Switching to CPU; each frame may take several seconds.');
    await createSessions('wasm');
    status('ready', 'CPU inference is ready. Single frames may take several seconds.');
  }

  async function initialize() {
    if (initialized) return {provider};
    status('runtime', 'Loading the on-device neural runtime…');
    ort = await import(RUNTIME_URL);
    ort.env.wasm.wasmPaths = WASM_PATH;
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.proxy = false;
    // Cache the model bytes once. Runtime failures can retry on CPU without
    // downloading a second copy or sending the portrait to a server.
    await modelBuffer('appearance');
    await modelBuffer('distilled');
    if (globalThis.navigator?.gpu) {
      try { await createSessions('webgpu'); }
      catch { await fallbackToCpu(); }
    } else {
      status('fallback', 'WebGPU is unavailable. Loading CPU inference; each frame may take several seconds.');
      await createSessions('wasm');
    }
    initialized = true;
    status('ready', provider === 'webgpu' ? 'WebGPU inference is ready.' : 'CPU inference is ready. Single frames may take several seconds.');
    return {provider};
  }

  async function run(name, feeds) {
    try { return await sessions[name].run(feeds); }
    catch (error) {
      if (provider !== 'webgpu') throw error;
      await fallbackToCpu();
      return sessions[name].run(feeds);
    }
  }

  async function tensorData(tensor, size, shape) {
    if (!tensor || tensor.type !== 'float32' || tensor.dims.join(',') !== shape) {
      throw new Error('The portrait model returned an unexpected tensor.');
    }
    const data = typeof tensor.getData === 'function' ? await tensor.getData() : tensor.data;
    if (data.length !== size) throw new Error('The portrait model returned an incomplete tensor.');
    return data;
  }

  async function encode(rgb) {
    if (!(rgb instanceof Float32Array) || rgb.length !== PIXELS * 3) throw new Error('The portrait crop is invalid.');
    await initialize();
    status('encode', 'Encoding the portrait’s appearance…');
    let newSource = new ort.Tensor('float32', rgb, [1, 3, PORTRAIT_SIZE, PORTRAIT_SIZE]);
    let newFeature = null;
    let outputs;
    try {
      outputs = await run('appearance', {img: newSource});
      const values = await tensorData(outputs.output, 32 * 16 * 64 * 64, '1,32,16,64,64');
      newFeature = new ort.Tensor('float32', Float32Array.from(values), [1, 32, 16, 64, 64]);
      disposeTensor(sourceTensor);
      disposeTensor(featureTensor);
      sourceTensor = newSource;
      featureTensor = newFeature;
      newSource = null;
      newFeature = null;
      status('encoded', 'Portrait ready. Changes are rendered by the neural model on this device.');
      return {width: PORTRAIT_SIZE, height: PORTRAIT_SIZE, provider};
    } finally {
      disposeOutputs(outputs);
      disposeTensor(newSource);
      disposeTensor(newFeature);
    }
  }

  async function render(controls) {
    if (!sourceTensor || !featureTensor) throw new Error('Load and align a portrait before rendering.');
    const start = performance.now();
    const {pose, expression} = portraitControls(controls);
    const poseTensor = new ort.Tensor('float32', pose, [1, 3]);
    const expressionTensor = new ort.Tensor('float32', expression, [1, EXPRESSION_NAMES.length]);
    let outputs;
    try {
      outputs = await run('distilled', {
        feature_3d: featureTensor,
        pose_control_vector: poseTensor,
        input_rgb: sourceTensor,
        expression_control_vector: expressionTensor,
      });
      const rgb = await tensorData(outputs.rgb, PIXELS * 3, '1,3,256,256');
      const pixels = new Uint8ClampedArray(PIXELS * 4);
      for (let i = 0; i < PIXELS; i += 1) {
        for (let channel = 0; channel < 3; channel += 1) {
          const value = rgb[channel * PIXELS + i];
          if (!Number.isFinite(value)) throw new Error('The neural renderer returned invalid pixels.');
          pixels[i * 4 + channel] = Math.round(bounded(value, 0, 1) * 255);
        }
        pixels[i * 4 + 3] = 255;
      }
      return {pixels, ms: performance.now() - start, provider};
    } finally {
      disposeOutputs(outputs);
      disposeTensor(poseTensor);
      disposeTensor(expressionTensor);
    }
  }

  async function dispose() {
    disposed = true;
    disposeTensor(sourceTensor);
    disposeTensor(featureTensor);
    sourceTensor = null;
    featureTensor = null;
    await releaseSessions();
    buffers.clear();
    initialized = false;
    return {};
  }

  globalThis.onmessage = ({data}) => {
    // Encode, render, provider failover, and disposal cannot overlap. This also
    // prevents a replaced source tensor from being disposed during inference.
    queue = queue.catch(() => {}).then(async () => {
      try {
        if (disposed) throw new Error('The portrait engine has been disposed.');
        let result;
        if (data.operation === 'init') result = await initialize();
        else if (data.operation === 'encode') result = await encode(data.rgb);
        else if (data.operation === 'render') result = await render(data.controls);
        else if (data.operation === 'dispose') result = await dispose();
        else throw new Error('Unknown portrait operation.');
        globalThis.postMessage({id: data.id, result}, result.pixels ? [result.pixels.buffer] : []);
      } catch (error) {
        globalThis.postMessage({id: data.id, error: error.message || 'Portrait inference failed.'});
      }
    });
  };
}
