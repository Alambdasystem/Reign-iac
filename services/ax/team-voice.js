import '/assets/voice.js';

// The shared room uses the same live microphone/WebRTC transport as private
// voice. Only this adapter knows room routes; owner chat and voice APIs are
// never called. Spoken requests still execute through the authorized room.
export function createTeamVoice(deps = {}) {
  const api = deps.api, uuid = deps.uuid || (() => crypto.randomUUID());
  const timers = deps.timers || globalThis;
  const makeController = deps.createVoiceController || globalThis.REIGNVoice?.createVoiceController;
  const ownSessions = new Map();
  let session = null, disposed = false, lastState = {active: false, phase: 'idle', muted: false};
  let controller, stopping = null, intent = 0;
  const aborted = () => Object.assign(new Error('Live microphone session ended.'), {name: 'AbortError'});
  const canonical = value => typeof value === 'string' ? value.replace(/\s+/gu, ' ').trim() : '';
  const roomPath = roomId => '/api/team/rooms/' + encodeURIComponent(roomId);
  const sessionPath = value => roomPath(value.roomId) + '/voice/sessions/' + encodeURIComponent(value.id);
  const current = value => !disposed && session === value && !value.closing;
  function emit() {
    const uncertain = session?.uncertain;
    deps.onState?.({...lastState, voiceSessionId: session?.id || null,
      uncertain: !!uncertain,
      status: uncertain ? 'Your spoken request was not confirmed. Retry this voice request to confirm the same turn.' : lastState.status});
  }
  function settle(job, error, value) {
    if (job.done) return;
    job.done = true; timers.clearTimeout(job.timer);
    job.signal?.removeEventListener('abort', job.abort);
    if (job.session.uncertain === job) { job.session.uncertain = null; emit(); }
    job.session.jobs.delete(job.callId);
    error ? job.reject(error) : job.resolve(value);
  }
  function resolveEvent(job, event) {
    if (job.done || !job.runId || event?.data?.run_id !== job.runId) return;
    const data = event.data;
    if (event.type === 'assistant_error') {
      settle(job, null, {status: 'failed', error: canonical(data.detail || data.error) || 'REIGN could not finish this room request.',
        thread_id: job.session.roomId, run_id: job.runId});
    } else if (event.type === 'assistant_message') {
      if (data.voice_session_id !== job.session.id) return;
      const answer = canonical(data.text);
      const result = {status: 'completed', answer, thread_id: job.session.roomId, run_id: job.runId};
      const segments = Array.isArray(data.segments) ? data.segments.map(segment => ({
        text: canonical(segment.text), expression: segment.expression})) : [];
      if (segments.length > 0 && segments.length <= 4 &&
          segments.every(segment => segment.text.length > 0 && segment.text.length <= 400 &&
            ['neutral', 'warm', 'thoughtful', 'concerned'].includes(segment.expression)) &&
          segments.map(segment => segment.text).join(' ') === answer) {
        result.speech = {speech_status: 'ready', status: result.status, text: answer,
          thread_id: result.thread_id, run_id: result.run_id, segments};
      }
      settle(job, null, result);
    }
  }
  function acceptEvents(roomId, events) {
    const value = session;
    if (!value || value.roomId !== roomId || value.closing) return;
    for (const event of events || []) {
      if (!['assistant_message', 'assistant_error'].includes(event?.type) || !event.data?.run_id) continue;
      value.results.set(event.data.run_id, event);
      while (value.results.size > 64) value.results.delete(value.results.keys().next().value);
      for (const job of value.jobs.values()) resolveEvent(job, event);
    }
  }
  async function closeLease(value, force = false) {
    if (!value) return;
    value.closing = true;
    for (const job of [...value.jobs.values()]) settle(job, aborted());
    if (value.closePromise && !force) return value.closePromise;
    // A pending offer has a client-generated ID, so Off can revoke it before
    // the provider's answer arrives. This cleanup never inherits call abort.
    value.closePromise = api(sessionPath(value), 'DELETE', undefined,
      {timeout: 12000, keepalive: true, handleAuth: false}).then(result => {
      value.closeFailed = false; return result;
    }, error => { value.closeFailed = true; throw error; });
    return value.closePromise;
  }
  async function transport(path, method = 'GET', body, options = {}) {
    const value = session;
    if (!value || !current(value)) throw aborted();
    if (path === '/api/voice' && method === 'GET') {
      // Room boot starts heartbeat asynchronously. Admit this exact viewer
      // before requesting a room-bound provider connection.
      await api(roomPath(value.roomId) + '/presence', 'POST', {
        connection_id: value.connectionId, in_call: false, microphone: false, camera: false, screen: false},
      {signal: options.signal, timeout: 12000});
      if (!current(value)) throw aborted();
      return api(roomPath(value.roomId) + '/voice', 'GET', undefined, {...options, timeout: 15000});
    }
    if (path === '/api/voice/offer' && method === 'POST') {
      value.offerStarted = true;
      let result;
      try {
        result = await api(roomPath(value.roomId) + '/voice/offer', 'POST', {
          sdp: body.sdp, connection_id: value.connectionId, client_session_id: value.id},
        {...options, timeout: 35000});
      } catch (error) {
        if (error.code === 'team_voice_closing') {
          // Off can precede a provider's late answer. Keep that server slot
          // reserved and require a new gesture; never reopen audio on a timer.
          error.message = 'Microphone is off. Your previous voice connection is still closing. Try Microphone on again in a moment.';
        }
        throw error;
      }
      if (!current(value)) { void closeLease(value, true).catch(() => {}); throw aborted(); }
      if (result.voice_session_id !== value.id) throw Error('The live voice session could not be verified. Start voice again.');
      value.connected = true;
      return result;
    }
    throw Error('This live voice operation is not available in a shared room.');
  }
  async function submit(job) {
    if (job.done || job.sending || !current(job.session)) return;
    job.sending = true;
    if (job.session.uncertain === job) { job.session.uncertain = null; emit(); }
    try {
      const result = await api(sessionPath(job.session) + '/turns', 'POST', job.payload,
        {signal: job.signal, timeout: 20000});
      if (job.done || !current(job.session)) return;
      if (!result?.run_id) throw Error('The spoken request could not be confirmed.');
      job.runId = result.run_id;
      if (result.event) deps.onAccepted?.(result);
      job.onStatus?.('running');
      resolveEvent(job, job.session.results.get(job.runId));
    } catch (error) {
      if (job.done) return;
      if (!current(job.session) || error.name === 'AbortError') settle(job, aborted());
      else if (error.code === 'team_voice_closed' || error.status === 410) {
        settle(job, error); void stop({reason: 'Live voice disconnected. Turn the microphone on to reconnect.', failed: true});
      }
      else if (error.status > 0 && error.status < 500) settle(job, error);
      else {
        // Keep the one original UUID and body for a visible retry. Continuing
        // to capture microphone audio never silently re-executes this request.
        job.session.uncertain = job; emit();
      }
    } finally { job.sending = false; }
  }
  function runTurn(text, options = {}) {
    const value = session;
    if (!value || !current(value) || !value.connected) return Promise.reject(aborted());
    if (value.jobs.has(options.voiceCallId)) return value.jobs.get(options.voiceCallId).promise;
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const job = {session: value, callId: options.voiceCallId, payload: {client_message_id: uuid(), text},
      signal: options.signal, onStatus: options.onStatus, promise, resolve, reject, done: false, sending: false, runId: null};
    job.abort = () => settle(job, aborted());
    job.timer = timers.setTimeout(() => {
      if (!job.done && current(value)) void stop({reason: 'Voice stopped because this room request could not be confirmed. Its saved status remains in the conversation.', failed: true});
    }, 180000);
    value.jobs.set(job.callId, job);
    if (job.signal?.aborted) job.abort();
    else { job.signal?.addEventListener('abort', job.abort, {once: true}); void submit(job); }
    return promise;
  }
  if (makeController) controller = makeController({
    ...(deps.controllerDeps || {}), api: transport, audio: deps.audio, runTurn,
    connectionCheck: false,
    cancelTurn: () => closeLease(session),
    onState(value) {
      lastState = value;
      if (!value.active && session && !session.closing) void closeLease(session).catch(() => {});
      emit();
    },
    onCaption: value => deps.onCaption?.(value),
    onLevel: level => deps.onLevel?.(level),
    onOutputLevel: (level, value) => deps.onOutputLevel?.(level, value),
    onUserSpeechStart: () => deps.onUserSpeechStart?.(),
  });
  async function start({roomId, connectionId}) {
    if (disposed || lastState.active || !roomId || !connectionId) return;
    if (!controller) {
      lastState = {active: false, phase: 'failed', status: 'Live voice could not load. Refresh this page to try again.'}; emit(); return;
    }
    const prior = stopping || (session ? stop({reason: lastState.status || 'Closing the previous voice connection.'}) : null);
    const requested = ++intent;
    if (prior && !await prior) return;
    if (disposed || requested !== intent) return;
    const value = {id: uuid(), roomId, connectionId, jobs: new Map(), results: new Map(),
      closing: false, connected: false, offerStarted: false, closePromise: null, uncertain: null};
    session = value; ownSessions.set(value.id, roomId);
    while (ownSessions.size > 32) ownSessions.delete(ownSessions.keys().next().value);
    return controller.start();
  }
  function stop(options = {}) {
    intent++;
    if (stopping) return stopping;
    const value = session;
    // stop() shuts tracks and the native sink synchronously, before awaiting
    // server cleanup. Calling it is independent of room-call media ownership.
    const stopped = controller?.stop(options);
    const closed = closeLease(value, !!value?.closeFailed);
    const cleanup = Promise.allSettled([stopped, closed]).then(results => {
      const succeeded = results.every(result => result.status === 'fulfilled');
      if (succeeded && session === value) session = null;
      if (!succeeded && !disposed) {
        lastState = {...lastState, active: false, phase: 'failed', status: 'Microphone is off. Server voice cleanup could not be confirmed; reconnect before starting again.'}; emit();
      }
      return succeeded;
    }).finally(() => { if (stopping === cleanup) stopping = null; });
    stopping = cleanup;
    return cleanup;
  }
  return {
    start, stop, acceptEvents,
    mute() { return controller?.mute(); },
    enableSpeaker() { return controller?.enableSpeaker(); },
    retry() { if (session?.uncertain) return submit(session.uncertain); },
    suppressPlayback(roomId, event) { return ownSessions.get(event?.data?.voice_session_id) === roomId; },
    snapshot() { return {...lastState, voiceSessionId: session?.id || null}; },
    dispose() { disposed = true; return stop({reason: 'Live microphone off. Workspace closed.'}); },
  };
}
