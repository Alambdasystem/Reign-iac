import { createPortraitEngine } from '/assets/portrait-engine.js';
import { createMotionCompositor } from '/assets/portrait-motion.js';

const $ = (id) => document.getElementById(id);
const controls = ['yaw', 'pitch', 'roll', 'mouth', 'blink', 'smile'];
const sourceCanvas = $('sourceCanvas');
const outputCanvas = $('outputCanvas');
const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
const compositor = createMotionCompositor({canvas: outputCanvas});
let sourceImage, engine, renderTimer, objectUrl;
let ready = false, loading = false, rendering = false, pendingRender = false, motion = false, closed = false;
let engineInitialized = false, encoding = false, pendingEncode = false, encodePromise, renderPromise;
let sourceRevision = 0, sourceLoadRevision = 0, engineGeneration = 0, frames = 0;
let sessionVerified = false, checkingSession, loggingOut = false;
const SIGNOUT_KEY = 'reign:ax:signed-out';
const LOGIN_PATH = '/?next=%2Favatar-lab';
const pendingPhotoUrls = new Set();
const presentationWaiters = new Set();
let presentationFrame, presentationEpoch = 0, lastDisplayTime = -Infinity;
let transitioning = false, displayFrames = 0, interpolatedFrames = 0;
let poseRevision = 0, lastPose, motionOrigin, motionTime = 0, lastKeyframeMs = 120;

