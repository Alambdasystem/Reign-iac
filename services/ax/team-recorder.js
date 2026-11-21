// An explicit, bounded microphone capture. This controller never submits text,
// records remote audio, or changes the enabled state of a call's microphone.
export function createTeamRecorder(deps = {}) {
  const scope = globalThis, devices = deps.mediaDevices || scope.navigator?.mediaDevices;
  const Recorder = deps.MediaRecorder || scope.MediaRecorder, Stream = deps.MediaStream || scope.MediaStream;
  const Reader = deps.FileReader || scope.FileReader, timers = deps.timers || scope;
  let current = null, closed = false;
  const limitBytes = 512 * 1024;
  const notify = (state, message) => { try { deps.onState?.({state, message}); } catch {} };
  const stopTracks = stream => { for (const track of stream?.getTracks?.() || []) { try { track.stop(); } catch {} } };
  const cancelled = () => Object.assign(new Error('Recording cancelled.'), {name: 'AbortError'});
  function finish(session, error, value) {
    if (session.done) return;
    session.done = true;
    timers.clearTimeout(session.timer); stopTracks(session.stream);
    session.chunks.length = 0;
    if (current === session) { current = null; notify(error && error.name !== 'AbortError' ? 'error' : 'idle', error?.name === 'AbortError' ? '' : error?.message || ''); }
    error ? session.reject(error) : session.resolve(value);
  }
  function start({stream} = {}) {
    if (closed) return Promise.reject(cancelled());
    if (current) return Promise.reject(Error('A recording is already in progress.'));
    if (!Recorder || !Stream || !Reader || !devices?.getUserMedia) return Promise.reject(Error('Voice recording is not available in this browser. You can type your message instead.'));
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const session = {promise, resolve, reject, stream: null, recorder: null, chunks: [], bytes: 0, done: false, timer: undefined};
    current = session; notify('requesting', 'Allow microphone access to dictate a message.');
    let permission;
    try {
      const source = stream?.getAudioTracks?.().find(track => track.readyState !== 'ended' && track.enabled);
      // Cloning owns only this recording track; a muted call remains muted.
      permission = source ? Promise.resolve(new Stream([source.clone()]))
        : devices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: false});
    } catch (error) { finish(session, error); return promise; }
    void (async () => {
      try {
        const owned = await permission;
        if (session.done || current !== session) { stopTracks(owned); return; }
        session.stream = owned;
        const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/webm'].find(type => Recorder.isTypeSupported?.(type));
        if (!mime) throw Error('This browser cannot record a supported audio format. You can type your message instead.');
        const recorder = new Recorder(owned, {mimeType: mime, audioBitsPerSecond: 32000}); session.recorder = recorder;
        recorder.ondataavailable = event => {
          if (session.done || !event.data?.size) return;
          session.bytes += event.data.size;
          if (session.bytes > limitBytes) {
            finish(session, Error('The recording is too large. Try a shorter message.'));
            try { recorder.stop(); } catch {}
            return;
          }
          session.chunks.push(event.data);
        };
        recorder.onerror = () => { finish(session, Error('The microphone recording failed. Try again or type your message.')); try { recorder.stop(); } catch {} };
        recorder.onstop = () => {
          if (session.done) return;
          timers.clearTimeout(session.timer); stopTracks(session.stream);
          if (!session.bytes) { finish(session, Error('No audio was recorded. Try again or type your message.')); return; }
          notify('processing', 'Preparing your recording…');
          const type = String(recorder.mimeType || mime).split(';')[0];
          const blob = new Blob(session.chunks, {type}); session.chunks.length = 0;
          const reader = new Reader();
          reader.onerror = () => finish(session, Error('The recording could not be read. Try again.'));
          reader.onload = () => {
            if (session.done) return;
            const result = String(reader.result || ''), split = result.indexOf(',');
            if (split < 0) finish(session, Error('The recording could not be prepared.'));
            else finish(session, null, {audio_base64: result.slice(split + 1), mime_type: type});
          };
          reader.readAsDataURL(blob);
        };
        recorder.start(250);
        notify('recording', 'Recording your microphone · stops after 20 seconds.');
        session.timer = timers.setTimeout(stop, 20000);
      } catch (error) { finish(session, error); }
    })();
    return promise;
  }
  function stop() {
    const session = current;
    if (!session) return Promise.resolve(null);
    if (!session.recorder) { cancel(); return session.promise; }
    if (session.recorder.state === 'recording') {
      try { session.recorder.stop(); stopTracks(session.stream); }
      catch (error) { finish(session, error); }
    }
    return session.promise;
  }
  function cancel() {
    const session = current;
    if (!session) return;
    finish(session, cancelled());
    try { if (session.recorder?.state === 'recording') session.recorder.stop(); } catch {}
  }
  return {start, stop, cancel, dispose() { closed = true; cancel(); }};
}
