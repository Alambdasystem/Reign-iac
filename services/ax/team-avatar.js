/* The existing neural portrait follows the sound actually being played.
 * Mouth openness follows PCM energy, not phoneme prediction. Audio never waits
 * for portrait loading or model inference. */
import { createReignAvatar, speechEnvelope } from '/assets/reign-avatar.js';
export { mixAxis, speechEnvelope } from '/assets/reign-avatar.js';

export function audioOutputTime(context, now = performance.now()) {
  const time = Number(context?.currentTime) || 0;
  try {
    const stamp = context.getOutputTimestamp?.();
    if (Number.isFinite(stamp?.contextTime) && stamp.contextTime >= 0 &&
        Number.isFinite(stamp.performanceTime) && stamp.performanceTime > 0 &&
        now >= stamp.performanceTime && now - stamp.performanceTime <= 500) {
      // Use a recent device timestamp, never beyond the rendered position.
      return Math.min(time, stamp.contextTime + Math.min(.1, (now - stamp.performanceTime) / 1000));
    }
  } catch { /* Older browsers expose only context and latency estimates. */ }
  const latency = Math.min(1, Math.max(0, Number(context?.baseLatency) || 0) +
    Math.max(0, Number(context?.outputLatency) || 0));
  return Math.max(0, time - latency);
}

export function pcmEnvelope(buffer) {
  if (!Number.isFinite(buffer.duration) || buffer.duration <= 0 || buffer.duration > 30 || buffer.numberOfChannels !== 1 ||
      !Number.isFinite(buffer.sampleRate) || buffer.sampleRate < 8000 || buffer.sampleRate > 192000) throw Error('audio limit');
  const pcm = buffer.getChannelData(0), window = Math.max(1, Math.round(buffer.sampleRate / 100));
  if (!pcm.length || pcm.length > buffer.sampleRate * 30 + 1) throw Error('audio limit');
  const values = new Float32Array(Math.ceil(pcm.length / window));
  for (let offset = 0, index = 0; offset < pcm.length; offset += window, index++)
    values[index] = speechEnvelope(pcm.subarray(offset, Math.min(pcm.length, offset + window)));
  return {values, step: window / buffer.sampleRate};
}

