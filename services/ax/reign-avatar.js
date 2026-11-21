/* REIGN's shared, lightweight portrait. These are precomputed neural frames,
 * blended from actual output audio energy; this is not phoneme prediction or
 * live inference. One decoded public atlas is shared by instances on a page.
 * Audio playback and permission handling stay with the calling voice player. */
export const REIGN_ATLAS_URL = '/assets/team-avatar-assets/portrait-atlas.webp?v=e78062581a93b6f1b2e1fdb077f56b48c7c5004a39ee576e2c39cb5ae96b3197';
const EXPRESSIONS = ['neutral', 'warm', 'thoughtful', 'concerned'];
const SIZE = 256;
let atlasPromise = null;

export function preloadReignAvatar() {
  if (!atlasPromise) {
    const image = new Image();
    image.decoding = 'async'; image.src = REIGN_ATLAS_URL;
    let timeout;
    const deadline = new Promise((_, reject) => {
      timeout = setTimeout(() => reject(Error('Portrait loading timed out.')), 20000);
    });
    atlasPromise = Promise.race([image.decode(), deadline]).then(() => {
      if (image.naturalWidth !== 768 || image.naturalHeight !== 4096) throw Error('Portrait version unavailable.');
      return image;
    }).catch(() => {
      atlasPromise = null;
      throw Error('REIGN’s face could not load. Reload to try again.');
    }).finally(() => clearTimeout(timeout));
  }
  return atlasPromise;
}

export function mixAxis(value, minimum, maximum, steps) {
  const scaled = Math.max(0, Math.min(steps - 1, (value - minimum) / (maximum - minimum) * (steps - 1)));
  const low = Math.min(steps - 2, Math.floor(scaled));
  return [[low, 1 - (scaled - low)], [low + 1, scaled - low]];
}

export function speechEnvelope(samples) {
  if (!samples?.length) return 0;
  let sum = 0;
  for (const value of samples) sum += value * value;
  return Math.min(.9, Math.max(0, Math.sqrt(sum / samples.length) - .008) * 5.5);
}

export function createReignAvatar({canvas, onStatus = () => {}, getSpeechLevel = null}) {
  const ctx = canvas.getContext('2d', {alpha: false});
  let atlas = null, loaded = false, dead = false, speaking = false, level = 0, mouth = 0;
  let animation = null, lastPaint = -Infinity, lastTime = 0, paints = 0;
  let expression = 0, previousExpression = 0, expressionChanged = 0;
  canvas.width = canvas.height = SIZE;
  function diagnostics(state) {
    canvas.dataset.ready = String(loaded && !dead);
    if (state) canvas.dataset.state = state;
    canvas.dataset.speaking = String(speaking && !dead);
    canvas.dataset.mouth = mouth.toFixed(3);
    canvas.dataset.expression = EXPRESSIONS[expression];
    canvas.dataset.mode = 'precomputed-neural';
    canvas.dataset.frames = String(paints);
  }
  function status(state, message) {
    diagnostics(state);
    if (!dead) onStatus({state, message});
  }
  function schedule() {
    if (!dead && loaded && animation === null && !document.hidden) animation = requestAnimationFrame(paint);
  }
  function stamp(exp, row, column, weight, crop = null) {
    if (weight <= .0001) return;
    ctx.globalAlpha = weight;
    const y = (exp * 4 + row) * SIZE;
    if (crop) ctx.drawImage(atlas, column * SIZE, y + crop.top, SIZE, crop.height, 0, crop.top, SIZE, crop.height);
    else ctx.drawImage(atlas, column * SIZE, y, SIZE, SIZE, 0, 0, SIZE, SIZE);
  }
  function paint(now, initial = false) {
    animation = null;
    if (dead || !loaded || (document.hidden && !initial)) return;
    if (now - lastPaint < 1000 / 30) { schedule(); return; }
    const dt = Math.min(100, Math.max(0, now - (lastTime || now)));
    lastTime = lastPaint = now;
    let desired = 0;
    if (speaking) {
      const sampled = getSpeechLevel ? getSpeechLevel() : level;
      desired = Number.isFinite(sampled) ? Math.min(.9, Math.max(0, sampled)) : 0;
    }
    mouth += (desired - mouth) * (1 - Math.exp(-dt / (desired > mouth ? 35 : 90)));
    const yaw = Math.sin(now / 2200) * 1.65;
    const transition = Math.min(1, Math.max(0, (now - expressionChanged) / 260));
    const expressions = previousExpression === expression ? [[expression, 1]] :
      [[previousExpression, 1 - transition], [expression, transition]];
    const yawWeights = mixAxis(yaw, -3, 3, 3), mouthWeights = mixAxis(mouth, 0, .9, 3);
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.globalCompositeOperation = 'lighter';
    for (const [exp, ew] of expressions) for (const [col, yw] of yawWeights) for (const [row, mw] of mouthWeights)
      stamp(exp, row, col, ew * yw * mw);
    ctx.globalCompositeOperation = 'source-over';
    const phase = now % 4700, blink = phase < 180 ? Math.sin(phase / 180 * Math.PI) : 0;
    if (blink > .001) {
      const dominant = transition < .5 ? previousExpression : expression;
      for (const [col, weight] of yawWeights) stamp(dominant, 3, col, weight * blink, {top: 66, height: 54});
    }
    ctx.globalAlpha = 1; paints++; diagnostics(); schedule();
  }
  function setSpeaking(value) {
    if (dead) return;
    speaking = value === true;
    // Barge-in, end and logout close the mouth immediately, even if the next
    // browser animation frame is suspended. Never retain a previous speaker.
    if (!speaking) { level = 0; mouth = 0; }
    diagnostics(); schedule();
  }
  function setSpeechLevel(value) {
    if (dead) return;
    level = speaking && Number.isFinite(value) ? Math.min(.9, Math.max(0, value)) : 0;
  }
  function setExpression(value) {
    if (dead) return;
    const next = Math.max(0, EXPRESSIONS.indexOf(value));
    if (next === expression) return;
    previousExpression = expression; expression = next; expressionChanged = performance.now();
    diagnostics(); schedule();
  }
  function stop() { setSpeaking(false); setExpression('neutral'); }
  function visibility() {
    if (document.hidden && animation !== null) { cancelAnimationFrame(animation); animation = null; }
    else { lastTime = performance.now(); schedule(); }
  }
  function destroy() {
    if (dead) return;
    stop(); dead = true; loaded = false; atlas = null;
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    document.removeEventListener('visibilitychange', visibility);
    canvas.width = canvas.height = 0;
    diagnostics('destroyed');
  }
  document.addEventListener('visibilitychange', visibility);
  status('loading', 'REIGN is getting ready…');
  const ready = preloadReignAvatar().then(image => {
    if (dead) return false;
    atlas = image; loaded = true;
    // Draw before exposing ready so an opaque, still-empty canvas never hides
    // the matching CSS poster. The first paint needs no microphone or audio.
    paint(performance.now(), true);
    status('ready', 'REIGN is ready.');
    return true;
  }).catch(() => {
    if (!dead) { loaded = false; status('error', 'REIGN’s face could not load. Reload to try again.'); }
    return false;
  });
  return {ready, setSpeaking, setSpeechLevel, setExpression, stop, destroy};
}

// The private interface remains a classic script; expose the same renderer
// there without a second implementation or an additional audio player.
if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
  window.REIGNAvatar = {createReignAvatar, preloadReignAvatar, REIGN_ATLAS_URL};
  window.dispatchEvent(new Event('reign:avatar-module-ready'));
}
