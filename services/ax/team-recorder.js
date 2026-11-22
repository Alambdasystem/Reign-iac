// An explicit, bounded microphone capture. This controller never submits text,
// records remote audio, or changes the enabled state of a call's microphone.
export function createTeamRecorder(deps = {}) {
  const scope = globalThis, devices = deps.mediaDevices || scope.navigator?.mediaDevices;
  const Recorder = deps.MediaRecorder || scope.MediaRecorder, Stream = deps.MediaStream || scope.MediaStream;
  const Reader = deps.FileReader || scope.FileReader, timers = deps.timers || scope;
  const Audio = deps.AudioContext || scope.AudioContext || scope.webkitAudioContext;
  const now = deps.now || (() => scope.performance?.now?.() ?? Date.now());
  let current = null, closed = false;
  const limitBytes = 512 * 1024;
  const notify = (state, message) => { try { deps.onState?.({state, message}); } catch {} };
  const stopTracks = stream => { for (const track of stream?.getTracks?.() || []) { try { track.stop(); } catch {} } };
  const cancelled = () => Object.assign(new Error('Recording cancelled.'), {name: 'AbortError'});
  function stopDetector(session) {
    timers.clearTimeout(session.meterTimer); session.meterTimer = undefined;
    try { session.meterSource?.disconnect(); } catch {}
    try { session.meter?.disconnect(); } catch {}
    session.meterSource = session.meter = null;
    if (session.context) { try { void session.context.close().catch(() => {}); } catch {} session.context = null; }
  }
  function finish(session, error, value) {
    if (session.done) return;
    session.done = true;
    timers.clearTimeout(session.timer); stopDetector(session); stopTracks(session.stream);
    session.chunks.length = 0;
    if (current === session) { current = null; notify(error && error.name !== 'AbortError' ? 'error' : 'idle', error?.name === 'AbortError' ? '' : error?.message || ''); }
    error ? session.reject(error) : session.resolve(value);
  }
  function start({stream, autoStop = false} = {}) {
    if (closed) return Promise.reject(cancelled());
    if (current) return Promise.reject(Error('A recording is already in progress.'));
    if (!Recorder || !Stream || !Reader || !devices?.getUserMedia) return Promise.reject(Error('Voice recording is not available in this browser. You can type your message instead.'));
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const session = {promise, resolve, reject, stream: null, recorder: null, chunks: [], bytes: 0, done: false,
      timer: undefined, meterTimer: undefined, context: null, meter: null, meterSource: null,
      speechMs: 0, heardSpeech: false, lastVoiceAt: 0, lastSampleAt: 0};
    current = session; notify('requesting', autoStop ? 'Allow microphone access to talk to REIGN.' : 'Allow microphone access to dictate a message.');
    // Resume from the initiating gesture, before a permission prompt can yield.
    // This local energy detector never connects the microphone to a speaker.
    if (autoStop && Audio) {
      try { session.context = new Audio(); void session.context.resume().catch(() => {}); } catch { stopDetector(session); }
    }
    let permission;
    try {
      const source = stream?.getAudioTracks?.().find(track => track.readyState !== 'ended' && track.enabled);
      // Cloning owns only this recording track; a muted call remains muted.
      permission = source ? Promise.resolve(new Stream([source.clone()]))
        : devices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}, video: false});
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
          timers.clearTimeout(session.timer); stopDetector(session); stopTracks(session.stream);
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
        let detectsSilence = false;
        if (session.context) {
          try {
            session.meterSource = session.context.createMediaStreamSource(owned);
            session.meter = session.context.createAnalyser(); session.meter.fftSize = 1024;
            session.meterSource.connect(session.meter);
            const samples = new Float32Array(session.meter.fftSize);
            session.lastSampleAt = now();
            const sample = () => {
              if (session.done || current !== session || recorder.state !== 'recording') return;
              const time = now(), gap = time - session.lastSampleAt, elapsed = Math.max(0, Math.min(100, gap));
              session.lastSampleAt = time;
              if (gap > 250) session.lastVoiceAt = time;
              if (session.context?.state === 'running') {
                try { session.meter.getFloatTimeDomainData(samples); }
                catch {
                  stopDetector(session);
                  notify('recording', 'Recording your microphone. Choose Finish recording when done; the limit is 20 seconds.');
                  return;
                }
                let energy = 0; for (const value of samples) energy += value * value;
                const rms = Math.sqrt(energy / samples.length);
                // Hysteresis keeps quieter syllables once speech has begun.
                // A click or brief microphone startup pop cannot end a turn.
                if (rms >= (session.heardSpeech ? .006 : .012)) {
                  session.speechMs += elapsed; session.lastVoiceAt = time;
                  if (!session.heardSpeech && session.speechMs >= 100) {
                    session.heardSpeech = true;
                    notify('recording', 'Hearing you. Pause briefly when your question is finished.');
                  }
                } else if (!session.heardSpeech) session.speechMs = 0;
                if (session.heardSpeech && time - session.lastVoiceAt >= 750) {
                  notify('processing', 'Sending your spoken question…');
                  void stop().catch(() => {}); return;
                }
              } else {
                // Suspended/background tabs cannot infer silence from stale
                // analyser data. Keep manual Finish and the hard cap available.
                session.lastVoiceAt = time;
              }
              session.meterTimer = timers.setTimeout(sample, 50);
            };
            session.meterTimer = timers.setTimeout(sample, 50); detectsSilence = true;
          } catch { stopDetector(session); }
        }
        notify('recording', detectsSilence
          ? 'Listening. Speak, then pause briefly to send. You can also finish manually.'
          : 'Recording your microphone. Choose Finish recording when done; the limit is 20 seconds.');
        session.timer = timers.setTimeout(() => { void stop().catch(() => {}); }, 20000);
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
