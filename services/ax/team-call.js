/* Small, explicit-consent room calls. This module owns media, not room history.
 * A room resume never creates a controller session or requests a device.
 * Signaling stays behind the authenticated /api/team boundary. No owner tools,
 * provider credentials, recording, or automatic image uploads are used here.
 *
 * WebRTC transceiver/replaceTrack contract: https://www.w3.org/TR/webrtc/
 * Screen capture activation: https://www.w3.org/TR/screen-capture/
 */

const SLOTS = ['microphone', 'camera', 'screen'];
const MAX_PEERS = 3;
const MAX_ICE = 24;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ROOM = /^[A-Za-z0-9_-]{1,100}$/;

function mediaMessage(error, device) {
  if (error?.name === 'NotAllowedError') return `${device} permission was denied. You can allow it in your browser settings and try again.`;
  if (error?.name === 'NotFoundError') return `No ${device.toLowerCase()} was found. Connect or enable a device, then try again.`;
  if (error?.name === 'NotReadableError') return `${device} is busy or unavailable. Close the other app using it and try again.`;
  if (error?.name === 'InvalidStateError') return 'Choose Share screen directly from its button while this page is active.';
  return `${device} could not start. Check the device and browser permissions, then try again.`;
}

function validatedConfig(value) {
  if (!value || !Array.isArray(value.ice_servers) || value.ice_servers.length > 8) throw new Error('invalid_call_config');
  const iceServers = value.ice_servers.map(server => {
    const urls = typeof server?.urls === 'string' ? [server.urls] : server?.urls;
    if (!Array.isArray(urls) || !urls.length || urls.length > 8 ||
        urls.some(url => typeof url !== 'string' || url.length > 2000 || !/^(stun|stuns|turn|turns):[^\s]+$/.test(url))) {
      throw new Error('invalid_call_config');
    }
    const result = {urls: [...urls]};
    for (const key of ['username', 'credential']) {
      if (server[key] !== undefined) {
        if (typeof server[key] !== 'string' || server[key].length > 1024) throw new Error('invalid_call_config');
        result[key] = server[key];
      }
    }
    return result;
  });
  return {iceServers, maxParticipants: Math.min(4, Math.max(2, Number(value.max_participants) || 4)),
    relayConfigured: value.relay_configured === true && iceServers.some(server => server.urls.some(url => /^turns?:/.test(url)))};
}

function description(kind, payload) {
  if (!payload || payload.type !== kind || typeof payload.sdp !== 'string' || payload.sdp.length > 16384 ||
      !payload.sdp.startsWith('v=0') || payload.sdp.includes('\0')) throw new Error('invalid_signal');
  const media = payload.sdp.split(/\r?\n/).filter(line => line.startsWith('m=')).map(line => line.split(' ')[0]);
  // The fixed audio/camera/screen layout bounds receiver allocation and avoids
  // camera toggles renegotiating or accidentally replacing a screen track.
  if (media.join(',') !== 'm=audio,m=video,m=video') throw new Error('invalid_signal');
  return {type: kind, sdp: payload.sdp};
}

function candidate(payload) {
  if (payload === null) return null;
  if (!payload || typeof payload.candidate !== 'string' || payload.candidate.length > 4096) throw new Error('invalid_signal');
  if (payload.sdpMid != null && (typeof payload.sdpMid !== 'string' || payload.sdpMid.length > 64)) throw new Error('invalid_signal');
  if (payload.sdpMLineIndex != null && (!Number.isInteger(payload.sdpMLineIndex) || payload.sdpMLineIndex < 0 || payload.sdpMLineIndex > 2)) throw new Error('invalid_signal');
  if (payload.usernameFragment != null && (typeof payload.usernameFragment !== 'string' || payload.usernameFragment.length > 256)) throw new Error('invalid_signal');
  return {candidate: payload.candidate, sdpMid: payload.sdpMid ?? null,
    sdpMLineIndex: payload.sdpMLineIndex ?? null, usernameFragment: payload.usernameFragment ?? null};
}

