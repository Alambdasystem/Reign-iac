import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const code = fs.readFileSync(new URL('../services/ax/team-voice.js', import.meta.url), 'utf8').replace(/^import .*;$/gm, '');
const {createTeamVoice} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const room = '123e4567-e89b-42d3-a456-426614174001';
const connection = '123e4567-e89b-42d3-a456-426614174002';
function harness({handle} = {}) {
  const requests = [], states = [], accepted = [], timers = new Map();
  let sequence = 10, timer = 0, config, controllerActive = false, starts = 0, stops = 0, muted = false;
  const uuid = () => '123e4567-e89b-42d3-a456-' + String(++sequence).padStart(12, '0');
  const voice = createTeamVoice({uuid, onState: value => states.push(value), onAccepted: value => accepted.push(value),
    timers: {setTimeout(fn, delay) { const id = ++timer; timers.set(id, {fn, delay}); return id; }, clearTimeout: id => timers.delete(id)},
    api: async (path, method, body, options) => {
      const request = {path, method, body, options}; requests.push(request);
      const custom = await handle?.(request); if (custom !== undefined) return custom;
      if (path.endsWith('/presence')) return {presence: []};
      if (path.endsWith('/voice')) return {configured: true, model: 'synthetic-realtime', transport: 'webrtc', max_duration_seconds: 3600};
      if (path.endsWith('/voice/offer')) return {sdp: 'v=0\nsynthetic-answer', voice_session_id: body.client_session_id};
      if (method === 'DELETE') return {closed: true};
      if (path.endsWith('/turns')) return {event: {type: 'message', data: {text: body.text}}, run_id: 'run-' + body.client_message_id};
      throw Error('Unexpected room request');
    },
    createVoiceController(value) {
      config = value;
      return {
        async start() {
          starts++; controllerActive = true; config.onState({active: true, phase: 'checking', muted});
          try {
            await config.api('/api/voice', 'GET');
            if (!controllerActive) return;
            await config.api('/api/voice/offer', 'POST', {sdp: 'v=0\nsynthetic-offer'});
            if (controllerActive) config.onState({active: true, phase: 'listening', muted, status: 'Microphone on.'});
          } catch (error) { if (controllerActive) { controllerActive = false; config.onState({active: false, phase: 'failed', status: error.message}); } }
        },
        stop(options = {}) { stops++; controllerActive = false; config.onState({active: false, phase: 'ended', status: options.reason || 'Microphone off.'}); return Promise.resolve(); },
        mute() { muted = !muted; config.onState({active: controllerActive, phase: 'listening', muted}); return muted; },
        enableSpeaker() { return Promise.resolve(true); },
      };
    }});
  const assistant = (request, text = 'A room answer.', expression = 'warm') => ({type: 'assistant_message', data: {
    run_id: 'run-' + request.body.client_message_id, text,
    voice_session_id: requests.find(item => item.path.endsWith('/voice/offer')).body.client_session_id,
    segments: [{text, expression}]}});
  return {voice, requests, states, accepted, timers, assistant, get config() { return config; },
    get starts() { return starts; }, get stops() { return stops; }, get active() { return controllerActive; }};
}

test('one persistent voice connection handles multiple room turns with matching expression plans', async () => {
  const h = harness(); await h.voice.start({roomId: room, connectionId: connection});
  assert.equal(h.config.connectionCheck, false); assert.equal(h.requests[0].path, '/api/team/rooms/' + room + '/presence');
  assert.equal(h.requests[0].body.in_call, false);
  for (const call of ['first', 'second']) {
    const reply = h.config.runTurn('A spoken question.', {voiceCallId: call}); await tick();
    const request = h.requests.at(-1), event = h.assistant(request);
    h.voice.acceptEvents(room, [event]);
    const result = await reply;
    assert.equal(result.answer, 'A room answer.'); assert.equal(result.thread_id, room);
    assert.equal(result.speech.text, result.answer); assert.equal(result.speech.run_id, result.run_id);
    assert.equal(result.speech.segments[0].expression, 'warm');
    assert.equal(h.voice.suppressPlayback(room, event), true);
  }
  assert.equal(h.starts, 1); assert.equal(h.active, true);
  assert.equal(h.requests.filter(item => item.path.endsWith('/voice/offer')).length, 1);
  assert.ok(h.requests.every(item => item.path.startsWith('/api/team/rooms/' + room + '/')));
  assert.ok(h.requests.every(item => !item.path.includes('/transcriptions')));
  h.voice.mute(); assert.equal(h.states.at(-1).muted, true); h.voice.mute();
  assert.equal(h.states.at(-1).muted, false); assert.equal(h.starts, 1);
  await h.voice.stop(); assert.equal(h.active, false); assert.equal(h.timers.size, 0);
});

