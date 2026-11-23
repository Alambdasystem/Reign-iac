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

// Concentrate interpolation toward the nearest genuine neural frame. A
// four-way average of slightly different eyes/teeth is visibly blurry.
export function crispAxis(weights, exponent = 1.8) {
  const adjusted = weights.map(([index, weight]) => [index, Math.pow(Math.max(0, weight), exponent)]);
  const sum = adjusted.reduce((total, [, weight]) => total + weight, 0);
  return adjusted.map(([index, weight]) => [index, sum ? weight / sum : 0]);
}

// Speech-controlled yaw uses the actual output clock. It holds while playback
// is suspended and stays within the compositor's gentle yaw range.
export function speechHeadYaw({active = false, level = 0, clockMs = null, now = 0} = {}) {
  if (!active) return Math.sin(now / 2600) * .55;
  const time = Number.isFinite(clockMs) && clockMs >= 0 ? clockMs : now;
  const energy = Number.isFinite(level) ? Math.max(0, Math.min(.9, level)) : 0;
  const turn = (Math.sin(time / 1050) * 2.05 + Math.sin(time / 2450 + .75) * .9);
  return Math.max(-3, Math.min(3, turn * (.45 + .55 * energy / .9)));
}

// Compute the position of the *source mouth* after object-fit:cover crops a
// centered square. A fixed percent of the screen instead targets the nose in
// wide fullscreen mode and causes a visibly blurry oval.
export function mouthOverlayGeometry(width, height) {
  if (!(Number.isFinite(width) && Number.isFinite(height)) || width <= 0 || height <= 0) return null;
  const cover = Math.max(width, height);
  return {
    centerY: height / 2 + cover * .11,  // mouth near source-image y=.61
    radiusX: cover * .115,            // include the full lips and jaw opening
    radiusY: cover * .052
  };
}

export function speechEnvelope(samples) {
  if (!samples?.length) return 0;
  let sum = 0;
  for (const value of samples) sum += value * value;
  return Math.min(.9, Math.max(0, Math.sqrt(sum / samples.length) - .008) * 5.5);
}

// The team portrait is a square source. Inverse-warp the face and the
// neural eye/lip samples together, with zero displacement at the shoulders,
// background edges, and frame boundary.
const headVertex = `
attribute vec2 position;
varying vec2 portraitUV;
void main() {
  portraitUV = vec2(position.x, -position.y) * .5 + .5;
  gl_Position = vec4(position, 0.0, 1.0);
}`;
const headFragment = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 portraitUV;
uniform sampler2D poster;
uniform sampler2D face;
uniform vec3 pose;
uniform float speaking;

float feather(vec2 p, vec2 center, vec2 radius, float inner) {
  return 1.0 - smoothstep(inner, 1.0, length((p - center) / radius));
}

