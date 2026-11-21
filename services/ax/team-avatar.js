/* Small precomputed neural portrait; no model weights, worker or inference here.
 * Audio is authoritative. Expression cues are per sentence; mouth motion follows
 * actual PCM energy rather than claiming phoneme-accurate lip sync. */
const EXPRESSIONS = ['neutral', 'warm', 'thoughtful', 'concerned'];
const ATLAS = '/assets/team-avatar-assets/portrait-atlas.webp';
const SIZE = 256;

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

export function createTeamAvatar({canvas, api, onStatus = () => {}}) {
  const ctx = canvas.getContext('2d', {alpha: false});
  const atlas = new Image();
  let ready = false, dead = false, unlocked = false, revision = 0, pending = null;
  let audioContext = null, source = null, analyser = null, finishAudio = null;
  let animation = null, lastPaint = 0, lastTime = 0, mouth = 0, expression = 0;
  let previousExpression = 0, expressionChanged = 0, speaking = false;
  const samples = new Float32Array(256);
  canvas.width = canvas.height = SIZE;

  function status(state, message) { if (!dead) onStatus({state, message}); }
  function schedule() {
    if (!dead && ready && animation === null && !document.hidden) animation = requestAnimationFrame(paint);
  }
  function stamp(exp, row, column, weight, crop = null) {
    if (weight <= .0001) return;
    ctx.globalAlpha = weight;
    const y = (exp * 4 + row) * SIZE;
    if (crop) ctx.drawImage(atlas, column * SIZE, y + crop.top, SIZE, crop.height, 0, crop.top, SIZE, crop.height);
    else ctx.drawImage(atlas, column * SIZE, y, SIZE, SIZE, 0, 0, SIZE, SIZE);
  }
  function paint(now) {
    animation = null;
    if (dead || !ready || document.hidden) return;
    if (now - lastPaint < 1000 / 30) { schedule(); return; }
    const dt = Math.min(100, Math.max(0, now - (lastTime || now)));
    lastTime = lastPaint = now;
    let desired = 0;
    if (speaking && analyser) { analyser.getFloatTimeDomainData(samples); desired = speechEnvelope(samples); }
    mouth += (desired - mouth) * (1 - Math.exp(-dt / (desired > mouth ? 35 : 90)));
    const yaw = Math.sin(now / 2200) * 1.65;
    const transition = Math.min(1, Math.max(0, (now - expressionChanged) / 260));
    const expressions = previousExpression === expression ? [[expression, 1]] :
      [[previousExpression, 1 - transition], [expression, transition]];
    const yawWeights = mixAxis(yaw, -3, 3, 3);
    const mouthWeights = mixAxis(mouth, 0, .9, 3);
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.globalCompositeOperation = 'lighter';
    for (const [exp, ew] of expressions) for (const [col, yw] of yawWeights) for (const [row, mw] of mouthWeights)
      stamp(exp, row, col, ew * yw * mw);
    ctx.globalCompositeOperation = 'source-over';
    // Use the corresponding neural blink only over the eye region so narration
    // can keep moving the mouth during a blink.
    const phase = now % 4700;
    const blink = phase < 180 ? Math.sin(phase / 180 * Math.PI) : 0;
    if (blink > .001) {
      const dominant = transition < .5 ? previousExpression : expression;
      for (const [col, weight] of yawWeights) stamp(dominant, 3, col, weight * blink, {top: 66, height: 54});
    }
    ctx.globalAlpha = 1;
    canvas.dataset.speaking = String(speaking);
    canvas.dataset.expression = EXPRESSIONS[expression];
    canvas.dataset.mode = 'precomputed-neural';
    schedule();
  }
  function endAudio() {
    if (source) { try { source.stop(); } catch {} source.disconnect(); source = null; }
    if (analyser) { analyser.disconnect(); analyser = null; }
    if (finishAudio) { const finish = finishAudio; finishAudio = null; finish(); }
    speaking = false;
  }
  function stop() {
    revision++; pending = null; endAudio();
    previousExpression = expression; expression = 0; expressionChanged = performance.now();
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
        previousExpression = expression;
        expression = Math.max(0, EXPRESSIONS.indexOf(segment.expression));
        expressionChanged = performance.now();
        source = audioContext.createBufferSource(); source.buffer = buffer;
        analyser = audioContext.createAnalyser(); analyser.fftSize = 256;
        source.connect(analyser); analyser.connect(audioContext.destination);
        speaking = true; status('speaking', segment.text || 'REIGN is speaking.');
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
  function visibility() {
    if (document.hidden && animation !== null) { cancelAnimationFrame(animation); animation = null; }
    else { lastTime = performance.now(); schedule(); }
  }
  function destroy() {
    if (dead) return;
    stop(); dead = true;
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    document.removeEventListener('visibilitychange', visibility);
    if (audioContext) void audioContext.close().catch(() => {});
    audioContext = null; atlas.src = ''; ready = false;
    canvas.width = canvas.height = 0;
  }
  document.addEventListener('visibilitychange', visibility);
  atlas.src = ATLAS;
  void atlas.decode().then(() => {
    if (dead) return;
    if (atlas.naturalWidth !== 768 || atlas.naturalHeight !== 4096) throw Error('invalid atlas');
    ready = true; status('ready', 'REIGN is ready.'); schedule();
  }).catch(() => { if (!dead) status('error', 'The portrait could not load. Voice and conversation remain available.'); });
  return {play, stop, unlock, destroy};
}