export function createTeamAvatar({canvas, api, onStatus = () => {}}) {
  let ready = false, dead = false, unlocked = false, revision = 0, pending = null;
  let audioContext = null, source = null, clip = null, finishAudio = null, drainTimer = null, playbackTimer = null, requestAbort = null;
  let speaking = false, loadingAudio = false;
  let livePlayback = null;
  const resumeWaiters = new Set();
  function status(state, message) {
    if (!dead) onStatus({state, message, audioEnabled: !!(unlocked && audioContext?.state === 'running'),
      retryAvailable: !!(pending && unlocked && audioContext?.state === 'running')});
  }
  function playbackState() {
    if (livePlayback) { canvas.dataset.audioState = livePlayback.audioState || 'running'; return livePlayback; }
    canvas.dataset.audioState = audioContext?.state || 'locked';
    if (!clip) return {active: false};
    if (audioContext?.state !== 'running') return {active: false, clockMs: clip.position * 1000, segment: clip.segment};
    const position = audioOutputTime(audioContext);
    clip.position = position;
    const elapsed = position - clip.start;
    const active = elapsed >= 0 && elapsed < clip.duration;
    if (active && !speaking) { speaking = true; status('speaking', clip.text || 'REIGN is speaking.'); }
    const index = Math.max(0, Math.min(clip.envelope.values.length - 1, elapsed / clip.envelope.step));
    const low = Math.floor(index), fraction = index - low;
    const level = active ? clip.envelope.values[low] * (1 - fraction) +
      clip.envelope.values[Math.min(low + 1, clip.envelope.values.length - 1)] * fraction : 0;
    return {active, level, expression: clip.expression, clockMs: position * 1000,
      cueMs: clip.start * 1000, expressionSource: clip.expressionSource, segment: clip.segment};
  }
  const portrait = createReignAvatar({canvas,
    onStatus(value) {
      if (dead) return;
      ready = value.state === 'ready';
      if (value.state === 'ready' && (speaking || pending || loadingAudio)) return;
      status(value.state, value.message);
    }, getPlaybackState: playbackState});
  function wakeResumeWaiters() { for (const resume of [...resumeWaiters]) resume(); }
  function endAudio() {
    if (drainTimer !== null) clearTimeout(drainTimer); drainTimer = null;
    if (playbackTimer !== null) clearTimeout(playbackTimer); playbackTimer = null;
    const retired = source; source = null; clip = null;
    if (retired) { retired.onended = null; try { retired.stop(); } catch {} retired.disconnect(); }
    if (finishAudio) { const finish = finishAudio; finishAudio = null; finish(); }
    speaking = false; portrait.setPlaybackState({active: false});
  }
  function stop() {
    revision++; pending = null; loadingAudio = false;
    livePlayback = null;
    requestAbort?.abort(); requestAbort = null;
    endAudio(); wakeResumeWaiters(); portrait.stop();
    if (ready) status('ready', 'REIGN is ready.');
  }
  function startLivePlayback() {
    stop(); livePlayback = {active: false, audioState: 'connecting'};
    portrait.setPlaybackState(livePlayback);
  }
  function setLivePlayback(value = {}) {
    // Incoming native WebRTC audio owns the same prepared portrait. An idle
    // live microphone must not erase another participant's cached room speech.
    if (!livePlayback && !value.active) return;
    if (!livePlayback) startLivePlayback();
    livePlayback = {...value, active: value.active === true};
    portrait.setPlaybackState(livePlayback);
    if (livePlayback.active && !speaking) { speaking = true; status('speaking', 'REIGN is speaking live.'); }
    else if (!livePlayback.active && speaking) { speaking = false; status('ready', 'REIGN is listening.'); }
  }
  function endLivePlayback() { if (livePlayback) stop(); }
  function finishWhenHeard() {
    if (drainTimer !== null) clearTimeout(drainTimer); drainTimer = null;
    if (!clip?.ended || !finishAudio || audioContext?.state !== 'running') return;
    if (audioOutputTime(audioContext) >= clip.start + clip.duration - .003 || performance.now() - clip.endedAt > 1500) {
      const finish = finishAudio; finishAudio = null; finish();
    } else drainTimer = setTimeout(finishWhenHeard, 20);
  }
  function tickPlayback() {
    if (playbackTimer !== null) clearTimeout(playbackTimer); playbackTimer = null;
    if (dead || !clip) return;
    portrait.setPlaybackState(playbackState());
    // Audio status and completion work even if the portrait is still loading,
    // missing, or its animation is hidden. This loop exists only during output.
    if (audioContext?.state === 'running') playbackTimer = setTimeout(tickPlayback, 33);
  }
  function contextChanged() {
    if (dead || !audioContext) return;
    unlocked = audioContext.state === 'running';
    canvas.dataset.audioState = audioContext.state;
    if (audioContext.state === 'closed') {
      const interrupted = !!(clip || loadingAudio || pending);
      stop();
      if (interrupted) status('paused', 'The audio connection closed. The written reply is saved; enable audio before asking again.');
      return;
    }
    if (!unlocked) {
      if (playbackTimer !== null) clearTimeout(playbackTimer); playbackTimer = null;
      speaking = false; portrait.setPlaybackState(playbackState());
      if (clip || loadingAudio || pending) status('paused', 'REIGN audio is paused. Tap Enable REIGN audio to resume.');
    } else {
      // A long suspension is not running output time. Give the device tail a
      // fresh bounded drain window when the audio clock starts moving again.
      if (clip?.ended) clip.endedAt = performance.now();
      wakeResumeWaiters(); finishWhenHeard();
      if (clip) tickPlayback();
    }
  }
  async function unlock() {
    if (dead) return false;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw Error('audio unavailable');
      if (!audioContext || audioContext.state === 'closed') {
        audioContext?.removeEventListener('statechange', contextChanged);
        audioContext = new Audio(); audioContext.addEventListener('statechange', contextChanged);
      }
      // This call begins synchronously inside Ask, Talk, or Listen's gesture.
      await audioContext.resume();
      if (dead) return false;
      contextChanged();
      if (!unlocked) { status('paused', 'Tap Enable REIGN audio to hear the reply.'); return false; }
      if (pending) { const next = pending; pending = null; void play(next.roomId, next.event); }
      else if (!clip && !loadingAudio) status('ready', 'REIGN audio is enabled.');
      return true;
    } catch {
      unlocked = false;
      status('paused', 'Audio could not start. Tap Enable REIGN audio to try again. The written reply is saved.');
      return false;
    }
  }
  function waitUntilRunning(current) {
    if (dead || current !== revision) return Promise.resolve(false);
    if (audioContext?.state === 'running') return Promise.resolve(true);
    status('paused', 'REIGN audio is paused. Tap Enable REIGN audio to resume.');
    return new Promise(resolve => {
      const check = () => {
        if (dead || current !== revision || audioContext?.state === 'running') {
          resumeWaiters.delete(check); resolve(!dead && current === revision && audioContext?.state === 'running');
        }
      };
      resumeWaiters.add(check); check();
    });
  }
  async function play(roomId, event) {
    if (dead) return;
    stop();
    const data = event?.data || {};
    let segments = Array.isArray(data.segments) ? data.segments.slice(0, 4) : [];
    if (!segments.length && data.audio_id) segments = [{audio_id: data.audio_id, text: data.text, expression: 'neutral', annotated: false}];
    if (!segments.some(segment => segment.audio_id)) {
      status(data.speech_status === 'unavailable' ? 'error' : 'ready', data.speech_status === 'unavailable'
        ? 'The reply is saved, but speech could not be generated.' : 'REIGN replied in the conversation.');
      return;
    }
    if (!unlocked || !audioContext || audioContext.state !== 'running') {
      pending = {roomId, event}; status('paused', 'Tap Enable REIGN audio to hear this reply.'); return;
    }
    const current = revision;
    requestAbort = new AbortController(); loadingAudio = true;
    const abort = requestAbort, context = audioContext;
    let index = 0;
    const load = async segment => {
      if (!/^[a-f0-9-]{36}$/.test(segment?.audio_id || '')) return null;
      const blob = await api('/api/team/rooms/' + encodeURIComponent(roomId) + '/audio/' + encodeURIComponent(segment.audio_id),
        'GET', undefined, {responseType: 'blob', signal: abort.signal, timeout: 45000});
      if (dead || current !== revision) return null;
      if (blob.size > 1_500_000) throw Error('audio limit');
      const buffer = await context.decodeAudioData(await blob.arrayBuffer());
      if (dead || current !== revision) return null;
      return {buffer, envelope: pcmEnvelope(buffer)};
    };
    // One bounded lookahead removes download/decode gaps between sentences.
    // Capture rejection immediately even if this turn is cancelled before its
    // pending clip is consumed; no stale fetch may create a playback source.
    const prepare = segment => load(segment).then(value => ({value}), error => ({error}));
    let prepared = prepare(segments[0]);
    status('buffering', 'Preparing REIGN’s voice…');
    try {
      for (; index < segments.length; index++) {
        const segment = segments[index];
        if (dead || current !== revision) return;
        if (index > 0) status('buffering', 'Preparing REIGN’s next sentence…');
        const result = await prepared;
        if (dead || current !== revision) return;
        if (result.error) throw result.error;
        prepared = index + 1 < segments.length ? prepare(segments[index + 1]) : null;
        if (!result.value) continue;
        const {buffer, envelope} = result.value;
        if (!await waitUntilRunning(current)) return;
        source = audioContext.createBufferSource(); source.buffer = buffer;
        source.connect(audioContext.destination);
        // The output clock, mouth and cue share one scheduled audio origin.
        const start = audioContext.currentTime + .02;
        const annotated = segment.annotated !== false && ['neutral', 'warm', 'thoughtful', 'concerned'].includes(segment.expression);
        clip = {start, duration: buffer.duration, position: audioOutputTime(audioContext), envelope,
          expression: annotated ? segment.expression : 'neutral', expressionSource: annotated ? 'response-segment' : 'neutral',
          text: segment.text, segment: index + 1, ended: false};
        await new Promise(resolve => {
          finishAudio = resolve;
          source.onended = () => {
            if (!clip || current !== revision || dead) return;
            clip.ended = true; clip.endedAt = performance.now(); finishWhenHeard();
          };
          source.start(start);
          tickPlayback();
        });
        if (dead || current !== revision) return;
        endAudio();
      }
      if (current === revision) { portrait.stop(); status('ready', 'REIGN is ready.'); }
    } catch {
      if (!dead && current === revision) {
        endAudio(); portrait.stop();
        pending = {roomId, event: {data: {...data, segments: segments.slice(index)}}};
        status('error', 'REIGN’s remaining speech could not load. Tap Retry REIGN audio to try again. The written reply is saved.');
      }
    } finally {
      if (current === revision) { loadingAudio = false; requestAbort = null; }
    }
  }
  function destroy() {
    if (dead) return;
    stop(); dead = true; portrait.destroy();
    if (audioContext) {
      audioContext.removeEventListener('statechange', contextChanged);
      void audioContext.close().catch(() => {});
    }
    audioContext = null; ready = false;
  }
  return {play, stop, unlock, destroy, startLivePlayback, setLivePlayback, endLivePlayback};
}
