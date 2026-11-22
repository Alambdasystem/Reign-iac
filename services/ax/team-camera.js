/* A private, explicit camera preview. This module never uploads, records audio,
 * joins a call, or owns a shared call's tracks. */
export function createTeamCamera({video, onState = () => {}, mediaDevices = globalThis.navigator?.mediaDevices,
  document: doc = globalThis.document, timers = globalThis} = {}) {
  let stream = null, epoch = 0, disposed = false, starting = null, capturing = false, cancelReady = null;
  let phase = 'idle';
  const stopped = () => Object.assign(Error('Camera preview stopped.'), {name: 'AbortError'});
  function emit(next, message) {
    phase = next;
    video.dataset.state = next;
    onState({phase: next, active: next === 'ready', message});
  }
  function retire(value) {
    for (const track of value?.getTracks() || []) {
      track.onended = null;
      try { track.stop(); } catch { /* Already ended. */ }
    }
  }
  function stop(message = 'Camera stopped. Nothing is being shared.') {
    epoch++; starting = null; capturing = false;
    cancelReady?.(); cancelReady = null;
    retire(stream); stream = null;
    video.pause(); video.srcObject = null;
    emit('idle', message);
  }
  function start() {
    if (disposed) return Promise.resolve(false);
    if (phase === 'ready') return Promise.resolve(true);
    if (starting) return starting;
    const revision = ++epoch;
    const current = () => !disposed && revision === epoch;
    emit('requesting', 'Allow your camera to open a private preview. Your microphone stays off.');
    const task = (async () => {
      let acquired;
      try {
        if (!mediaDevices?.getUserMedia) throw Error('This browser cannot open a camera. Try a supported browser over HTTPS.');
        // Start the permission request directly within the Camera gesture.
        acquired = await mediaDevices.getUserMedia({audio: false,
          video: {facingMode: 'user', width: {ideal: 960}, height: {ideal: 540}}});
        if (!current()) { retire(acquired); return false; }
        const track = acquired.getVideoTracks()[0];
        if (!track || track.readyState === 'ended') throw Error('No active camera was found.');
        for (const audio of acquired.getAudioTracks()) audio.stop();
        stream = acquired;
        track.onended = () => { if (current()) stop('The camera disconnected. Open Camera to try again.'); };
        video.muted = true; video.playsInline = true; video.autoplay = true;
        await new Promise((resolve, reject) => {
          let timer, finished = false;
          const finish = error => {
            if (finished) return;
            finished = true;
            timers.clearTimeout(timer); video.removeEventListener('loadeddata', ready); video.removeEventListener('error', failed);
            cancelReady = null; error ? reject(error) : resolve();
          };
          const ready = () => { if (video.readyState >= 2 && video.videoWidth && video.videoHeight) finish(); };
          const failed = () => finish(Error('The camera preview could not play. Close it and try again.'));
          cancelReady = () => finish(stopped());
          video.addEventListener('loadeddata', ready); video.addEventListener('error', failed);
          timer = timers.setTimeout(() => finish(Error('The camera has no current frame. Close it and try again.')), 8000);
          video.srcObject = stream;
          Promise.resolve(video.play()).then(ready, failed);
        });
        if (!current()) return false;
        emit('ready', 'Private preview only. Choose Describe what you see to send one still image to REIGN.');
        return true;
      } catch (error) {
        retire(acquired);
        if (current()) {
          stream = null; video.pause(); video.srcObject = null;
          const denied = ['NotAllowedError', 'PermissionDeniedError'].includes(error.name);
          emit('error', denied ? 'Camera permission was not granted. Allow camera access in your browser, then try Camera again.' :
            error.name === 'NotFoundError' ? 'No camera was found. Connect one, then try Camera again.' : error.message || 'The camera could not start.');
        }
        return false;
      } finally { if (current()) starting = null; }
    })();
    starting = task;
    const clear = () => { if (starting === task) starting = null; };
    void task.then(clear, clear);
    return task;
  }
  async function capture() {
    const revision = epoch, track = stream?.getVideoTracks()[0];
    const current = () => !disposed && epoch === revision && phase === 'ready' && track?.readyState === 'live';
    if (!current() || video.readyState < 2 || !video.videoWidth || !video.videoHeight) throw Error('Open the camera preview before describing an image.');
    if (capturing) throw Error('A camera image is already being prepared.');
    capturing = true;
    const canvas = doc.createElement('canvas');
    try {
      const scale = Math.min(1, 960 / video.videoWidth, 540 / video.videoHeight);
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      const context = canvas.getContext('2d', {alpha: false});
      if (!context) throw Error('This browser could not capture the camera frame.');
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const capturedAt = new Date().toISOString();
      for (const quality of [.84, .7, .55]) {
        const blob = await new Promise((resolve, reject) => {
          const timeout = timers.setTimeout(() => reject(Error('Camera capture timed out. Try again.')), 5000);
          try { canvas.toBlob(value => { timers.clearTimeout(timeout); resolve(value); }, 'image/jpeg', quality); }
          catch (error) { timers.clearTimeout(timeout); reject(error); }
        });
        if (!current()) throw stopped();
        if (blob?.type === 'image/jpeg' && blob.size > 0 && blob.size <= 512 * 1024)
          return {blob, width: canvas.width, height: canvas.height, capturedAt};
      }
      throw Error('The camera image is too large. Try a simpler view.');
    } finally { canvas.width = canvas.height = 0; if (revision === epoch) capturing = false; }
  }
  return {start, capture, stop, snapshot: () => ({phase, active: phase === 'ready'}),
    dispose() { if (disposed) return; stop(); disposed = true; }};
}