test('Off revokes a pending offer ID and a late provider answer cannot revive it', async () => {
  const offer = deferred(), h = harness({handle: request => request.path.endsWith('/voice/offer') ? offer.promise : undefined});
  const starting = h.voice.start({roomId: room, connectionId: connection}); await tick();
  const sent = h.requests.find(request => request.path.endsWith('/voice/offer')); assert.ok(sent);
  await h.voice.stop();
  assert.equal(h.active, false); assert.ok(h.requests.some(request => request.method === 'DELETE' && request.path.endsWith(sent.body.client_session_id)));
  offer.resolve({sdp: 'v=0\nlate-answer', voice_session_id: sent.body.client_session_id}); await starting; await tick();
  assert.equal(h.active, false); assert.equal(h.starts, 1);
  assert.equal(h.requests.filter(request => request.method === 'DELETE').length, 2, 'late completion repeats only idempotent cleanup');
});

test('uncertain voice submission waits for explicit retry of its exact UUID and body', async () => {
  let attempts = 0;
  const h = harness({handle: request => { if (request.path.endsWith('/turns') && ++attempts === 1) throw Error('Connection lost'); }});
  await h.voice.start({roomId: room, connectionId: connection});
  const reply = h.config.runTurn('Do this once.', {voiceCallId: 'one'}); await tick();
  assert.equal(h.states.at(-1).uncertain, true); assert.equal(attempts, 1);
  await h.voice.retry();
  const turns = h.requests.filter(request => request.path.endsWith('/turns'));
  assert.equal(turns.length, 2); assert.deepEqual(turns[0].body, turns[1].body);
  h.voice.acceptEvents(room, [h.assistant(turns[1])]); await reply;
  assert.equal(h.states.at(-1).uncertain, false); await h.voice.stop();
});

test('a room event arriving before POST acknowledgement still completes the same live turn', async () => {
  const accepted = deferred(), h = harness({handle: request => request.path.endsWith('/turns') ? accepted.promise : undefined});
  await h.voice.start({roomId: room, connectionId: connection});
  const reply = h.config.runTurn('Quick response.', {voiceCallId: 'quick'}); await tick();
  const request = h.requests.at(-1), event = h.assistant(request);
  h.voice.acceptEvents(room, [event]); accepted.resolve({run_id: event.data.run_id});
  assert.equal((await reply).answer, event.data.text); await h.voice.stop();
});

test('ending live voice rejects its own unfinished turn and suppresses its late cached audio only', async () => {
  const h = harness(); await h.voice.start({roomId: room, connectionId: connection});
  const reply = h.config.runTurn('Pending request.', {voiceCallId: 'pending'}); const rejected = assert.rejects(reply, {name: 'AbortError'}); await tick();
  const event = h.assistant(h.requests.at(-1)); await h.voice.stop(); await rejected;
  assert.equal(h.voice.suppressPlayback(room, event), true);
  assert.equal(h.voice.suppressPlayback('another-room', event), false);
  assert.equal(h.voice.suppressPlayback(room, {...event, data: {...event.data, voice_session_id: 'another-session'}}), false);
  assert.ok(h.requests.filter(request => request.method === 'DELETE').every(request => request.path.includes('/voice/sessions/')));
  assert.equal(h.timers.size, 0);
});

test('an expired server voice lease shuts down the live connection instead of continuing to submit', async () => {
  const h = harness({handle: request => { if (request.path.endsWith('/turns')) throw Object.assign(Error('Expired'), {status: 410, code: 'team_voice_closed'}); }});
  await h.voice.start({roomId: room, connectionId: connection});
  await assert.rejects(h.config.runTurn('After expiry.', {voiceCallId: 'expired'}), /Expired/); await tick();
  assert.equal(h.active, false); assert.ok(h.stops > 0);
  assert.ok(h.requests.some(request => request.method === 'DELETE'));
});