/**
 * createTeamCall({api,me,onState,onStreams,onPresence})
 * api(path, method, body, {signal,keepalive}) returns parsed authenticated JSON;
 * its errors should expose .status for membership/session revocation handling.
 *
 * join({roomId}), leave({reason}), setMicrophone(boolean), setCamera(boolean),
 * setScreen(boolean), syncPresence({roomId,presence}), snapshot(), dispose().
 * Call setScreen(true) directly from a user gesture, before unrelated awaits.
 * onStreams: {local:{microphone,camera,screen},remote:[{connectionId,userId,
 *   displayName,audio,camera,screen,state,microphoneEnabled,cameraEnabled,screenEnabled}]}
 * Each media field is a MediaStream or null. The UI owns elements and playback
 * permission recovery. Local video must be muted to prevent feedback.
 * captureFrame({connectionId?,source:'camera'|'screen'}) returns a Blob and
 * source metadata from an already-enabled track; it never starts or uploads media.
 */
export function createTeamCall(deps = {}) {
  if (typeof deps.api !== 'function') throw new Error('A team API adapter is required.');
  const scope = typeof globalThis !== 'undefined' ? globalThis : {};
  const devices = deps.mediaDevices || scope.navigator?.mediaDevices;
  const Peer = deps.Peer || scope.RTCPeerConnection;
  const Stream = deps.MediaStream || scope.MediaStream;
  const Abort = deps.AbortController || scope.AbortController;
  const timers = deps.timers || scope;
  const later = timers.setTimeout.bind(timers), cancel = timers.clearTimeout.bind(timers);
  const makeId = deps.makeId || (() => scope.crypto.randomUUID());
  const doc = deps.document || scope.document;
  let session = null, epoch = 0, disposed = false, joining = null, leaving = null, capturing = false;
  let state = {phase: 'idle', active: false, roomId: null, connectionId: null,
    microphone: false, camera: false, screen: false, cameraPending: false, screenPending: false,
    relayConfigured: false, participants: 0, status: 'Join call when you are ready. Your microphone and camera are off.'};
  const snapshot = () => ({...state});
  const live = s => session === s && s.epoch === epoch && !s.abort.signal.aborted;
  const statusCode = error => Number(error?.status || error?.status_code || 0);
  const connectionGone = error => error?.code === 'team_connection_required';
  const forbidden = error => statusCode(error) === 401 || (statusCode(error) === 403 && !connectionGone(error));
  const safe = (callback, value) => { try { callback?.(value); } catch { /* UI callbacks do not own media cleanup. */ } };
  const update = change => { state = {...state, ...change}; safe(deps.onState, snapshot()); };
  const stopped = () => Object.assign(new Error('Call ended.'), {name: 'AbortError'});
  const base = s => `/api/team/rooms/${encodeURIComponent(s.roomId)}`;

  function request(s, path, method = 'GET', body, {timeout = 8000, detached = false, keepalive = false} = {}) {
    const controller = new Abort();
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (fn, value) => {
        if (done) return;
        done = true; cancel(timer);
        if (!detached) s.abort.signal.removeEventListener('abort', abort);
        fn(value);
      };
      const abort = () => { controller.abort(); finish(reject, stopped()); };
      const timer = later(() => { controller.abort(); finish(reject, new Error('call_request_timeout')); }, timeout);
      if (!detached && s.abort.signal.aborted) { abort(); return; }
      if (!detached) s.abort.signal.addEventListener('abort', abort, {once: true});
      try {
        Promise.resolve(deps.api(path, method, body, {signal: controller.signal, keepalive})).then(
          value => finish(resolve, value), error => finish(reject, error));
      } catch (error) { finish(reject, error); }
    });
  }

  function stopStream(stream) {
    for (const track of stream?.getTracks() || []) {
      track.onended = null;
      try { track.stop(); } catch { /* Already ended. */ }
    }
  }
  function acquireMedia(s, permission, signal) {
    // Browser device prompts are not abortable. Settle our waiter on leave, and
    // stop any stream that the browser grants after consent was withdrawn.
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (fn, value) => {
        if (done) return;
        done = true;
        s.abort.signal.removeEventListener('abort', aborted);
        signal?.removeEventListener('abort', aborted);
        fn(value);
      };
      const aborted = () => finish(reject, stopped());
      s.abort.signal.addEventListener('abort', aborted, {once: true});
      signal?.addEventListener('abort', aborted, {once: true});
      if (!live(s) || signal?.aborted) aborted();
      Promise.resolve(permission).then(stream => {
        if (done || !live(s) || signal?.aborted) { stopStream(stream); return; }
        finish(resolve, stream);
      }, error => finish(reject, error));
    });
  }
  function publishStreams(s) {
    if (!live(s)) return;
    safe(deps.onStreams, {local: {...s.local}, remote: [...s.peers.values()].map(p => ({
      connectionId: p.id, userId: p.person.user_id, displayName: p.person.display_name,
      ...p.remote, state: p.state, microphoneEnabled: p.person.microphone,
      cameraEnabled: p.person.camera, screenEnabled: p.person.screen,
    }))});
  }
  function callStatus(s, notice) {
    if (!live(s) || !s.joined) return;
    const peers = [...s.peers.values()], connected = peers.filter(p => p.state === 'connected').length;
    const failed = peers.some(p => p.state === 'failed');
    const phase = failed ? 'degraded' : connected ? 'connected' : peers.length ? 'connecting' : 'waiting';
    const summary = notice || (failed ? 'A participant could not connect. Leave and rejoin the call to retry.' :
      connected ? `Connected to ${connected} participant${connected === 1 ? '' : 's'}.` :
        peers.length ? 'Connecting to the other participants…' : 'Joined call. Waiting for another participant.');
    update({phase, participants: peers.length + 1,
      status: summary + (s.config.relayConfigured ? '' : ' No relay is configured; direct calls may fail on some networks.')});
  }
  function closePeer(p, stateName = 'closed') {
    p.state = stateName; cancel(p.connectTimer); cancel(p.disconnectTimer);
    p.pc.onicecandidate = p.pc.ontrack = p.pc.onconnectionstatechange = p.pc.oniceconnectionstatechange = null;
    try { p.pc.close(); } catch { /* Idempotent cleanup. */ }
    for (const stream of Object.values(p.remote)) stopStream(stream);
    p.remote = {audio: null, camera: null, screen: null};
    p.remoteIce.length = p.localIce.length = 0;
  }
  function peerFailed(s, p, error) {
    if (!live(s) || p.state === 'closed' || p.state === 'failed') return;
    if (forbidden(error)) { void leave({reason: 'Your workspace access changed. Media has stopped.', failed: true}); return; }
    closePeer(p, 'failed'); publishStreams(s); callStatus(s);
  }
  function serial(s, p, action) {
    const work = p.queue.then(async () => {
      if (!live(s) || ['failed', 'closed'].includes(p.state)) return;
      return action();
    });
    p.queue = work.catch(error => peerFailed(s, p, error));
    return work;
  }
  function sendSignal(s, p, kind, payload) {
    if (++p.pendingSignals > MAX_ICE + 2) { p.pendingSignals--; return Promise.reject(new Error('signal_queue_full')); }
    const work = p.outgoing.then(() => {
      if (!live(s) || ['failed', 'closed'].includes(p.state)) return;
      return request(s, `${base(s)}/signals`, 'POST', {connection_id: s.id, to: p.id, kind, payload});
    });
    p.outgoing = work.catch(error => peerFailed(s, p, error)).finally(() => p.pendingSignals--);
    return work;
  }
  function flushLocalIce(s, p) {
    p.descriptionSent = true;
    for (const item of p.localIce.splice(0)) void sendSignal(s, p, 'ice', item).catch(() => {});
  }
  function trackFor(s, slot) { return s.local[slot]?.getTracks()[0] || null; }
  async function bindAnswerSlots(s, p) {
    const transceivers = p.pc.getTransceivers();
    if (transceivers.length !== 3) throw new Error('invalid_media_layout');
    p.slots = Object.fromEntries(SLOTS.map((slot, index) => [slot, transceivers[index]]));
    for (const slot of SLOTS) {
      p.slots[slot].direction = 'sendrecv';
      await p.slots[slot].sender.replaceTrack(trackFor(s, slot));
    }
  }
  function addPeer(s, person) {
    if (!live(s) || s.peers.has(person.connection_id) || s.peers.size >= MAX_PEERS) return;
    const pc = new Peer({iceServers: s.config.iceServers, bundlePolicy: 'max-bundle'});
    const p = {id: person.connection_id, person, pc, slots: {}, state: 'connecting',
      queue: Promise.resolve(), outgoing: Promise.resolve(), pendingSignals: 0,
      remoteIce: [], localIce: [], iceSent: 0, descriptionSent: false,
      remote: {audio: null, camera: null, screen: null}};
    s.peers.set(p.id, p);
    p.connectTimer = later(() => { if (p.state !== 'connected') peerFailed(s, p); }, 25000);
    pc.onicecandidate = event => {
      if (!live(s) || ['failed', 'closed'].includes(p.state)) return;
      if (++p.iceSent > MAX_ICE) { peerFailed(s, p); return; }
      let value;
      try { value = event.candidate ? candidate(event.candidate.toJSON ? event.candidate.toJSON() : event.candidate) : null; }
      catch (error) { peerFailed(s, p, error); return; }
      if (!p.descriptionSent) p.localIce.push(value);
      else void sendSignal(s, p, 'ice', value).catch(() => {});
    };
    pc.ontrack = event => {
      if (!live(s) || ['failed', 'closed'].includes(p.state)) { event.track.stop(); return; }
      const index = pc.getTransceivers().indexOf(event.transceiver);
      const slot = index === 0 ? 'audio' : index === 1 ? 'camera' : index === 2 ? 'screen' : null;
      if (!slot) { event.track.stop(); return; }
      if (p.remote[slot]?.getTracks().includes(event.track)) return;
      stopStream(p.remote[slot]); p.remote[slot] = new Stream([event.track]);
      event.track.onended = () => { if (live(s)) { p.remote[slot] = null; publishStreams(s); } };
      publishStreams(s);
    };
    const connectionChanged = () => {
      if (!live(s) || ['closed', 'failed'].includes(p.state)) return;
      const value = pc.connectionState || pc.iceConnectionState;
      if (value === 'connected' || value === 'completed') {
        p.state = 'connected'; cancel(p.connectTimer); cancel(p.disconnectTimer); p.disconnectTimer = null;
      } else if (value === 'failed' || value === 'closed' || pc.iceConnectionState === 'failed') {
        peerFailed(s, p); return;
      } else if (value === 'disconnected' && !p.disconnectTimer) {
        p.state = 'connecting'; p.disconnectTimer = later(() => peerFailed(s, p), 8000);
      }
      publishStreams(s); callStatus(s);
    };
    pc.onconnectionstatechange = pc.oniceconnectionstatechange = connectionChanged;
    if (s.id < p.id) {
      // Only the deterministic offerer creates slots. Creating addTransceiver
      // slots on an answerer before setRemoteDescription duplicates m-lines.
      void serial(s, p, async () => {
        for (const slot of SLOTS) p.slots[slot] = pc.addTransceiver(slot === 'microphone' ? 'audio' : 'video', {direction: 'sendrecv'});
        for (const slot of SLOTS) await p.slots[slot].sender.replaceTrack(trackFor(s, slot));
        await pc.setLocalDescription(await pc.createOffer());
        await sendSignal(s, p, 'offer', description('offer', pc.localDescription));
        flushLocalIce(s, p);
      }).catch(() => {});
    }
  }
  function applyPresence(s, rows, notify = true) {
    if (!live(s) || !s.joined || !Array.isArray(rows)) return;
    const people = new Map();
    for (const row of rows.slice(0, 16)) {
      if (row?.in_call !== true || !UUID.test(row.connection_id || '')) continue;
      people.set(row.connection_id, {connection_id: row.connection_id, user_id: row.user_id,
        display_name: String(row.display_name || 'Participant').slice(0, 80),
        microphone: row.microphone === true, camera: row.camera === true, screen: row.screen === true});
    }
    if (people.size > s.config.maxParticipants) {
      void leave({reason: 'This call has reached its participant limit. Media has stopped.', failed: true}); return;
    }
    s.presence = people;
    for (const [id, p] of s.peers) {
      if (!people.has(id)) { closePeer(p); s.peers.delete(id); }
      else p.person = people.get(id);
    }
    try { for (const [id, person] of people) if (id !== s.id) addPeer(s, person); }
    catch { void leave({reason: 'This browser could not create a media connection. Media has stopped.', failed: true}); return; }
    if (notify) safe(deps.onPresence, rows);
    publishStreams(s); callStatus(s);
  }
  function presenceBody(s) {
    return {connection_id: s.id, in_call: true, microphone: state.microphone,
      camera: !!s.local.camera, screen: !!s.local.screen};
  }
  function scheduleHeartbeat(s, delay = 8000) {
    cancel(s.heartbeatTimer);
    if (live(s) && s.joined) s.heartbeatTimer = later(() => void heartbeat(s), delay);
  }
  function heartbeat(s) {
    if (!live(s) || !s.joined) return Promise.resolve();
    if (s.heartbeat) { s.heartbeatAgain = true; return s.heartbeat; }
    cancel(s.heartbeatTimer);
    s.heartbeat = request(s, `${base(s)}/presence`, 'POST', presenceBody(s)).then(result => {
      if (live(s)) { s.heartbeatFailures = 0; applyPresence(s, result.presence); }
    }).catch(error => {
      if (!live(s)) return;
      if (forbidden(error) || connectionGone(error) || ++s.heartbeatFailures >= 2) {
        void leave({reason: forbidden(error) ? 'Your workspace access changed. Media has stopped.' :
          'Room connection was lost. Media has stopped; join again when your network recovers.', failed: true});
      } else callStatus(s, 'Room connection is interrupted. Checking whether you can stay in this call…');
    }).finally(() => {
      s.heartbeat = null;
      const again = s.heartbeatAgain; s.heartbeatAgain = false;
      scheduleHeartbeat(s, again ? 0 : 8000);
    });
    return s.heartbeat;
  }
  async function receiveSignal(s, signal) {
    if (!live(s) || !UUID.test(signal?.from || '') || signal.from === s.id) return;
    if (!s.presence.has(signal.from)) await heartbeat(s);
    if (!live(s) || !s.presence.has(signal.from)) return;
    const p = s.peers.get(signal.from);
    if (!p || ['failed', 'closed'].includes(p.state)) return;
    await serial(s, p, async () => {
      if (signal.kind === 'ice') {
        const item = candidate(signal.payload);
        if (p.pc.remoteDescription) await p.pc.addIceCandidate(item);
        else {
          if (p.remoteIce.length >= MAX_ICE) throw new Error('ice_queue_full');
          p.remoteIce.push(item);
        }
      } else if (signal.kind === 'offer') {
        if (s.id < p.id || p.pc.remoteDescription) throw new Error('unexpected_offer');
        await p.pc.setRemoteDescription(description('offer', signal.payload));
        await bindAnswerSlots(s, p);
        for (const item of p.remoteIce.splice(0)) await p.pc.addIceCandidate(item);
        await p.pc.setLocalDescription(await p.pc.createAnswer());
        await sendSignal(s, p, 'answer', description('answer', p.pc.localDescription));
        flushLocalIce(s, p);
      } else if (signal.kind === 'answer') {
        if (s.id > p.id || p.pc.remoteDescription || p.pc.localDescription?.type !== 'offer') throw new Error('unexpected_answer');
        await p.pc.setRemoteDescription(description('answer', signal.payload));
        for (const item of p.remoteIce.splice(0)) await p.pc.addIceCandidate(item);
      } else throw new Error('invalid_signal');
    }).catch(() => {});
  }
  async function poll(s) {
    if (!live(s)) return;
    try {
      const value = await request(s, `${base(s)}/signals?connection_id=${encodeURIComponent(s.id)}&after=${s.lastSignal}`);
      if (!live(s)) return;
      if (!Array.isArray(value.signals) || value.signals.length > 128) throw new Error('invalid_signals');
      for (const signal of value.signals) {
        if (!Number.isSafeInteger(signal.seq) || signal.seq <= s.lastSignal) continue;
        await receiveSignal(s, signal);
        if (!live(s)) return;
        s.lastSignal = signal.seq;
      }
      if (Number.isSafeInteger(value.last_seq)) s.lastSignal = Math.max(s.lastSignal, value.last_seq);
      s.signalFailures = 0;
    } catch (error) {
      if (!live(s)) return;
      if (forbidden(error)) { void leave({reason: 'Your workspace access changed. Media has stopped.', failed: true}); return; }
      if (connectionGone(error)) { void leave({reason: 'Your call connection expired. Media has stopped; join the call again to continue.', failed: true}); return; }
      s.signalFailures++;
      callStatus(s, 'Call signaling is interrupted. Existing audio may continue; checking the room connection…');
      if (s.signalFailures >= 3) { void leave({reason: 'Call signaling was lost. Media has stopped. Join again to retry.', failed: true}); return; }
    } finally {
      if (live(s)) s.pollTimer = later(() => void poll(s), s.signalFailures ? 1500 : 700);
    }
  }

  function join({roomId} = {}) {
    if (disposed) return Promise.resolve(snapshot());
    if (session?.roomId === roomId) return joining?.session === session ? joining.promise : Promise.resolve(snapshot());
    const preceding = leave();
    if (!ROOM.test(roomId || '')) {
      update({phase: 'failed', status: 'Choose a saved room before joining a call.'});
      return Promise.resolve(snapshot());
    }
    const s = {epoch: ++epoch, id: null, roomId, abort: new Abort(), joined: false,
      registrationStarted: false, local: {microphone: null, camera: null, screen: null},
      peers: new Map(), presence: new Map(), mediaEpoch: {camera: 0, screen: 0}, mediaPending: {}, mediaAbort: {},
      lastSignal: 0, signalFailures: 0, heartbeatFailures: 0};
    // Reserve the new session before any await, so room-switch/leave cancels a
    // join that is still waiting for the previous call's DELETE to finish.
    session = s;
    update({phase: 'joining', active: true, roomId, connectionId: null, participants: 0,
        microphone: false, camera: false, screen: false, cameraPending: false, screenPending: false,
        status: 'Checking room call access…'});
    const work = (async () => {
      try {
        await preceding;
        if (!live(s)) return snapshot();
        s.id = makeId();
        update({connectionId: s.id});
        if (!UUID.test(s.id) || !devices?.getUserMedia || !Peer || !Stream) throw new Error('unsupported_browser');
        s.config = validatedConfig(await request(s, '/api/team/call-config'));
        if (!live(s)) return snapshot();
        update({relayConfigured: s.config.relayConfigured, status: 'Allow microphone access to join this call.'});
        const permission = devices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false});
        let microphone;
        try { microphone = await acquireMedia(s, permission); } catch (error) { error.callMedia = 'Microphone'; throw error; }
        if (!live(s)) { stopStream(microphone); return snapshot(); }
        const track = microphone.getAudioTracks()[0];
        if (!track) { stopStream(microphone); throw new Error('missing_microphone'); }
        for (const other of microphone.getTracks()) if (other !== track) other.stop();
        s.local.microphone = new Stream([track]);
        track.onended = () => { if (live(s)) void leave({reason: 'Your microphone disconnected. Media has stopped.', failed: true}); };
        update({microphone: true, status: 'Joining the room call…'});
        s.registrationStarted = true;
        const registered = await request(s, `${base(s)}/presence`, 'POST', presenceBody(s));
        if (!live(s)) return snapshot();
        s.joined = true; applyPresence(s, registered.presence);
        scheduleHeartbeat(s); void poll(s); publishStreams(s); callStatus(s);
      } catch (error) {
        if (live(s)) {
          const reason = error.callMedia ? mediaMessage(error, error.callMedia) :
            statusCode(error) === 409 ? 'This call is full. Up to four participants can join.' :
              forbidden(error) ? 'Your session or workspace access changed. Sign in again to join.' :
                error.message === 'unsupported_browser' ? 'This browser does not support room calls. Use a current browser over HTTPS.' :
                  'The call could not start. Your media is off. Check your connection and try again.';
          await leave({reason, failed: true});
        }
      }
      return snapshot();
    })().finally(() => { if (joining?.session === s) joining = null; });
    joining = {session: s, promise: work};
    return work;
  }

  function setMicrophone(enabled) {
    const s = session;
    if (!s || !live(s) || !s.joined) return false;
    enabled = enabled === true;
    for (const track of s.local.microphone?.getAudioTracks() || []) track.enabled = enabled;
    update({microphone: enabled}); publishStreams(s); scheduleHeartbeat(s, 0);
    return enabled;
  }
  async function replaceSlot(s, slot, track) {
    await Promise.all([...s.peers.values()].map(p => serial(s, p, async () => {
      if (p.slots[slot]) await p.slots[slot].sender.replaceTrack(track);
    }).catch(() => {})));
  }
  function setVideo(slot, enabled) {
    const s = session;
    if (!s || !live(s) || !s.joined) return Promise.resolve(false);
    if (!enabled) {
      s.mediaEpoch[slot]++;
      s.mediaAbort[slot]?.abort(); s.mediaPending[slot] = null;
      stopStream(s.local[slot]); s.local[slot] = null;
      update({[slot]: false, [`${slot}Pending`]: false}); publishStreams(s); scheduleHeartbeat(s, 0);
      return replaceSlot(s, slot, null).then(() => false);
    }
    if (s.local[slot]) return Promise.resolve(true);
    if (s.mediaPending[slot]) return s.mediaPending[slot];
    const revision = ++s.mediaEpoch[slot], device = slot === 'screen' ? 'Screen sharing' : 'Camera';
    const abort = new Abort(); s.mediaAbort[slot] = abort;
    let permission;
    try {
      // getDisplayMedia must be invoked synchronously from the click handler.
      permission = slot === 'screen' ? devices.getDisplayMedia({video: {width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 10, max: 15}}, audio: false}) :
        devices.getUserMedia({video: {width: {ideal: 640, max: 1280}, height: {ideal: 360, max: 720}, frameRate: {ideal: 15, max: 20}}, audio: false});
    } catch (error) { update({status: mediaMessage(error, device)}); return Promise.resolve(false); }
    update({[`${slot}Pending`]: true});
    const pending = acquireMedia(s, permission, abort.signal).then(async stream => {
      if (!live(s) || revision !== s.mediaEpoch[slot]) { stopStream(stream); return false; }
      const track = stream.getVideoTracks()[0];
      if (!track) { stopStream(stream); throw new Error('missing_video'); }
      for (const other of stream.getTracks()) if (other !== track) other.stop();
      s.local[slot] = new Stream([track]);
      track.contentHint = slot === 'screen' ? 'detail' : 'motion';
      track.onended = () => { if (live(s)) void setVideo(slot, false); };
      update({[slot]: true}); publishStreams(s);
      await replaceSlot(s, slot, track);
      if (!live(s) || revision !== s.mediaEpoch[slot]) return false;
      scheduleHeartbeat(s, 0); return true;
    }).catch(error => {
      if (live(s) && revision === s.mediaEpoch[slot]) update({status: mediaMessage(error, device)});
      return false;
    }).finally(() => {
      if (s.mediaPending[slot] === pending) s.mediaPending[slot] = null;
      if (live(s) && revision === s.mediaEpoch[slot]) update({[`${slot}Pending`]: false});
    });
    s.mediaPending[slot] = pending;
    return pending;
  }

  function leave({reason = 'You left the call. Your microphone, camera and screen sharing are off.', failed = false} = {}) {
    const s = session;
    if (!s) return leaving || Promise.resolve(snapshot());
    session = null; epoch++; s.abort.abort();
    cancel(s.heartbeatTimer); cancel(s.pollTimer);
    s.mediaEpoch.camera++; s.mediaEpoch.screen++;
    for (const stream of Object.values(s.local)) stopStream(stream);
    s.local = {microphone: null, camera: null, screen: null};
    for (const p of s.peers.values()) closePeer(p);
    s.peers.clear(); s.presence.clear();
    safe(deps.onStreams, {local: {...s.local}, remote: []});
    update({phase: failed ? 'failed' : 'idle', active: false, roomId: null, connectionId: null,
      microphone: false, camera: false, screen: false, cameraPending: false, screenPending: false,
      participants: 0, status: reason});
    leaving = (s.registrationStarted ? request(s, `${base(s)}/presence/${encodeURIComponent(s.id)}`, 'DELETE', undefined,
      {timeout: 3000, detached: true, keepalive: true}).catch(() => {}) : Promise.resolve())
      .then(() => snapshot()).finally(() => { leaving = null; });
    return leaving;
  }

  async function captureFrame({connectionId, source} = {}) {
    const s = session;
    if (!s || !live(s) || !s.joined || !['camera', 'screen'].includes(source)) throw new Error('Choose an active camera or shared screen.');
    if (capturing) throw new Error('A snapshot is already being prepared.');
    if (connectionId && connectionId !== s.id) throw new Error('You can share a snapshot only from your own camera or screen.');
    const revision = s.mediaEpoch[source], track = s.local[source]?.getVideoTracks()[0];
    const current = () => live(s) && revision === s.mediaEpoch[source] && track?.readyState !== 'ended';
    if (!track || !current()) throw new Error('That camera or screen is not currently shared.');
    if (!doc?.createElement) throw new Error('Snapshot capture is not supported in this browser.');
    capturing = true;
    const video = doc.createElement('video');
    video.muted = true; video.playsInline = true; video.srcObject = new Stream([track]);
    let timeout, cancelSnapshot;
    try {
      await new Promise((resolve, reject) => {
        const ready = () => { if (video.readyState >= 2 && video.videoWidth) resolve(); };
        cancelSnapshot = () => reject(stopped());
        s.abort.signal.addEventListener('abort', cancelSnapshot, {once: true});
        video.onloadeddata = ready;
        timeout = later(() => reject(new Error('The selected video has no current frame. Try again after it starts playing.')), 5000);
        Promise.resolve(video.play()).then(ready, reject);
      });
      if (!current()) throw stopped();
      const scale = Math.min(1, 1024 / Math.max(video.videoWidth, video.videoHeight));
      const canvas = doc.createElement('canvas');
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .84));
      if (!blob || !current()) throw new Error('The snapshot could not be prepared.');
      const person = s.presence.get(s.id) || deps.me;
      return {blob, source, connectionId: s.id,
        userId: person?.user_id || person?.id || null, displayName: person?.display_name || 'You',
        capturedAt: new Date().toISOString(), width: canvas.width, height: canvas.height};
    } finally {
      cancel(timeout); s.abort.signal.removeEventListener('abort', cancelSnapshot);
      video.onloadeddata = null; video.pause(); video.srcObject = null; capturing = false;
    }
  }

  return {join, leave, setMicrophone, setCamera: enabled => setVideo('camera', enabled),
    setScreen: enabled => setVideo('screen', enabled), snapshot, captureFrame,
    syncPresence({roomId, presence} = {}) { if (session?.roomId === roomId) applyPresence(session, presence, false); },
    dispose() { disposed = true; return leave(); }};
}