function status(message, state = '') {
  $('statusText').textContent = message;
  $('statusDot').className = `status-dot ${state}`;
}
function syncControls() {
  for (const name of controls) {
    const value = Number($(name).value);
    $(`${name}Value`).textContent = ['yaw', 'pitch', 'roll'].includes(name) ? `${Math.round(value * 10) / 10}°` : `${Math.round(value * 100)}%`;
  }
}
function readControls() { return Object.fromEntries(controls.map((name) => [name, Number($(name).value)])); }
function enableControls(enabled) {
  $('motionControls').disabled = !enabled;
  $('motionButton').disabled = !enabled;
  $('resetButton').disabled = !enabled;
  const sourceBusy = closed || loading || encoding;
  for (const name of ['portraitFile', 'sampleButton', 'cropZoom', 'cropX', 'cropY']) $(name).disabled = sourceBusy;
  $('applyCrop').disabled = !sourceImage || sourceBusy;
  updatePresentationState();
}
function updateRuntime(provider) {
  if (provider === 'webgpu') $('runtimeNote').textContent = 'WebGPU active · smooth transitions between neural poses · photos stay local.';
  else if (provider === 'wasm') $('runtimeNote').textContent = 'CPU preview · motion plays slowly while new poses are prepared. Chrome or Edge with WebGPU is faster.';
}
function stopMotion() {
  motion = false;
  pendingRender = false; poseRevision++;
  wakePresentationWaiters();
  $('motionButton').textContent = '▶ Play motion';
  $('motionButton').setAttribute('aria-pressed', 'false');
  updatePresentationState();
}
function wakePresentationWaiters() {
  for (const resolve of presentationWaiters) resolve();
  presentationWaiters.clear();
}
function updatePresentationState() {
  const stats = $('frameStat').dataset;
  stats.transitioning = String(transitioning);
  stats.displayFrames = String(displayFrames);
  stats.interpolatedFrames = String(interpolatedFrames);
  stats.sourceRevision = String(sourceRevision);
  stats.settled = String(ready && !closed && !loading && !encoding && !rendering && !pendingRender && !transitioning && !motion);
}
function resetPresentation() {
  presentationEpoch++;
  if (presentationFrame !== undefined) cancelAnimationFrame(presentationFrame);
  presentationFrame = undefined; transitioning = false; lastDisplayTime = -Infinity;
  compositor.reset(); lastPose = undefined;
  frames = 0; displayFrames = 0; interpolatedFrames = 0;
  $('frameStat').dataset.frames = '0';
  wakePresentationWaiters(); updatePresentationState();
}
function schedulePresentation() {
  if (presentationFrame !== undefined || closed || !transitioning) return;
  const epoch = presentationEpoch;
  presentationFrame = requestAnimationFrame((now) => {
    presentationFrame = undefined;
    if (closed || epoch !== presentationEpoch || !transitioning) return;
    // A steady display clock creates intermediate pixels without increasing
    // model calls. Limit this small CPU compositor to 30 presentations/second.
    if (now - lastDisplayTime >= 1000 / 30) {
      let result;
      try { result = compositor.draw(now); }
      catch (error) { fail(error); return; }
      if (result.painted) {
        displayFrames++;
        if (result.interpolated) interpolatedFrames++;
        lastDisplayTime = now;
      }
      transitioning = result.active;
      if (!transitioning) {
        wakePresentationWaiters();
        if (motion && rendering) status('Preparing the next pose. The last frame stays visible.', 'busy');
        else if (!motion && !rendering && ready) status('Pose ready. Try another expression or Play motion.');
      }
      updatePresentationState();
    }
    schedulePresentation();
  });
}
function nextMotionPose() {
  // Advance a bounded logical timeline, never the wall-clock time spent inside
  // inference. A slow CPU therefore produces smaller steps, not sudden jumps.
  const nextTime = motionTime + Math.max(.08, Math.min(.30, lastKeyframeMs / 1000));
  const t = nextTime, intro = Math.min(1, t / 1.2), ease = intro * intro * (3 - 2 * intro);
  const target = {yaw: Math.sin(t * .55) * 7, pitch: Math.sin(t * .37) * 2,
    roll: Math.sin(t * .43) * 2, mouth: (1 + Math.sin(t * 1.25)) * .15, smile: .2, blink: 0};
  const previous = lastPose || readControls(), origin = motionOrigin || previous;
  const maximum = {yaw: 1.2, pitch: .8, roll: .7, mouth: .08, smile: .08, blink: .12};
  const pose = {};
  for (const name of controls) {
    const desired = origin[name] + (target[name] - origin[name]) * ease;
    pose[name] = previous[name] + Math.max(-maximum[name], Math.min(maximum[name], desired - previous[name]));
  }
  return {pose, time: nextTime};
}
function drawCrop() {
  if (!sourceImage) return;
  const zoom = Number($('cropZoom').value), x = Number($('cropX').value), y = Number($('cropY').value);
  const width = sourceImage.naturalWidth, height = sourceImage.naturalHeight;
  const side = Math.min(width, height) / zoom;
  sourceContext.drawImage(sourceImage, (width - side) * (x + 1) / 2, (height - side) * (y + 1) / 2, side, side, 0, 0, 256, 256);
  $('cropZoomValue').textContent = `${zoom.toFixed(2)}×`;
  $('cropXValue').textContent = x.toFixed(2);
  $('cropYValue').textContent = y.toFixed(2);
}
async function loadSource(url, label, ownsUrl = false) {
  const loadRevision = ++sourceLoadRevision;
  if (ownsUrl) pendingPhotoUrls.add(url);
  let accepted = false;
  stopMotion();
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    if (loadRevision !== sourceLoadRevision || closed) return false;
    if (image.naturalWidth < 128 || image.naturalHeight < 128) throw new Error('Choose a photo at least 128 × 128 pixels.');
    if (image.naturalWidth * image.naturalHeight > 40_000_000) throw new Error('Choose a photo smaller than 40 megapixels.');
    sourceImage = image;
    sourceRevision++;
    resetPresentation();
    $('sourceCaption').textContent = label;
    $('cropZoom').value = '1'; $('cropX').value = '0'; $('cropY').value = '0';
    drawCrop();
    $('previewSource').src = url; $('previewSource').hidden = false; outputCanvas.hidden = true;
    // Transfer ownership at acceptance, before any asynchronous inference.
    const previousUrl = objectUrl;
    objectUrl = ownsUrl ? url : undefined;
    accepted = true;
    if (previousUrl && previousUrl !== url) URL.revokeObjectURL(previousUrl);
    enableControls(false);
    if (engineInitialized) void encodeSource();
    return true;
  } catch (error) {
    if (loadRevision !== sourceLoadRevision || closed) return false;
    throw error;
  } finally {
    if (ownsUrl && pendingPhotoUrls.delete(url) && !accepted) URL.revokeObjectURL(url);
  }
}
function encodeSource() {
  if (!engineInitialized || !engine || !sourceImage || closed) return Promise.resolve();
  stopMotion(); ready = false; pendingRender = false;
  sourceRevision++; pendingEncode = true;
  resetPresentation();
  if (encodePromise) return encodePromise;
  const generation = engineGeneration, activeEngine = engine;
  encoding = true;
  enableControls(false);
  encodePromise = (async () => {
    while (pendingEncode && !closed && generation === engineGeneration && engineInitialized) {
      pendingEncode = false;
      const revision = sourceRevision;
      $('portraitStage').setAttribute('aria-busy', 'true');
      $('renderingLabel').hidden = false;
      status('Learning this portrait’s appearance…', 'busy');
      try {
        // The engine snapshots the canvas at this call; another decoded source
        // becomes the next encode instead of changing an in-flight request.
        await activeEngine.encode(sourceCanvas);
        if (closed || generation !== engineGeneration || revision !== sourceRevision) continue;
        controls.forEach((name) => { $(name).value = '0'; });
        syncControls(); ready = true;
        await requestRender();
      } catch (error) {
        if (!closed && generation === engineGeneration && revision === sourceRevision) fail(error, generation);
      }
    }
  })().finally(() => {
    if (closed || generation !== engineGeneration) return;
    encoding = false; encodePromise = undefined;
    if (pendingEncode && engineInitialized) return encodeSource();
    enableControls(ready);
    if (!rendering) { $('renderingLabel').hidden = true; $('portraitStage').setAttribute('aria-busy', 'false'); }
    updatePresentationState();
  });
  return encodePromise;
}
function fail(error, generation = engineGeneration) {
  if (closed || generation !== engineGeneration) return;
  stopMotion(); ready = false; loading = false; engineInitialized = false;
  pendingEncode = false; pendingRender = false; enableControls(false);
  resetPresentation();
  $('loadProgress').hidden = true; $('renderingLabel').hidden = true;
  $('portraitStage').setAttribute('aria-busy', 'false');
  $('renderBadge').textContent = 'PREVIEW STOPPED'; $('renderBadge').className = 'render-badge';
  status(error?.message || 'The neural preview couldn’t start. Reload and try Chrome or Edge with graphics acceleration enabled.', 'error');
  $('stageOverlay').hidden = false;
  $('stageOverlay').querySelector('h3').textContent = 'Let’s try that again';
  $('stageOverlay').querySelector('p').textContent = 'The model couldn’t complete this preview. Your photo is still local.';
  $('startButton').disabled = false; $('startButton').textContent = 'Retry neural preview';
}
async function requestRender() {
  if (!ready || closed) return;
  pendingRender = true;
  updatePresentationState();
  if (rendering) return renderPromise;
  rendering = true;
  const generation = engineGeneration, activeEngine = engine;
  renderPromise = (async () => {
    while (pendingRender && ready && !closed && generation === engineGeneration) {
      pendingRender = false;
      const revision = sourceRevision, requestedPose = poseRevision;
      const moving = motion, next = moving ? nextMotionPose() : {pose: readControls()};
      const current = () => !closed && ready && generation === engineGeneration && revision === sourceRevision && requestedPose === poseRevision;
      $('renderingLabel').hidden = false; $('portraitStage').setAttribute('aria-busy', 'true');
      updatePresentationState();
      try {
        const started = performance.now();
        const frame = await activeEngine.render(next.pose);
        const elapsed = Math.max(0, performance.now() - started, Number(frame.ms) || 0);
        if (!current()) continue;
        // The compositor owns two real endpoints. Keep at most this one newer
        // result while the display finishes; never build a neural-frame queue.
        while (transitioning && current()) await new Promise(resolve => presentationWaiters.add(resolve));
        if (!current()) continue;
        const durationMs = Math.max(120, Math.min(15000, elapsed * 1.05));
        if (!lastPose) {
          compositor.reset(frame.imageData); displayFrames++;
        } else {
          // The automatic trajectory already eases in and follows a smooth
          // path. Avoid stopping its velocity at every small neural keyframe.
          if (!compositor.append(frame.imageData, {durationMs, now: performance.now(), easing: moving ? 'linear' : 'smoothstep'})) throw new Error('The motion preview could not accept the next pose. Please retry.');
          transitioning = true; lastDisplayTime = -Infinity; schedulePresentation();
        }
        lastPose = {...next.pose}; lastKeyframeMs = durationMs;
        if (moving) {
          motionTime = next.time;
          for (const name of controls) $(name).value = String(next.pose[name]);
          syncControls();
        }
        outputCanvas.hidden = false; $('previewSource').hidden = true; $('stageOverlay').hidden = true;
        frames++;
        $('renderBadge').textContent = 'NEURAL OUTPUT'; $('renderBadge').className = 'render-badge ready';
        $('frameStat').textContent = `${(frame.ms / 1000).toFixed(2)} s / neural frame`;
        $('frameStat').dataset.frames = String(frames); $('frameStat').dataset.provider = frame.provider;
        updateRuntime(frame.provider);
        status(motion ? 'Motion is flowing between rendered poses. Adjust a control to pause.' : 'The portrait is moving smoothly to your pose.');
        if (!transitioning && !motion) status('Neural portrait ready. Try a small turn, a smile, or Play motion.');
        // Infer the next small pose while the independent display clock moves
        // toward this one. Slow devices stretch playback, up to the finite cap.
        pendingRender = motion && moving && current();
        updatePresentationState();
      } catch (error) {
        if (current()) fail(error, generation);
      }
    }
  })().finally(() => {
    if (closed || generation !== engineGeneration) return;
    rendering = false; renderPromise = undefined;
    if (!encoding) { $('renderingLabel').hidden = true; $('portraitStage').setAttribute('aria-busy', 'false'); }
    updatePresentationState();
    if (pendingRender && ready) return requestRender();
  });
  return renderPromise;
}
async function start() {
  if (loading || encoding || closed || !sessionVerified) return;
  const generation = ++engineGeneration, previousEngine = engine;
  engine = undefined; engineInitialized = false; sourceRevision++;
  pendingEncode = false; encodePromise = undefined; pendingRender = false; renderPromise = undefined; rendering = false;
  resetPresentation();
  loading = true; ready = false; enableControls(false);
  $('startButton').disabled = true; $('startButton').textContent = 'Loading neural model…';
  $('loadProgress').hidden = false; $('loadProgress').removeAttribute('value');
  $('renderBadge').textContent = 'LOADING MODEL'; status('Starting the portrait renderer…', 'busy');
  try {
    if (previousEngine) await previousEngine.dispose().catch(() => {});
    if (closed || generation !== engineGeneration) return;
    engine = createPortraitEngine({ onStatus(event) {
      if (closed || generation !== engineGeneration) return;
      updateRuntime(event.provider);
      status(event.message, 'busy');
      if (typeof event.progress === 'number') $('loadProgress').value = event.progress;
    }});
    await engine.init();
    if (closed || generation !== engineGeneration) return;
    engineInitialized = true; updateRuntime(engine.provider);
    $('loadProgress').hidden = true; loading = false;
    if (!sourceImage) throw new Error('The source photo is still loading. Please try again.');
    await encodeSource();
  } catch (error) { fail(error, generation); }
}
$('startButton').addEventListener('click', start);
$('motionButton').addEventListener('click', () => {
  if (motion) { stopMotion(); status('Motion paused. The current transition will settle.'); return; }
  if (!ready || closed) return;
  poseRevision++; motionTime = 0; motionOrigin = {...(lastPose || readControls())};
  motion = true; $('motionButton').textContent = 'Ⅱ Pause motion'; $('motionButton').setAttribute('aria-pressed', 'true');
  status('Preparing the next pose for smooth motion…', 'busy');
  requestRender();
});
for (const name of controls) $(name).addEventListener('input', () => {
  stopMotion(); syncControls(); clearTimeout(renderTimer); renderTimer = setTimeout(requestRender, 120);
});
$('resetButton').addEventListener('click', () => {
  stopMotion(); controls.forEach((name) => { $(name).value = '0'; }); syncControls(); requestRender();
});
document.querySelectorAll('[data-preset]').forEach((button) => button.addEventListener('click', () => {
  stopMotion(); controls.forEach((name) => { $(name).value = '0'; });
  const presets = { smile: { smile: .5 }, turn: { yaw: 9, roll: 2 }, mouth: { mouth: .6 }, blink: { blink: .8 } };
  for (const [name, value] of Object.entries(presets[button.dataset.preset])) $(name).value = String(value);
  syncControls(); requestRender();
}));
for (const name of ['cropZoom', 'cropX', 'cropY']) $(name).addEventListener('input', () => {
  stopMotion(); drawCrop(); status('Crop adjusted. Select Apply crop to update the neural portrait.');
});
$('applyCrop').addEventListener('click', async () => {
  if (engineInitialized) await encodeSource(); else status('Crop set. Start neural preview to animate this portrait.');
});
$('portraitFile').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 15 * 1024 * 1024) {
    status('Choose a JPEG, PNG, or WebP image smaller than 15 MB.', 'error'); return;
  }
  const newUrl = URL.createObjectURL(file);
  try {
    if (await loadSource(newUrl, 'Your portrait · local to this tab', true)) $('cropDetails').open = true;
  } catch (error) { status(error.message || 'That image couldn’t be read. Try another photo.', 'error'); }
});
$('sampleButton').addEventListener('click', async () => {
  try {
    if (await loadSource('/avatar-assets/sample-portrait.png', 'Sample portrait · AI generated')) $('portraitFile').value = '';
  } catch (error) { status(error.message || 'The sample portrait couldn’t be loaded.', 'error'); }
});
function releasePortrait() {
  if (closed) return;
  closed = true; sessionVerified = false;
  sourceLoadRevision++; sourceRevision++; engineGeneration++;
  ready = false; engineInitialized = false; pendingEncode = false; pendingRender = false;
  stopMotion(); clearTimeout(renderTimer);
  resetPresentation(); compositor.dispose();
  const activeEngine = engine;
  engine = undefined; encodePromise = undefined; renderPromise = undefined;
  activeEngine?.dispose({immediate: true}).catch(() => {});
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  for (const url of pendingPhotoUrls) URL.revokeObjectURL(url);
  pendingPhotoUrls.clear();
  objectUrl = undefined;
  if (sourceImage) sourceImage.src = '';
  sourceImage = undefined;
  $('portraitFile').value = '';
  $('previewSource').removeAttribute('src'); $('previewSource').hidden = true;
  // Drop the backing buffers as well as the decoded photo and worker tensors.
  sourceCanvas.width = sourceCanvas.height = outputCanvas.width = outputCanvas.height = 0;
  outputCanvas.hidden = true;
  enableControls(false); $('startButton').disabled = true;
  $('loadProgress').hidden = true; $('renderingLabel').hidden = true;
  $('portraitStage').setAttribute('aria-busy', 'false');
}
function showAuthMessage(message) {
  $('logoutMessage').textContent = message;
  $('logoutMessage').hidden = !message;
}
function announceSignout() {
  // This notification contains no account data or credentials.
  try { localStorage.setItem(SIGNOUT_KEY, `${Date.now()}:${Math.random()}`); } catch { /* Storage may be unavailable. */ }
}
async function logout() {
  if (loggingOut) return;
  loggingOut = true; $('logoutButton').disabled = true;
  releasePortrait();
  showAuthMessage('Logging out… Preview unloaded.');
  status('Preview unloaded. Logging out…');
  const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 15000);
  try {
    const token = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith('ax_csrf='))?.slice(8) || '';
    const response = await fetch('/logout', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'X-CSRF-Token': token}, signal: abort.signal});
    if (!response.ok) throw new Error('Logout was not confirmed.');
    announceSignout();
    window.location.replace(LOGIN_PATH);
  } catch {
    showAuthMessage('Couldn’t confirm log out. The preview is unloaded. Check your connection and try Log out again.');
    status('Preview unloaded. Log out still needs confirmation.', 'error');
    $('logoutButton').textContent = 'Retry log out';
  } finally {
    clearTimeout(timeout); loggingOut = false; $('logoutButton').disabled = false;
  }
}
function checkSession() {
  if (closed || checkingSession) return checkingSession;
  const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 15000);
  checkingSession = (async () => {
    try {
      const response = await fetch('/api/me', {credentials: 'same-origin', cache: 'no-store', signal: abort.signal});
      if (closed) return;
      if (response.status === 401) { releasePortrait(); window.location.replace(LOGIN_PATH); return; }
      if (!response.ok) throw new Error('Account check failed.');
      const profile = await response.json();
      if (closed) return;
      if (!profile.authenticated || !['owner', 'avatar'].includes(profile.role)) throw new Error('Account check failed.');
      sessionVerified = true;
      $('accountName').textContent = profile.display_name || profile.username;
      $('accountName').hidden = false;
      $('workspaceLink').hidden = profile.role !== 'owner';
      $('labBrand').href = profile.role === 'owner' ? '/' : '/avatar-lab';
      $('labBrand').setAttribute('aria-label', profile.role === 'owner' ? 'Back to REIGN workspace' : 'REIGN Avatar Lab');
      if (!loading && !engineInitialized) $('startButton').disabled = false;
      showAuthMessage('');
    } catch {
      if (!closed) showAuthMessage('Couldn’t check your sign-in. Check your connection and return to this tab to retry, or use Log out.');
    } finally { clearTimeout(timeout); checkingSession = undefined; }
  })();
  return checkingSession;
}
$('logoutButton').addEventListener('click', logout);
window.addEventListener('storage', (event) => {
  if (event.key !== SIGNOUT_KEY || !event.newValue) return;
  releasePortrait(); window.location.replace(LOGIN_PATH);
});
window.addEventListener('focus', checkSession);
window.addEventListener('pagehide', releasePortrait);
window.addEventListener('pageshow', (event) => {
  if (event.persisted && closed) window.location.reload();
});
updatePresentationState();
checkSession();
loadSource('/avatar-assets/sample-portrait.png', 'Sample portrait · AI generated').catch(() => {
  status('The sample portrait couldn’t load. Choose a photo to continue.', 'error');
});