test('Off then immediate On waits for the old lease close before negotiating another session', async () => {
  const closing = deferred(); let held = true;
  const h = harness({handle: request => request.method === 'DELETE' && held ? closing.promise : undefined});
  await h.voice.start({roomId: room, connectionId: connection});
  const first = h.requests.find(request => request.path.endsWith('/voice/offer')).body.client_session_id;
  const stop = h.voice.stop(), restart = h.voice.start({roomId: room, connectionId: connection}); await tick();
  assert.equal(h.active, false); assert.equal(h.starts, 1);
  held = false; closing.resolve({closed: true}); await stop; await restart;
  assert.equal(h.starts, 2); assert.equal(h.active, true);
  const offers = h.requests.filter(request => request.path.endsWith('/voice/offer'));
  assert.notEqual(offers[1].body.client_session_id, first);
  assert.ok(h.requests.filter(request => request.method === 'DELETE').every(request => request.path.endsWith(first)));
  await h.voice.stop();
});

test('Off while a restart waits cancels that pending intent instead of opening a microphone later', async () => {
  const closing = deferred(); let held = true;
  const h = harness({handle: request => request.method === 'DELETE' && held ? closing.promise : undefined});
  await h.voice.start({roomId: room, connectionId: connection});
  const stopped = h.voice.stop(), restarting = h.voice.start({roomId: room, connectionId: connection});
  const secondStop = h.voice.stop(); held = false; closing.resolve({closed: true});
  await Promise.all([stopped, restarting, secondStop]); assert.equal(h.starts, 1); assert.equal(h.active, false);
});

test('a still-closing provider reservation leaves the microphone off until an explicit retry', async () => {
  const opening = deferred(); let offers = 0, closing = true;
  const h = harness({handle: request => {
    if (!request.path.endsWith('/voice/offer')) return;
    if (++offers === 1) return opening.promise;
    if (closing) throw Object.assign(Error('Previous connection is closing.'), {status: 409, code: 'team_voice_closing'});
  }});
  const first = h.voice.start({roomId: room, connectionId: connection}); await tick();
  const firstId = h.requests.find(request => request.path.endsWith('/voice/offer')).body.client_session_id;
  await h.voice.stop(); await h.voice.start({roomId: room, connectionId: connection}); await tick();
  assert.equal(h.active, false); assert.equal(h.states.at(-1).phase, 'failed');
  assert.match(h.states.at(-1).status, /Microphone is off.*still closing.*Try Microphone on again in a moment/);
  assert.equal(h.starts, 2); assert.equal(offers, 2); assert.equal(h.timers.size, 0);
  opening.resolve({sdp: 'v=0\nlate-answer', voice_session_id: firstId}); await first; closing = false; await tick();
  assert.equal(h.active, false); assert.equal(offers, 2, 'late cleanup must not automatically reopen audio');
  assert.ok(h.requests.every(request => !request.path.endsWith('/turns')));
  await h.voice.start({roomId: room, connectionId: connection});
  assert.equal(h.active, true); assert.equal(offers, 3); await h.voice.stop();
});

test('matching run ID without this voice session provenance cannot authorize narration', async () => {
  const h = harness(); await h.voice.start({roomId: room, connectionId: connection});
  const reply = h.config.runTurn('Question.', {voiceCallId: 'bound'}); await tick();
  const event = h.assistant(h.requests.at(-1)); let finished = false; void reply.then(() => { finished = true; });
  h.voice.acceptEvents(room, [{...event, data: {...event.data, voice_session_id: 'different-session'}}]); await tick();
  assert.equal(finished, false);
  h.voice.acceptEvents(room, [event]); await reply; assert.equal(finished, true); await h.voice.stop();
});

test('room voice capability can be warmed without microphone capture or a provider offer', async () => {
  const h = harness();
  assert.equal(await h.voice.prepare({roomId: room, connectionId: connection}), true);
  assert.equal(h.starts, 0);
  assert.equal(h.requests.filter(request => request.path.endsWith('/voice/offer')).length, 0);
  assert.equal(h.requests.filter(request => request.path.endsWith('/voice')).length, 1);
  await h.voice.start({roomId: room, connectionId: connection});
  assert.equal(h.starts, 1);
  assert.equal(h.requests.filter(request => request.path.endsWith('/voice')).length, 1, 'reuse the primed capability');
  assert.equal(h.requests.filter(request => request.path.endsWith('/voice/offer')).length, 1);
  await h.voice.dispose();
});

test('preparing voice never auto-starts an audible session or microphone', async () => {
  const h=harness();
  await h.voice.prepare({roomId:room,connectionId:connection});
  assert.equal(h.states.some(s=>s.active),false);
  assert.equal(h.starts,0);
  await h.voice.dispose();
});