void main() {
  vec2 p = portraitUV;
  float halfWidth = mix(.28, .41, smoothstep(.04, .31, p.y));
  float head = 1.0 - smoothstep(halfWidth - .12, halfWidth, abs(p.x - .5));
  head *= smoothstep(0.0, .10, p.y) * (1.0 - smoothstep(.72, .84, p.y));
  vec2 neck = p - vec2(.5, .79);
  vec2 shift = vec2(pose.x * (.004 + (.48 - p.y) * .008), pose.y / 256.0);
  shift += pose.z * vec2(-neck.y, neck.x);
  vec2 uv = p - shift * head;
  float eyes = feather(uv, vec2(.5, 93.0 / 256.0), vec2(.245, .095), .65);
  float lips = feather(uv, vec2(.5, .61), vec2(.115, .052), .55) * speaking;
  gl_FragColor = mix(texture2D(poster, uv), texture2D(face, uv), max(eyes, lips));
}`;

function createLocalizedHead(source) {
  if (!source.parentElement?.classList?.contains('reign-portrait')) return null;
  const layer = document.createElement('canvas');
  const gl = layer.getContext('webgl', {alpha: false, antialias: false, depth: false});
  if (!gl) return null;
  const program = gl.createProgram();
  const buffer = gl.createBuffer();
  const textures = [gl.createTexture(), gl.createTexture()];
  const shaders = [];
  const image = new Image();
  const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
  let pose, speaking, ready = false, disposed = false;

  function destroy() {
    if (disposed) return;
    disposed = true;
    image.onload = image.onerror = null;
    layer.removeEventListener('webglcontextlost', destroy);
    layer.remove();
    gl.deleteProgram(program);
    gl.deleteBuffer(buffer);
    textures.forEach(texture => gl.deleteTexture(texture));
    shaders.forEach(shader => gl.deleteShader(shader));
  }

  try {
    for (const [type, code] of [[gl.VERTEX_SHADER, headVertex], [gl.FRAGMENT_SHADER, headFragment]]) {
      const shader = gl.createShader(type);
      shaders.push(shader);
      gl.shaderSource(shader, code);
      gl.compileShader(shader);
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error('Head compositor unavailable');
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    pose = gl.getUniformLocation(program, 'pose');
    speaking = gl.getUniformLocation(program, 'speaking');
    for (const [index, name] of ['poster', 'face'].entries()) {
      gl.activeTexture(gl.TEXTURE0 + index);
      gl.bindTexture(gl.TEXTURE_2D, textures[index]);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.uniform1i(gl.getUniformLocation(program, name), index);
    }
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, SIZE, SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  } catch {
    destroy();
    return null;
  }

  layer.className = 'reign-head-motion';
  layer.hidden = true;
  layer.setAttribute('aria-hidden', 'true');
  layer.addEventListener('webglcontextlost', destroy, {once: true});
  source.after(layer);
  image.onload = () => {
    gl.activeTexture(gl.TEXTURE0);
    try {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      if (gl.getError() !== gl.NO_ERROR) throw Error('Poster texture unavailable');
      ready = true;
    } catch {
      destroy();
    }
  };
  image.onerror = destroy;
  image.src = '/assets/team-avatar-assets/idle-portrait.png';
  return {
    paint(yaw, nod, tilt, active, width, height) {
      if (!ready || disposed || !width || !height) return;
      const size = Math.min(image.naturalWidth,
        Math.max(384, Math.ceil(Math.max(width, height) * (globalThis.devicePixelRatio || 1) / 64) * 64));
      if (layer.width !== size || layer.height !== size) {
        layer.width = layer.height = size;
        gl.viewport(0, 0, size, size);
      }
      gl.activeTexture(gl.TEXTURE1);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, source);
      if (reducedMotion?.matches) gl.uniform3f(pose, 0, 0, 0);
      else gl.uniform3f(pose, yaw, nod, tilt * Math.PI / 180);
      gl.uniform1f(speaking, active ? 1 : 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      layer.hidden = false;
    },
    destroy,
  };
}

export function createReignAvatar({canvas, onStatus = () => {}, getSpeechLevel = null, getPlaybackState = null}) {
  const ctx = canvas.getContext('2d', {alpha: false});
  let head = null;
  let atlas = null, loaded = false, dead = false, speaking = false, level = 0, mouth = 0;
  let animation = null, lastPaint = -Infinity, lastTime = 0, paints = 0;
  let expression = 0, previousExpression = 0, expressionChanged = 0;
  let playbackClock = null, lastPlaybackClock = null, expressionClock = false;
  let expressionSource = 'neutral', segment = 0, expressionProgress = 1, headYaw = 0;
  let maskWidth = -1, maskHeight = -1, maskReady = false;
  function layoutMouthMask() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 0;
    const height = canvas.clientHeight || canvas.parentElement?.clientHeight || 0;
    if (width === maskWidth && height === maskHeight) return;
    maskWidth = width; maskHeight = height;
    const layout = mouthOverlayGeometry(width, height);
    maskReady = !!layout && !!canvas.style?.setProperty;
    if (!maskReady) return;
    canvas.style.setProperty('--reign-mouth-center-y', layout.centerY.toFixed(2) + 'px');
    canvas.style.setProperty('--reign-mouth-rx', layout.radiusX.toFixed(2) + 'px');
    canvas.style.setProperty('--reign-mouth-ry', layout.radiusY.toFixed(2) + 'px');
    // Blink crop is source y=66..120 in the 256px atlas. Map its center
    // through object-fit:cover so both eyes remain visible in call mode.
    const cover = Math.max(width, height);
    canvas.style.setProperty('--reign-eyes-center-y', (height / 2 + cover * (93 / 256 - .5)).toFixed(2) + 'px');
    canvas.style.setProperty('--reign-eyes-rx', (cover * .245).toFixed(2) + 'px');
    canvas.style.setProperty('--reign-eyes-ry', (cover * .095).toFixed(2) + 'px');
  }
  let listeningLevel = 0, listeningUpdatedAt = -Infinity, headNod = 0, headTilt = 0;
  canvas.width = canvas.height = SIZE;
  function diagnostics(state) {
    canvas.dataset.ready = String(loaded && !dead);
    if (state) canvas.dataset.state = state;
    canvas.dataset.speaking = String(speaking && !dead);
    canvas.dataset.mouth = mouth.toFixed(3);
    canvas.dataset.yaw = headYaw.toFixed(3);
    canvas.dataset.nod = headNod.toFixed(3);
    canvas.dataset.tilt = headTilt.toFixed(3);
    canvas.dataset.present = String(loaded && !dead);
    canvas.dataset.expression = EXPRESSIONS[expression];
    canvas.dataset.expressionSource = expressionSource;
    canvas.dataset.expressionProgress = expressionProgress.toFixed(3);
    canvas.dataset.audioTime = (Number.isFinite(playbackClock) ? playbackClock / 1000 : 0).toFixed(3);
    canvas.dataset.segment = String(segment);
    canvas.dataset.mode = 'precomputed-neural';
    canvas.dataset.composite = 'sharp-original-eyes-and-mouth';
    canvas.dataset.maskReady = String(maskReady);
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
    layoutMouthMask();
    if (now - lastPaint < 1000 / 30) { schedule(); return; }
    if (getPlaybackState) {
      try { setPlaybackState(getPlaybackState() || {active: false}, false); }
      catch { setPlaybackState({active: false}, false); }
    }
    const dt = playbackClock === null ? Math.min(100, Math.max(0, now - (lastTime || now))) :
      Math.min(100, Math.max(0, playbackClock - (lastPlaybackClock ?? playbackClock)));
    lastPlaybackClock = playbackClock;
    lastTime = lastPaint = now;
    let desired = 0;
    if (speaking) {
      const sampled = getSpeechLevel ? getSpeechLevel() : level;
      desired = Number.isFinite(sampled) ? Math.min(.9, Math.max(0, sampled)) : 0;
    }
    // Stronger but bounded jaw motion. Audio silence still settles to a closed
    // mouth, and stop/barged-in playback closes it synchronously.
    const articulated = desired > .02 ? Math.min(.9, desired * 1.27 + .045) : 0;
    mouth += (articulated - mouth) * (1 - Math.exp(-dt / (articulated > mouth ? 28 : 76)));
    headYaw = speaking ? speechHeadYaw({active: true, level: desired, clockMs: playbackClock, now}) : 0;
    // Small acknowledgement nods follow *incoming* speech activity while
    // REIGN listens. These are display-only 2D gestures, not invented 3D
    // rotations or an assertion that REIGN understood untranscribed words.
    const recentlyListening = !speaking && now - listeningUpdatedAt < 240;
    const listening = recentlyListening ? Math.min(1, listeningLevel * 2.2) : 0;
    headNod = speaking ? Math.sin((playbackClock ?? now) / 270) * Math.min(2.4, desired * 2.7)
      : listening > .05 ? Math.sin(now / 390) * listening * 2.2 : 0;
    headTilt = speaking ? Math.sin((playbackClock ?? now) / 1600) * 1.8
      : listening > .05 ? Math.sin(now / 850) * listening * 1.6 : 0;
    const expressionTime = expressionClock && playbackClock !== null ? playbackClock : now;
    const transition = Math.min(1, Math.max(0, (expressionTime - expressionChanged) / 260));
    expressionProgress = transition;
    const expressions = previousExpression === expression ? [[expression, 1]] :
      [[previousExpression, 1 - transition], [expression, transition]];
    // The full-resolution original owns the head. Center-column neural
    // mouth frames prevent blurred sideways motion inside the mouth mask.
    // Wider neural head turns remain blocked by the image-quality gate.
    const yawWeights = [[1, 1]];
    const mouthWeights = crispAxis(mixAxis(mouth, 0, .9, 3));
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
    ctx.globalAlpha = 1;
    head?.paint(headYaw, headNod, headTilt, speaking, maskWidth, maskHeight);
    paints++; diagnostics(); schedule();
  }
  function setListeningLevel(value) {
    if (dead) return;
    const amplitude = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
    listeningLevel = amplitude;
    listeningUpdatedAt = performance.now();
    schedule();
  }
  function setSpeaking(value) {
    if (dead) return;
    speaking = value === true;
    // Barge-in, end and logout close the mouth immediately, even if the next
    // browser animation frame is suspended. Never retain a previous speaker.
    if (!speaking) { level = 0; mouth = 0; headYaw = 0; }
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
    expressionClock = false; expressionSource = next === 0 ? 'neutral' : 'manual';
    diagnostics(); schedule();
  }
  function setPlaybackState(value = {}, redraw = true) {
    if (dead) return;
    speaking = value.active === true;
    if (!speaking && value.expression === 'neutral') {
      level = mouth = headYaw = 0; expression = previousExpression = 0; expressionChanged = 0;
      playbackClock = lastPlaybackClock = null; expressionClock = false;
      expressionSource = 'neutral'; expressionProgress = 1; segment = 0;
      diagnostics(); if (redraw) schedule(); return;
    }
    level = speaking && Number.isFinite(value.level) ? Math.min(.9, Math.max(0, value.level)) : 0;
    if (!speaking) { mouth = 0; headYaw = 0; }
    playbackClock = Number.isFinite(value.clockMs) && value.clockMs >= 0 ? value.clockMs : null;
    segment = Number.isInteger(value.segment) && value.segment >= 1 && value.segment <= 4 ? value.segment : 0;
    // Expressions enter on the verified audio cue, never on a server response
    // or a fetch completing. A suspended clock cannot advance this blend.
    if (speaking && EXPRESSIONS.includes(value.expression)) {
      const next = EXPRESSIONS.indexOf(value.expression);
      const timed = playbackClock !== null && Number.isFinite(value.cueMs) && value.cueMs >= 0;
      if (next !== expression || expressionClock !== timed) {
        previousExpression = expression; expression = next;
        expressionChanged = timed ? value.cueMs : performance.now();
        expressionClock = timed;
      }
      expressionSource = value.expressionSource === 'response-segment' ? 'response-segment' : 'neutral';
    }
    diagnostics(); if (redraw) schedule();
  }
  function stop() {
    listeningLevel = 0; listeningUpdatedAt = -Infinity; headNod = headTilt = 0;
    setPlaybackState({active: false, expression: 'neutral'});
  }
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
    head?.destroy(); head = null;
    canvas.width = canvas.height = 0;
    diagnostics('destroyed');
  }
  document.addEventListener('visibilitychange', visibility);
  status('loading', 'REIGN is getting ready…');
  const ready = preloadReignAvatar().then(image => {
    if (dead) return false;
    atlas = image; loaded = true;
    // An optional WebGL path must never prevent the existing blink/lip
    // overlay or audio from functioning on a device without it.
    try { head = createLocalizedHead(canvas); } catch { head = null; }
    // Draw before exposing ready so an opaque, still-empty canvas never hides
    // the matching CSS poster. The first paint needs no microphone or audio.
    paint(performance.now(), true);
    status('ready', 'REIGN is ready.');
    return true;
  }).catch(() => {
    if (!dead) { loaded = false; status('error', 'REIGN’s face could not load. Reload to try again.'); }
    return false;
  });
  return {ready, setSpeaking, setSpeechLevel, setListeningLevel, setExpression, setPlaybackState, stop, destroy};
}

// The private interface remains a classic script; expose the same renderer
// there without a second implementation or an additional audio player.
if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
  window.REIGNAvatar = {createReignAvatar, preloadReignAvatar, REIGN_ATLAS_URL};
  window.dispatchEvent(new Event('reign:avatar-module-ready'));
}
