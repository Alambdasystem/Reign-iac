/* Small precomputed neural portrait; no model weights, worker or inference here.
 * Audio is authoritative. Expression cues are per sentence; mouth motion follows
 * actual PCM energy rather than claiming phoneme-accurate lip sync. */
import { createReignAvatar, speechEnvelope } from '/assets/reign-avatar.js';
export { mixAxis, speechEnvelope } from '/assets/reign-avatar.js';

export function createTeamAvatar({canvas, api, onStatus = () => {}}) {
  let ready = false, dead = false, unlocked = false, revision = 0, pending = null;
  let audioContext = null, source = null, analyser = null, finishAudio = null;
  let speaking = false;
  const samples = new Float32Array(256);
  function status(state, message) { if (!dead) onStatus({state, message}); }
  const portrait = createReignAvatar({canvas,
    onStatus(value) {
      if (dead) return;
      ready = value.state === 'ready';
      // A late image decode must not replace a current speaking/listen prompt.
      if (value.state === 'ready' && (speaking || pending)) return;
      status(value.state, value.message);
    },
    getSpeechLevel() {
      if (!speaking || !analyser || audioContext?.state !== 'running') return 0;
      analyser.getFloatTimeDomainData(samples);
      return speechEnvelope(samples);
    }});
  function endAudio() {
    if (source) { try { source.stop(); } catch {} source.disconnect(); source = null; }
    if (analyser) { analyser.disconnect(); analyser = null; }
    if (finishAudio) { const finish = finishAudio; finishAudio = null; finish(); }
    speaking = false; portrait.setSpeaking(false);
  }
  function stop() {
    revision++; pending = null; endAudio();
    portrait.stop();
    if (ready) status('ready', 'REIGN is ready.');
  }
  async function unlock() {
    if (dead) return false;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw Error('audio unavailable');
      if (!audioContext) audioContext = new Audio();
      await audioContext.resume();
      if (dead) return false;
      unlocked = audioContext.state === 'running';
      if (!unlocked) { status('paused', 'Tap Listen to REIGN to enable audio.'); return false; }
      status('ready', 'REIGN audio is enabled.');
      if (pending) { const next = pending; pending = null; void play(next.roomId, next.event); }
      return true;
    } catch {
      status('error', 'Audio could not start. The written reply is still available.');
      return false;
    }
  }
  async function play(roomId, event) {
    if (dead) return;
    stop();
    const data = event?.data || {};
    let segments = Array.isArray(data.segments) ? data.segments.slice(0, 4) : [];
    if (!segments.length && data.audio_id) segments = [{audio_id: data.audio_id, text: data.text, expression: 'neutral'}];
    if (!segments.some(segment => segment.audio_id)) {
      status(data.speech_status === 'unavailable' ? 'error' : 'ready', data.speech_status === 'unavailable'
        ? 'The reply is saved, but speech could not be generated.' : 'REIGN replied in the conversation.');
      return;
    }
    if (!unlocked || !audioContext || audioContext.state !== 'running') {
      pending = {roomId, event}; status('paused', 'Tap Listen to REIGN to hear this reply.'); return;
    }
    const current = revision;
    status('loading', 'Preparing REIGN’s voice…');
    try {
      // The image preloads independently. A slow or unavailable portrait must
      // not prevent an otherwise valid spoken reply from playing.
      for (const segment of segments) {
        if (dead || current !== revision) return;
        if (!/^[a-f0-9-]{36}$/.test(segment.audio_id || '')) continue;
        const blob = await api(`/api/team/rooms/${encodeURIComponent(roomId)}/audio/${encodeURIComponent(segment.audio_id)}`,
          'GET', undefined, {responseType: 'blob'});
        if (dead || current !== revision) return;
        if (blob.size > 1_500_000) throw Error('audio limit');
        const buffer = await audioContext.decodeAudioData(await blob.arrayBuffer());
        if (dead || current !== revision) return;
        if (buffer.duration > 30 || buffer.numberOfChannels !== 1) throw Error('audio limit');
        portrait.setExpression(segment.expression);
        source = audioContext.createBufferSource(); source.buffer = buffer;
        analyser = audioContext.createAnalyser(); analyser.fftSize = 256;
        source.connect(analyser); analyser.connect(audioContext.destination);
        speaking = true; portrait.setSpeaking(true); status('speaking', segment.text || 'REIGN is speaking.');
        await new Promise(resolve => {
          finishAudio = resolve;
          source.onended = () => { if (finishAudio === resolve) { finishAudio = null; resolve(); } };
          source.start();
        });
        if (dead || current !== revision) return;
        endAudio();
      }
      if (current === revision) status('ready', 'REIGN is ready.');
    } catch {
      if (!dead && current === revision) { endAudio(); status('error', 'Speech is unavailable or expired. The written reply is saved.'); }
    }
  }
  function destroy() {
    if (dead) return;
    stop(); dead = true;
    portrait.destroy();
    if (audioContext) void audioContext.close().catch(() => {});
    audioContext = null; ready = false;
  }
  return {play, stop, unlock, destroy};
}
