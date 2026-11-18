import { Wllama } from './wllama.js';

const byId = id => document.getElementById(id);
const bridge = window.ReignPhone;
let engine = null;
let loading = false;
let busy = false;
let paired = false;
let current = 0;

function controls() {
  byId('load').disabled = loading || Boolean(engine);
  byId('ask').disabled = !engine || busy;
  byId('askCore').disabled = !bridge || busy;
  byId('genesis').disabled = !bridge;
  byId('pair').disabled = !engine || !bridge || paired;
}
function answer(source, text, reusable = false) {
  byId('answer').parentElement.hidden = false;
  byId('source').textContent = source;
  byId('answer').textContent = text;
  byId('reuse').hidden = !reusable;
}
function heartbeat() {
  if (engine && paired && !document.hidden) bridge.heartbeat();
}
window.nodeEvent = (event, data) => {
  if (event === 'pair') {
    paired = data.paired;
    byId('connection').textContent = data.message;
    controls();
    if (paired) heartbeat();
  } else if (event === 'connection') {
    byId('connection').textContent = data.message;
  } else if (event === 'core' && data.id === current) {
    busy = false;
    answer('REIGN Core', data.message, data.success);
    controls();
  }
};
byId('load').onclick = async () => {
  loading = true;
  controls();
  byId('model').textContent = 'Loading the bundled model into memory…';
  let runtime, wasmUrl;
  try {
    const wasm = await fetch('wllama.wasm');
    if (!wasm.ok) throw Error();
    wasmUrl = URL.createObjectURL(await wasm.blob());
    runtime = new Wllama({'single-thread/wllama.wasm': wasmUrl}, {suppressNativeLog:true});
    const response = await fetch('model.gguf');
    if (!response.ok) throw Error();
    await runtime.loadModel([await response.blob()], {n_threads:1, n_ctx:1024, n_batch:64});
    engine = runtime;
    byId('model').textContent = 'Ready on this phone · Q4_K_M';
    heartbeat();
  } catch {
    if (runtime) await runtime.exit().catch(() => {});
    byId('model').textContent = 'Couldn’t load the model. Close other apps and retry.';
  } finally { if (wasmUrl) URL.revokeObjectURL(wasmUrl); loading = false; controls(); }
};
byId('ask').onclick = async () => {
  const text = byId('prompt').value.trim();
  if (!text || !engine || busy) return;
  busy = true; controls(); answer('On-device answer', 'Thinking…');
  try {
    const messages = [{role:'system', content:'Answer the user briefly and accurately.'}, {role:'user', content:text}];
    const template = await engine.formatChat(messages, true);
    if ((await engine.tokenize(template, true)).length > 850) {
      answer('On-device answer', 'Shorten the message to leave room for the answer.');
      return;
    }
    const reply = await engine.createCompletion(template, {nPredict:128, sampling:{temp:0}, onNewToken:(_t,_p,s) => { byId('answer').textContent = s; }});
    answer('On-device answer', reply.trim() || 'The model returned an empty answer.', Boolean(reply.trim()));
  } catch { answer('On-device answer', 'Generation stopped. Try a shorter message.'); }
  finally { busy = false; controls(); }
};
byId('askCore').onclick = () => {
  const text = byId('prompt').value.trim();
  if (!text || busy || !bridge) return;
  busy = true; controls(); answer('REIGN Core', 'Sending this message to Core…');
  bridge.askCore(++current, text);
};
byId('reuse').onclick = () => { byId('prompt').value = byId('answer').textContent; byId('prompt').focus(); };
byId('genesis').onclick = () => bridge.openGenesis();
byId('pair').onclick = () => { byId('pair').disabled = true; bridge.register(); };
document.addEventListener('visibilitychange', heartbeat);
setInterval(heartbeat, 30000);
controls();
if (bridge) bridge.status();
