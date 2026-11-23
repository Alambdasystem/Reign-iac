'use strict';

// These tests use simulated WebRTC, media and provider events. They exercise
// lifecycle/concurrency contracts; they are not evidence of audible live calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const {createVoiceController} = require('../services/ax/voice.js');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
async function eventually(predicate, message) {
  for (let i = 0; i < 40; i++) {
    if (predicate()) return;
    await tick();
  }
  assert.ok(predicate(), message);
}

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(callback);
  }
  removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
  emit(name, data = {}) {
    const event = {target: this, type: name, ...data};
    this['on' + name]?.(event);
    for (const callback of [...(this.listeners.get(name) || [])]) callback(event);
  }
}

function track(id = 'microphone') {
  return {
    id, kind: 'audio', enabled: true, readyState: 'live', stops: 0,
    stop() { this.stops++; this.readyState = 'ended'; },
    getSettings() { return {sampleRate: 48000, channelCount: 1}; },
    addEventListener() {}, removeEventListener() {}
  };
}

function stream(...tracks) {
  return {getTracks: () => tracks, getAudioTracks: () => tracks.filter(t => t.kind === 'audio')};
}

class FakeChannel extends Events {
  constructor() { super(); this.readyState = 'connecting'; this.sent = []; this.closes = 0; }
  send(raw) {
    assert.equal(this.readyState, 'open', 'must not send on a closed event channel');
    this.sent.push(JSON.parse(raw));
  }
  close() { this.closes++; this.readyState = 'closed'; this.emit('close'); }
  receive(event) { this.emit('message', {data: JSON.stringify(event)}); }
}

class FakeAudio extends Events {
  constructor(clock) {
    super(); this.clock = clock; this.paused = true; this.srcObject = null; this.currentTime = 0;
    this.plays = 0; this.pauses = 0; this.blockNextPlay = false;
  }
  get currentTime() { return this.mediaTime + (this.paused || this.clockFrozen ? 0 : (this.clock.now() - this.anchor) / 1000); }
  set currentTime(value) { this.mediaTime = Number(value) || 0; this.anchor = this.clock.now(); }
  async play() {
    this.plays++;
    if (this.blockNextPlay) {
      this.blockNextPlay = false;
      throw Object.assign(new Error('Browser requires a playback gesture.'), {name: 'NotAllowedError'});
    }
    if (this.paused) this.anchor = this.clock.now();
    this.paused = false;
    this.emit('playing');
  }
  pause() { this.currentTime = this.currentTime; this.pauses++; this.paused = true; this.emit('pause'); }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
  load() {}
}

function fakeClock() {
  let time = 1000, nextId = 0;
  const scheduled = new Map();
  const schedule = (fn, delay, repeat) => {
    const id = ++nextId;
    scheduled.set(id, {fn, at: time + delay, delay, repeat});
    return id;
  };
  return {
    now: () => time,
    timers: {
      setTimeout: (fn, delay) => schedule(fn, delay, false),
      clearTimeout: id => scheduled.delete(id),
      setInterval: (fn, delay) => schedule(fn, delay, true),
      clearInterval: id => scheduled.delete(id)
    },
    advance(ms) {
      const target = time + ms;
      let iterations = 0;
      for (;;) {
        const due = [...scheduled.entries()].filter(([, task]) => task.at <= target)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        assert.ok(++iterations < 10000, 'fake timer loop must remain bounded');
        const [id, task] = due;
        time = task.at;
        if (task.repeat) task.at += task.delay;
        else scheduled.delete(id);
        task.fn();
      }
      time = target;
    },
    scheduled
  };
}

function harness(t, options = {}) {
  const clock = fakeClock(), microphone = track(), microphoneStream = stream(microphone);
  const peers = [], apiCalls = [], turnCalls = [], cancelled = [], states = [], captions = [], levels = [], outputLevels = [], outputStates = [];
  const audio = new FakeAudio(clock);
  let microphoneRequests = 0, speechStarts = 0;
  class FakePeer extends Events {
    constructor() {
      super(); this.connectionState = 'new'; this.iceConnectionState = 'new';
      this.iceGatheringState = 'complete'; this.tracks = []; this.closes = 0;
      this.channel = null; this.localDescription = null; this.remoteDescription = null;
      peers.push(this);
    }
    addTrack(input, media) { this.tracks.push({track: input, media}); }
    getSenders() { return this.tracks; }
    createDataChannel(label) { assert.equal(label, 'oai-events'); return this.channel = new FakeChannel(); }
    async createOffer() { return {type: 'offer', sdp: 'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'}; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription(value) { this.remoteDescription = value; }
    async getStats() { return new Map(); }
    close() { this.closes++; this.connectionState = 'closed'; }
  }
  const config = {configured: true, model: 'test-realtime-model', voice: 'marin', max_duration_seconds: 900};
  const controller = createVoiceController({
    connectionCheck: options.connectionCheck,
    onUserSpeechStart: () => { speechStarts++; options.onUserSpeechStart?.(); },
    api: async (path, method = 'GET', body, extra = {}) => {
      apiCalls.push({path, method, body, signal: extra.signal});
      if (options.api) return options.api(path, method, body, extra);
      if (path === '/api/voice' && method === 'GET') return {...config, ...options.config};
      if (path === '/api/voice/offer' && method === 'POST') {
        return {sdp: 'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n', type: 'answer',
          model: config.model, max_duration_seconds: config.max_duration_seconds};
      }
      throw new Error('Unexpected API request: ' + method + ' ' + path);
    },
    runTurn: async (message, context) => {
      turnCalls.push({message, context});
      return options.runTurn ? options.runTurn(message, context) : {answer: 'The verified task is complete.', status: 'completed'};
    },
    cancelTurn: async id => {
      cancelled.push(id);
      if (options.cancelTurn) return options.cancelTurn(id);
    },
    onState: state => states.push({...state}),
    onCaption: caption => captions.push({...caption}),
    onLevel: value => levels.push(value),
    onOutputLevel: (value, playback) => { outputLevels.push(value); outputStates.push({...playback, level:value}); },
    mediaDevices: {getUserMedia: async constraints => {
      microphoneRequests++;
      assert.ok(constraints.audio, 'must request an audio track');
      return options.getUserMedia ? options.getUserMedia(constraints) : microphoneStream;
    }},
    Peer: FakePeer, audio, AudioContext: options.AudioContext || null, timers: clock.timers, now: clock.now
  });
  const h = {
    controller, microphone, microphoneStream, peers, audio, apiCalls, turnCalls,
    cancelled, states, captions, clock, levels, outputLevels, outputStates,
    get microphoneRequests() { return microphoneRequests; },
    get speechStarts() { return speechStarts; },
    get peer() { return peers.at(-1); },
    get channel() { return peers.at(-1)?.channel; },
    async connect() {
      const pending = controller.start();
      await eventually(() => h.peer?.remoteDescription, 'must exchange an SDP offer');
      h.peer.connectionState = 'connected'; h.peer.iceConnectionState = 'connected';
      h.peer.emit('connectionstatechange');
      h.channel.readyState = 'open'; h.channel.emit('open');
      h.channel.receive({type: 'session.created', session: {id: 'test-session'}});
      await pending;
      if(options.connectionCheck!==false){
        h.channel.receive({type: 'response.created', response: {id: 'setup-response'}});
        h.channel.receive({type: 'response.done', response: {id: 'setup-response', status: 'completed', output: []}});
      }
      await settle();
      return h;
    },
    functionCall(id, message = 'Check the workspace status.', options = {}) {
      const responseId = options.responseId || 'response-' + id;
      if (options.created !== false) {
        h.channel.receive({type: 'response.created', response: {id: responseId}});
      }
      h.channel.receive({type: 'response.function_call_arguments.done',
        response_id: responseId, item_id: 'item-' + id,
        call_id: id, name: 'delegate_to_reign', arguments: JSON.stringify({message})});
      if (options.complete !== false) {
        h.channel.receive({type: 'response.done', response: {id: responseId, status: options.status || 'completed',
          output: [{type: 'function_call', call_id: id, name: 'delegate_to_reign', arguments: JSON.stringify({message})}]}});
      }
    }
  };
  t.after(async () => { await controller.stop({reason: 'Test cleanup'}); });
  return h;
}

function outputs(channel, callId) {
  return channel.sent.filter(event => event.type === 'conversation.item.create' &&
    event.item?.type === 'function_call_output' && (!callId || event.item.call_id === callId));
}

test('connects microphone and negotiates audio without exposing provider credentials', async t => {
  const h = await harness(t).connect();
  assert.equal(h.microphoneRequests, 1);
  assert.equal(h.peer.tracks[0].track, h.microphone);
  assert.equal(h.controller.snapshot().active, true);
  assert.equal(h.controller.snapshot().phase, 'listening');
  const offer = h.apiCalls.find(call => call.path === '/api/voice/offer');
  assert.deepEqual(Object.keys(offer.body), ['sdp']);
  assert.ok(offer.signal instanceof AbortSignal);
});

test('continuous room mode skips the generated greeting and keeps one microphone stream across speech turns', async t => {
  const h=await harness(t,{connectionCheck:false,config:{max_duration_seconds:3600}}).connect();
  assert.equal(h.channel.sent.some(event=>event.type==='conversation.item.create'||event.type==='response.create'),false);
  assert.equal(h.controller.snapshot().phase,'listening');
  h.clock.advance(21000);await settle();
  assert.equal(h.controller.snapshot().active,true,'there is no 20-second recording limit');
  assert.equal(h.microphone.stops,0);assert.equal(h.microphoneRequests,1);
  h.channel.receive({type:'input_audio_buffer.speech_started'});
  h.channel.receive({type:'input_audio_buffer.speech_stopped'});
  h.functionCall('continuous-first','First spoken question.');
  await eventually(()=>h.turnCalls.length===1&&narrations(h).length===1);
  const first=narrations(h)[0];
  h.channel.receive({type:'response.created',response:{id:'continuous-narration',metadata:first.response.metadata}});
  h.channel.receive({type:'input_audio_buffer.speech_started'});
  assert.equal(h.speechStarts,2,'real provider speech-start also stops any competing shared-room playback');
  assert.equal(h.audio.muted,true,'barge-in immediately closes REIGN output');
  h.channel.receive({type:'response.done',response:{id:'continuous-narration',status:'cancelled',output:[]}});
  h.channel.receive({type:'input_audio_buffer.speech_stopped'});
  h.functionCall('continuous-second','Second spoken question.');
  await eventually(()=>h.turnCalls.length===2);
  assert.equal(h.microphoneRequests,1);assert.equal(h.peers.length,1);
  assert.equal(h.peer.tracks[0].track,h.microphone);
  assert.equal(h.microphone.enabled,true);assert.equal(h.microphone.stops,0);
  assert.equal(h.turnCalls.every(item=>item.context.connectionCheck===false),true);
});

test('capability call duration honors the shared one-hour limit and reports its actual expiration', async t => {
  for(const configured of [3600,7200]){
    const h=await harness(t,{connectionCheck:false,config:{max_duration_seconds:configured}}).connect();
    const duration=[...h.clock.scheduled.values()].find(item=>!item.repeat&&item.delay===3600000);
    assert.ok(duration,'shared continuous voice must not inherit the private 15-minute timer');
    await duration.fn();
    assert.equal(h.controller.snapshot().active,false);
    assert.match(h.controller.snapshot().status,/60-minute voice session ended/);
    assert.equal(h.microphone.stops,1);
  }
});

test('the connection greeting must finish the REIGN graph before requesting speech', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise}).connect();
  const startup = h.channel.sent.filter(event => event.type === 'response.create');
  assert.equal(startup.length, 1);
  assert.deepEqual(startup[0].response.tool_choice, {type: 'function', name: 'delegate_to_reign'});
  assert.deepEqual(startup[0].response.output_modalities, ['text'],
    'forced delegation must not permit an unverified spoken preamble');
  assert.ok(h.channel.sent.some(event => event.type === 'conversation.item.create' &&
    event.item?.content?.some(content => /connection check/i.test(content.text || ''))));
  assert.equal(h.turnCalls.length, 0, 'connection signaling alone cannot claim the graph is ready');
  h.channel.receive({type: 'response.created', response: {
    id: 'connection-check-response', metadata: startup[0].response.metadata
  }});
  h.functionCall('connection-check', 'Greet me briefly and say you are ready to help. Do not run workspace actions.',
    {responseId: 'connection-check-response', created: false, complete: false});
  assert.equal(h.turnCalls.length, 0, 'partial provider function arguments must not run workspace code');
  h.channel.receive({type: 'response.done', response: {id: 'connection-check-response', status: 'completed'}});
  await eventually(() => h.turnCalls.length === 1, 'connection check must use the real graph interface');
  assert.equal(h.turnCalls[0].context.connectionCheck, true);
  assert.equal(h.channel.sent.filter(event => event.response?.tool_choice === 'none').length, 0);
  assert.equal(h.channel.sent.filter(event => event.response?.output_modalities?.includes('audio')).length, 0,
    'no speech response may be requested while the graph result is pending');
  graph.resolve({status: 'completed', answer: 'REIGN is ready to help you.'});
  await eventually(() => outputs(h.channel, 'connection-check').length === 1, 'verified greeting must return');
  assert.deepEqual(JSON.parse(outputs(h.channel, 'connection-check')[0].item.output),
    {status: 'completed', answer: 'REIGN is ready to help you.'});
  const narration = h.channel.sent.filter(event => event.response?.tool_choice === 'none');
  assert.equal(narration.length, 1);
  assert.deepEqual(narration[0].response.output_modalities, ['audio']);
  assert.ok(h.channel.sent.indexOf(outputs(h.channel, 'connection-check')[0]) < h.channel.sent.indexOf(narration[0]),
    'the verified function result must precede the audio response request');
});

test('delegates a spoken request, returns the verified result and requests narration without tools', async t => {
  const h = await harness(t).connect();
  h.channel.sent.length = 0;
  h.functionCall('call-a', 'Check Core, Temporal, and Computer.');
  await eventually(() => outputs(h.channel, 'call-a').length === 1, 'must return LangGraph result');
  assert.equal(h.turnCalls[0].message, 'Check Core, Temporal, and Computer.');
  assert.ok(h.turnCalls[0].context.signal instanceof AbortSignal);
  assert.ok(h.turnCalls[0].context.voiceCallId);
  const output = JSON.parse(outputs(h.channel, 'call-a')[0].item.output);
  assert.equal(output.answer, 'The verified task is complete.');
  const narration = h.channel.sent.find(event => event.type === 'response.create');
  assert.equal(narration.response.tool_choice, 'none');
  assert.deepEqual(narration.response.output_modalities, ['audio']);
});

test('does not rerun duplicate function arguments or subsequent item completion events', async t => {
  const h = await harness(t).connect();
  h.functionCall('call-duplicate', undefined, {complete: false});
  h.functionCall('call-duplicate', undefined, {created: false, complete: false});
  h.channel.receive({type: 'response.output_item.done', response_id: 'response-call-duplicate', item: {
    type: 'function_call', call_id: 'call-duplicate', name: 'delegate_to_reign',
    arguments: JSON.stringify({message: 'Check the workspace status.'})
  }});
  assert.equal(h.turnCalls.length, 0);
  const completion = {type: 'response.done', response: {id: 'response-call-duplicate', status: 'completed', output: [{
    type: 'function_call', call_id: 'call-duplicate', name: 'delegate_to_reign',
    arguments: JSON.stringify({message: 'Check the workspace status.'})
  }]}};
  h.channel.receive(completion);
  h.channel.receive(completion);
  await settle();
  assert.equal(h.turnCalls.length, 1, 'duplicate events must not rerun an action');
  assert.equal(outputs(h.channel, 'call-duplicate').length, 1);
});

test('serializes queued turns on the existing LangGraph conversation', async t => {
  const first = deferred();
  const h = await harness(t, {runTurn: message => message === 'First request'
    ? first.promise : {answer: 'Second result', status: 'completed'}}).connect();
  h.functionCall('first', 'First request');
  h.functionCall('second', 'Second request');
  await settle();
  assert.equal(h.turnCalls.length, 1);
  assert.equal(h.controller.snapshot().queueLength, 1);
  first.resolve({answer: 'First result', status: 'completed'});
  await eventually(() => h.turnCalls.length === 2, 'queued request must continue after the first run');
  await settle();
  assert.deepEqual(h.turnCalls.map(call => call.message), ['First request', 'Second request']);
  assert.equal(outputs(h.channel, 'first').length, 1);
  assert.equal(outputs(h.channel, 'second').length, 1);
});

test('microphone denial fails clearly without creating a voice connection', async t => {
  const h = harness(t, {getUserMedia: async () => {
    throw Object.assign(new Error('Permission denied'), {name: 'NotAllowedError'});
  }});
  await h.controller.start();
  assert.equal(h.controller.snapshot().active, false);
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.equal(h.apiCalls.filter(call => call.path === '/api/voice/offer').length, 0);
  assert.match(h.controller.snapshot().status, /microphone|permission/i);
});

test('a microphone permission result arriving after stop releases its tracks', async t => {
  const permission = deferred();
  const h = harness(t, {getUserMedia: () => permission.promise});
  const starting = h.controller.start();
  await eventually(() => h.microphoneRequests === 1, 'microphone permission should be pending');
  await h.controller.stop({reason: 'User ended the call'});
  permission.resolve(h.microphoneStream);
  await starting;
  await eventually(() => h.microphone.readyState === 'ended', 'late permission must release the acquired microphone');
  assert.equal(h.controller.snapshot().active, false);
  assert.equal(h.apiCalls.filter(call => call.path === '/api/voice/offer').length, 0);
});

test('mute toggles the actual microphone track and stop releases media', async t => {
  const h = await harness(t).connect();
  assert.equal(h.controller.mute(), true);
  assert.equal(h.microphone.enabled, false);
  assert.equal(h.controller.snapshot().muted, true);
  assert.equal(h.controller.mute(), false);
  assert.equal(h.microphone.enabled, true);
  await h.controller.stop({reason: 'User ended the call'});
  assert.equal(h.microphone.readyState, 'ended');
  assert.equal(h.peer.connectionState, 'closed');
  assert.equal(h.channel.readyState, 'closed');
  assert.equal(h.audio.srcObject, null);
  assert.equal(h.controller.snapshot().active, false);
});

test('blocked autoplay exposes recovery and enableSpeaker retries real playback', async t => {
  const h = await harness(t).connect();
  h.audio.blockNextPlay = true;
  h.peer.emit('track', {track: track('remote-audio'), streams: [stream(track('remote-audio'))]});
  await settle();
  assert.equal(h.controller.snapshot().speakerBlocked, true);
  const plays = h.audio.plays;
  await h.controller.enableSpeaker();
  assert.ok(h.audio.plays > plays);
  assert.equal(h.controller.snapshot().speakerBlocked, false);
  assert.equal(h.audio.paused, false);
});

test('provider prelude media stays muted even when the user enables the speaker', async t => {
  const h = await harness(t).connect();
  h.peer.emit('track', {streams: [stream(track('unverified-provider-audio'))]});
  await settle();
  assert.equal(h.audio.muted, true, 'a received media track is not a verified answer');
  h.channel.receive({type: 'response.created', response: {id: 'unverified-prelude'}});
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'unverified-prelude'});
  assert.equal(h.audio.muted, true);
  assert.notEqual(h.controller.snapshot().phase, 'speaking');
  await h.controller.enableSpeaker();
  assert.equal(h.audio.muted, true, 'playback recovery must preserve the graph-result audio gate');
  h.peer.emit('track', {streams: [stream(track('replacement-unverified-audio'))]});
  await settle();
  assert.equal(h.audio.muted, true, 'replacing the incoming track must not bypass the gate');
  assert.equal(h.turnCalls.length, 0);
});

test('only a matching verified narration buffer can unmute incoming audio', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise}).connect();
  h.peer.emit('track', {streams: [stream(track('remote-audio'))]});
  await settle();
  h.functionCall('verified-audio', 'Check the workspace.');
  await eventually(() => h.turnCalls.length === 1, 'graph must be pending');
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'response-verified-audio'});
  assert.equal(h.audio.muted, true);
  await h.controller.enableSpeaker();
  assert.equal(h.audio.muted, true);
  graph.resolve({status: 'completed', answer: 'The verified workspace result.'});
  await eventually(() => outputs(h.channel, 'verified-audio').length === 1, 'verified graph output must return');
  const narration = h.channel.sent.findLast(event => event.type === 'response.create');
  assert.deepEqual(narration.response.output_modalities, ['audio']);
  assert.equal(h.audio.muted, true, 'requesting audio does not verify an incoming playback buffer');
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'unrelated-early-buffer'});
  assert.equal(h.audio.muted, true, 'an uncorrelated buffer cannot borrow the pending narration');
  h.channel.receive({type: 'response.created', response: {
    id: 'verified-narration', metadata: narration.response.metadata
  }});
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'unrelated-buffer'});
  assert.equal(h.audio.muted, true);
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'verified-narration'});
  assert.equal(h.audio.muted, false, 'the verified narration must be audible when playback starts');
  assert.equal(h.controller.snapshot().phase, 'speaking');
  h.channel.receive({type: 'response.done', response: {id: 'verified-narration', status: 'completed', output: []}});
  h.channel.receive({type: 'output_audio_buffer.stopped', response_id: 'verified-narration'});
  assert.equal(h.audio.muted, false, 'normal provider completion must allow the browser to finish its buffered audio tail');
  await h.controller.enableSpeaker();
  assert.equal(h.audio.muted, false);
  h.channel.receive({type: 'response.created', response: {
    id: 'next-delegation', metadata: {reign_voice_phase: 'delegation', reign_voice_turn: '0'}
  }});
  assert.equal(h.audio.muted, true, 'a new non-narration response must close the verified playback gate');
});

class MeterAudioContext {
  state = 'running'; destination = {};
  async resume() { this.state = 'running'; }
  async close() { this.state = 'closed'; }
  createMediaStreamSource(value) {
    return {connect(analyser) { analyser.stream = value; }, disconnect() {}};
  }
  createAnalyser() {
    return {fftSize: 256, disconnect() {}, getByteTimeDomainData(values) {
      const microphone = this.stream?.getAudioTracks()[0]?.id === 'microphone';
      values.fill(microphone ? 176 : this.stream?.silent ? 128 : 144);
    }};
  }
}

function annotatedAnswer(segments = [
  {text:'The verified first sentence.',expression:'warm'},
  {text:'The verified second sentence.',expression:'thoughtful'}
]) {
  const result = {thread_id:'00000000-0000-4000-8000-000000000001',
    run_id:'00000000-0000-4000-8000-000000000101',status:'completed',answer:segments.map(item => item.text).join(' ')};
  return {...result,speech:{thread_id:result.thread_id,run_id:result.run_id,status:result.status,
    text:result.answer,segments,speech_status:'ready'}};
}
const narrations = h => h.channel.sent.filter(event => event.type === 'response.create' && event.response.tool_choice === 'none');
async function beginNarration(h, remote, id = 'first-spoken-response', {blocked = false} = {}) {
  h.peer.emit('track', {streams:[remote]}); await settle();
  h.functionCall('expression-plan');
  await eventually(() => narrations(h).length === 1, 'verified graph output must request narration');
  const request = narrations(h).at(-1);
  h.channel.receive({type:'response.created',response:{id,metadata:request.response.metadata}});
  h.audio.blockNextPlay = blocked;
  h.channel.receive({type:'output_audio_buffer.started',response_id:id});
  await settle();
  return request;
}

test('speaker recovery never waits for a suspended meter context and late resume cannot revive an ended call', async t => {
  const contexts = [];
  class SuspendedContext extends MeterAudioContext {
    state = 'suspended';
    constructor() { super(); this.pending = deferred(); contexts.push(this); }
    resume() { return this.pending.promise.then(() => { this.state='running'; this.onstatechange?.(); }); }
  }
  const h = await harness(t, {AudioContext:SuspendedContext}).connect();
  await beginNarration(h, stream(track('reign-output')));
  h.audio.pause();
  assert.equal(h.controller.snapshot().speakerBlocked, true);
  const before = h.audio.plays;
  const enabled = h.controller.enableSpeaker();
  assert.equal(h.audio.plays, before + 1, 'native play must begin synchronously in the gesture');
  assert.equal(await enabled, true, 'the unresolved meter resume cannot block audio recovery');
  assert.equal(h.audio.muted, false); assert.equal(h.audio.paused, false);
  h.clock.advance(100);
  assert.equal(h.outputLevels.at(-1), 0);
  assert.equal(h.controller.snapshot().playback.meterState, 'suspended');
  assert.equal(h.controller.snapshot().speakerBlocked, false);
  await h.controller.stop(); const states = h.states.length;
  contexts[0].pending.resolve(); await settle();
  assert.equal(h.states.length, states, 'a late meter resume cannot update the retired call');
  assert.equal(h.audio.srcObject, null);
});

test('a late successful play promise cannot hide a newer playback failure', async t => {
  const h = await harness(t).connect(), old = deferred();
  h.audio.play = () => old.promise;
  h.peer.emit('track', {streams:[stream(track('remote-audio'))]});
  h.audio.play = async () => { throw Error('Blocked'); };
  h.functionCall('new-play');
  await eventually(() => narrations(h).length === 1);
  h.channel.receive({type:'response.created',response:{id:'new-play',metadata:narrations(h)[0].response.metadata}});
  h.channel.receive({type:'output_audio_buffer.started',response_id:'new-play'});
  await settle(); assert.equal(h.controller.snapshot().speakerBlocked, true);
  old.resolve(); await settle();
  assert.equal(h.controller.snapshot().speakerBlocked, true);
});

test('sentence expressions follow verified playback and queue only after the real audio tail is quiet', async t => {
  const result = annotatedAnswer(), remote = stream(track('reign-output'));
  remote.silent = true;
  const h = await harness(t, {AudioContext:MeterAudioContext,runTurn:async () => result}).connect();
  const first = await beginNarration(h, remote);
  assert.equal(first.response.metadata.reign_voice_segment, '0');
  assert.ok(first.response.instructions.endsWith(JSON.stringify(result.speech.segments[0].text)));
  h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).active, true, 'silent samples do not end verified native playback');
  assert.equal(h.outputStates.at(-1).expression, 'neutral', 'provider events alone do not advance an expression cue');
  remote.silent = false; h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression, 'warm');
  assert.equal(h.outputStates.at(-1).expressionSource, 'model');
  const cue = h.outputStates.at(-1).cueMs;
  remote.silent = true; h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).active, true); assert.equal(h.outputStates.at(-1).level, 0);
  assert.equal(h.outputStates.at(-1).cueMs, cue);
  remote.silent = false;
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  h.clock.advance(500); assert.equal(narrations(h).length, 1, 'response.done cannot skip the remaining audio');
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'first-spoken-response'});
  h.clock.advance(500); assert.equal(narrations(h).length, 1, 'server drain cannot skip an audible native tail');
  remote.silent = true; h.clock.advance(250);
  assert.equal(narrations(h).length, 2);
  const second = narrations(h)[1];
  assert.equal(second.response.metadata.reign_voice_segment, '1');
  assert.notEqual(second.response.metadata.reign_voice_request, first.response.metadata.reign_voice_request);
  assert.equal(h.turnCalls.length, 1, 'sentence sequencing never re-runs the graph');
  h.channel.receive({type:'output_audio_buffer.cleared',response_id:'first-spoken-response'});
  h.channel.receive({type:'response.created',response:{id:'second-spoken-response',metadata:second.response.metadata}});
  h.channel.receive({type:'output_audio_buffer.started',response_id:'second-spoken-response'});
  remote.silent = false; await settle(); h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression, 'thoughtful');
  h.channel.receive({type:'input_audio_buffer.speech_started'});
  assert.equal(h.outputStates.at(-1).active, false); assert.equal(h.outputStates.at(-1).expression, 'neutral');
  h.channel.receive({type:'response.done',response:{id:'second-spoken-response',status:'completed',output:[]}});
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'second-spoken-response'});
  h.clock.advance(2000);
  assert.equal(narrations(h).length, 2, 'interruption must discard the rest of the speech queue');
});

test('slow expression annotations cannot delay exact narration or change a sentence after its audible onset', async t => {
  const complete=annotatedAnswer(), annotation=deferred();
  const result={...complete,speech:undefined,speechPromise:annotation.promise};
  const remote=stream(track('reign-output'));
  const h=await harness(t,{AudioContext:MeterAudioContext,runTurn:async()=>result}).connect();
  const readyAt=h.clock.now();
  const first=await beginNarration(h,remote);
  assert.equal(h.clock.now()-readyAt,0,'optional model work adds no timer wait before narration preparation');
  assert.ok(first.response.instructions.endsWith(JSON.stringify(complete.speech.segments[0].text)));
  assert.equal(first.response.metadata.reign_voice_segment_count,'2');
  h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'neutral');
  assert.ok(h.outputLevels.at(-1)>0,'the verified sentence is already playing while annotation is unresolved');
  annotation.resolve(complete.speech);await settle();h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'neutral','a late cue never changes an already audible sentence');
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'first-spoken-response'});
  remote.silent=true;h.clock.advance(250);
  assert.equal(narrations(h).length,2);
  const second=narrations(h)[1];
  h.channel.receive({type:'response.created',response:{id:'second-spoken-response',metadata:second.response.metadata}});
  h.channel.receive({type:'output_audio_buffer.started',response_id:'second-spoken-response'});
  remote.silent=false;await settle();h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'thoughtful','a matching future sentence still receives its model expression');
  assert.equal(h.turnCalls.length,1,'annotation scheduling never repeats the graph action');
});

test('expression metadata arriving before audible onset applies without delaying audio preparation', async t => {
  const complete=annotatedAnswer(), annotation=deferred();
  const remote=stream(track('reign-output'));remote.silent=true;
  const h=await harness(t,{AudioContext:MeterAudioContext,runTurn:async()=>({...complete,speech:undefined,speechPromise:annotation.promise})}).connect();
  await beginNarration(h,remote);h.clock.advance(66);
  annotation.resolve(complete.speech);await settle();
  remote.silent=false;h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'warm');
  assert.equal(h.outputStates.at(-1).expressionSource,'model');
});

test('a grouped annotation labels only future sentences fully contained in its verified span', async t => {
  const complete=annotatedAnswer(),annotation=deferred();
  const remote=stream(track('reign-output'));
  const h=await harness(t,{AudioContext:MeterAudioContext,runTurn:async()=>({...complete,speech:undefined,speechPromise:annotation.promise})}).connect();
  await beginNarration(h,remote);h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'neutral');
  annotation.resolve({...complete.speech,segments:[{text:complete.answer,expression:'concerned'}]});
  await settle();h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'neutral','the first sentence keeps its already audible neutral cue');
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'first-spoken-response'});
  remote.silent=true;h.clock.advance(250);
  assert.equal(narrations(h).length,2);
  const next=narrations(h)[1];
  h.channel.receive({type:'response.created',response:{id:'second-spoken-response',metadata:next.response.metadata}});
  h.channel.receive({type:'output_audio_buffer.started',response_id:'second-spoken-response'});
  remote.silent=false;await settle();h.clock.advance(66);
  assert.equal(h.outputStates.at(-1).expression,'concerned','the second sentence is fully inside the verified grouped tone span');
  assert.equal(h.outputStates.at(-1).expressionSource,'model');
  assert.equal(h.turnCalls.length,1);
});

test('interruption fences late expression plans and sentences crossing tone spans retain neutral cues', async t => {
  for(const interrupted of [false,true]){
    const complete=annotatedAnswer(),annotation=deferred();
    const remote=stream(track('reign-output'));remote.silent=true;
    const h=await harness(t,{AudioContext:MeterAudioContext,runTurn:async()=>({...complete,speech:undefined,speechPromise:annotation.promise})}).connect();
    await beginNarration(h,remote);
    if(interrupted)h.channel.receive({type:'input_audio_buffer.speech_started'});
    const differentlySegmented={...complete.speech,segments:[
      {text:'The verified',expression:'warm'},
      {text:'first sentence. The verified second sentence.',expression:'concerned'}]};
    annotation.resolve(interrupted?complete.speech:differentlySegmented);await settle();
    remote.silent=false;h.clock.advance(66);
    assert.equal(h.outputStates.at(-1).expression,'neutral');
    assert.equal(h.outputStates.at(-1).expressionSource,'neutral');
    if(interrupted)assert.equal(h.outputStates.at(-1).active,false);
    assert.equal(narrations(h).length,1);
  }
});

test('buffering or a frozen native media clock stops portrait motion and cannot silently advance a sentence', async t => {
  const remote = stream(track('reign-output'));
  const h = await harness(t, {AudioContext:MeterAudioContext,runTurn:async () => annotatedAnswer()}).connect();
  await beginNarration(h, remote); h.clock.advance(66);
  assert.ok(h.outputLevels.at(-1) > 0);
  h.audio.emit('waiting');
  assert.equal(h.outputStates.at(-1).active, false);
  h.audio.emit('playing'); h.clock.advance(66); assert.ok(h.outputLevels.at(-1) > 0);
  h.audio.currentTime = h.audio.currentTime; h.audio.clockFrozen = true;
  h.clock.advance(500);
  assert.equal(h.outputStates.at(-1).active, false); assert.equal(h.outputLevels.at(-1), 0);
  assert.equal(h.audio.muted, false, 'meter/clock diagnostics must never mute the actual audio');
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'first-spoken-response'});
  remote.silent = true; h.clock.advance(1600);
  assert.equal(narrations(h).length, 1);
  assert.equal(h.controller.snapshot().playback.retryAvailable, true);
  assert.match(h.controller.snapshot().status, /playback was not detected/);
});

test('a sentence drained during blocked playback is replayed only by a fresh speaker gesture, without re-running the graph', async t => {
  const h = await harness(t, {AudioContext:MeterAudioContext,runTurn:async () => annotatedAnswer()}).connect();
  await beginNarration(h, stream(track('reign-output')), 'first-spoken-response', {blocked:true});
  assert.equal(h.controller.snapshot().speakerBlocked, true);
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  h.channel.receive({type:'output_audio_buffer.stopped',response_id:'first-spoken-response'});
  h.clock.advance(2000); assert.equal(narrations(h).length, 1);
  await h.controller.enableSpeaker();
  assert.equal(narrations(h).length, 2); assert.equal(h.turnCalls.length, 1);
  assert.equal(narrations(h)[1].response.metadata.reign_voice_segment, '0');
  assert.notEqual(narrations(h)[1].response.metadata.reign_voice_request, narrations(h)[0].response.metadata.reign_voice_request);
  assert.equal(h.audio.muted, true, 'a replay still waits for its new matching narration buffer');
  h.channel.receive({type:'output_audio_buffer.started',response_id:'first-spoken-response'});
  assert.equal(h.audio.muted, true, 'the old buffer cannot authorize the new replay');
});

test('a malformed or mismatched expression plan falls back to the original verified neutral narration', async t => {
  const result = annotatedAnswer(); result.speech.segments[0].text = 'Unverified replacement text.';
  const h = await harness(t, {AudioContext:MeterAudioContext,runTurn:async () => result}).connect();
  await beginNarration(h, stream(track('reign-output'))); h.clock.advance(66);
  assert.equal(narrations(h)[0].response.metadata.reign_voice_segment_count, '2');
  assert.equal(narrations(h)[0].response.instructions.includes('Unverified replacement text'), false);
  assert.equal(h.audio.muted, false); assert.ok(h.outputLevels.at(-1) > 0);
  assert.equal(h.outputStates.at(-1).expressionSource, 'neutral');
  assert.equal(JSON.parse(outputs(h.channel, 'expression-plan')[0].item.output).answer, result.answer);
});

test('private transcript checks preserve decimal tokens and accept harmless apostrophe punctuation', async t => {
  const result = annotatedAnswer([{text:"It's 1.2 units.",expression:'thoughtful'}]);
  const h = await harness(t, {AudioContext:MeterAudioContext,runTurn:async () => result}).connect();
  await beginNarration(h, stream(track('reign-output')));
  h.channel.receive({type:'response.output_audio_transcript.done',response_id:'first-spoken-response',transcript:'It’s 1.2 units!'});
  assert.equal(h.audio.muted, false);
  h.channel.receive({type:'response.output_audio_transcript.done',response_id:'first-spoken-response',transcript:'Its 1 2 units.'});
  assert.equal(h.audio.muted, true);
  assert.ok(h.channel.sent.some(event => event.type === 'output_audio_buffer.clear'));
  h.channel.receive({type:'response.done',response:{id:'first-spoken-response',status:'completed',output:[]}});
  assert.match(h.controller.snapshot().status, /did not match/);
  assert.equal(h.outputStates.at(-1).expressionSource, 'neutral');
});

test('portrait output meter never borrows microphone energy and follows verified audible output', async t => {
  const h = await harness(t, {AudioContext: MeterAudioContext}).connect();
  const remote = stream(track('reign-output'));
  h.peer.emit('track', {streams: [remote]}); await settle();
  h.clock.advance(80);
  assert.ok(h.levels.at(-1) > 0, 'the existing activity meter may show the microphone');
  assert.equal(h.outputLevels.at(-1), 0, 'thinking/listening microphone energy must not animate REIGN');
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'unverified'});
  h.clock.advance(80); assert.equal(h.outputLevels.at(-1), 0);

  h.functionCall('portrait-output');
  await eventually(() => outputs(h.channel, 'portrait-output').length === 1, 'graph output must be verified');
  const narration = h.channel.sent.findLast(event => event.type === 'response.create');
  h.channel.receive({type: 'response.created', response: {id: 'portrait-narration', metadata: narration.response.metadata}});
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'portrait-narration'});
  await settle(); h.clock.advance(80);
  assert.equal(h.outputLevels.at(-1), .375, 'only the remote output analyser should be sampled');
  assert.equal(h.levels.at(-1), .375);
  h.controller.mute(); h.clock.advance(80);
  assert.equal(h.outputLevels.at(-1), .375, 'muting the local microphone must not stop REIGN speaking');

  remote.silent = true; h.clock.advance(80);
  assert.equal(h.outputLevels.at(-1), 0, 'a speaking provider state cannot invent mouth movement during silence');
  remote.silent = false; h.clock.advance(80); assert.ok(h.outputLevels.at(-1) > 0);
  h.channel.receive({type: 'output_audio_buffer.stopped', response_id: 'portrait-narration'});
  h.clock.advance(80);
  assert.ok(h.outputLevels.at(-1) > 0, 'verified browser-buffered audio tail keeps its portrait movement');
  h.channel.receive({type: 'input_audio_buffer.speech_started'});
  assert.equal(h.outputLevels.at(-1), 0, 'barge-in must clear portrait output synchronously');
  h.clock.advance(80); assert.equal(h.outputLevels.at(-1), 0);
  await h.controller.stop(); assert.equal(h.outputLevels.at(-1), 0);
});

test('blocked speaker playback never produces portrait output energy', async t => {
  const h = await harness(t, {AudioContext: MeterAudioContext}).connect();
  h.peer.emit('track', {streams: [stream(track('reign-output'))]}); await settle();
  h.functionCall('blocked-portrait');
  await eventually(() => outputs(h.channel, 'blocked-portrait').length === 1, 'graph output must be verified');
  const narration = h.channel.sent.findLast(event => event.type === 'response.create');
  h.channel.receive({type: 'response.created', response: {id: 'blocked-narration', metadata: narration.response.metadata}});
  h.audio.blockNextPlay = true;
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'blocked-narration'});
  await settle(); h.clock.advance(80);
  assert.equal(h.controller.snapshot().speakerBlocked, true); assert.equal(h.outputLevels.at(-1), 0);
  await h.controller.enableSpeaker(); h.clock.advance(80); assert.ok(h.outputLevels.at(-1) > 0);
});

test('stopping an active turn cancels its ownership and drops late results', async t => {
  const pendingTurn = deferred();
  const h = await harness(t, {runTurn: () => pendingTurn.promise}).connect();
  h.functionCall('late-result');
  await eventually(() => h.turnCalls.length === 1, 'turn should be running');
  const signal = h.turnCalls[0].context.signal;
  await h.controller.stop({reason: 'User ended the call'});
  assert.equal(signal.aborted, true);
  assert.ok(h.cancelled.includes(h.turnCalls[0].context.voiceCallId));
  const messagesAtStop = h.channel.sent.length;
  pendingTurn.resolve({answer: 'This result arrived too late.', status: 'completed'});
  await settle();
  assert.equal(h.channel.sent.length, messagesAtStop);
  assert.equal(h.controller.snapshot().active, false);
  assert.equal(h.microphone.readyState, 'ended');
});

test('configuration failure does not request microphone access', async t => {
  const h = harness(t, {config: {configured: false}});
  await h.controller.start();
  assert.equal(h.microphoneRequests, 0);
  assert.equal(h.controller.snapshot().active, false);
  assert.match(h.controller.snapshot().status, /not configured/i);
});

test('a failed media connection releases tracks and cannot claim it is listening', async t => {
  const h = await harness(t).connect();
  h.peer.connectionState = 'failed';
  h.peer.emit('connectionstatechange');
  await settle();
  assert.equal(h.controller.snapshot().active, false);
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.equal(h.microphone.readyState, 'ended');
});

test('backend failure returns an honest tool result instead of fabricated success', async t => {
  const h = await harness(t, {runTurn: async () => { throw new Error('The workspace is unavailable.'); }}).connect();
  h.functionCall('failed-action');
  await eventually(() => outputs(h.channel, 'failed-action').length === 1, 'failed run must resolve its tool call');
  const output = JSON.parse(outputs(h.channel, 'failed-action')[0].item.output);
  assert.notEqual(output.status, 'completed');
  assert.match(JSON.stringify(output), /failed|unavailable|error/i);
  assert.equal(h.turnCalls.length, 1);
});

test('waits for a successful provider response before running the graph or requesting narration', async t => {
  const h = await harness(t).connect();
  h.channel.sent.length = 0;
  h.functionCall('busy-provider', undefined, {responseId: 'tool-response', complete: false});
  await settle();
  assert.equal(h.turnCalls.length, 0, 'function arguments can still belong to an incomplete or cancelled response');
  assert.equal(outputs(h.channel, 'busy-provider').length, 0);
  assert.equal(h.channel.sent.filter(event => event.type === 'response.create').length, 0);
  h.channel.receive({type: 'response.done', response: {id: 'tool-response', status: 'completed'}});
  await eventually(() => outputs(h.channel, 'busy-provider').length === 1, 'successful provider response must reach graph');
  const narration = h.channel.sent.filter(event => event.type === 'response.create');
  assert.equal(narration.length, 1);
  assert.equal(narration[0].response.tool_choice, 'none');
});

for (const status of ['cancelled', 'failed', 'incomplete']) {
  test('does not execute staged function arguments from a ' + status + ' response', async t => {
    const h = await harness(t).connect();
    h.functionCall('unfinished-' + status, 'This workspace action must never execute.', {complete: false});
    await settle();
    assert.equal(h.turnCalls.length, 0);
    h.channel.receive({type: 'response.done', response: {
      id: 'response-unfinished-' + status, status, output: [{
        type: 'function_call', call_id: 'unfinished-' + status, name: 'delegate_to_reign',
        arguments: JSON.stringify({message: 'This workspace action must never execute.'})
      }]
    }});
    await settle();
    assert.equal(h.turnCalls.length, 0, status + ' generation must never invoke the graph');
    assert.equal(outputs(h.channel, 'unfinished-' + status).length, 0);
  });
}

test('barge-in invalidates a staged function before its successful final arrives', async t => {
  const h = await harness(t).connect();
  h.functionCall('old-staged', 'Do the obsolete task.', {complete: false});
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'replacement-user-input'});
  h.channel.receive({type: 'response.done', response: {id: 'response-old-staged', status: 'completed'}});
  await settle();
  assert.equal(h.turnCalls.length, 0, 'staged actions must retain their original user turn');
  h.functionCall('current-task', 'Do the current task.');
  await eventually(() => h.turnCalls.length === 1, 'the current successful response should still work');
  assert.equal(h.turnCalls[0].message, 'Do the current task.');
});

test('late arguments from an old response cannot acquire the new speech turn', async t => {
  const h = await harness(t).connect();
  h.channel.receive({type: 'response.created', response: {id: 'obsolete-provider-response'}});
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'current-user-input'});
  h.channel.receive({type: 'response.created', response: {id: 'current-provider-response'}});
  h.functionCall('late-old-arguments', 'Do not execute an interrupted request.',
    {responseId: 'obsolete-provider-response', created: false, complete: false});
  h.channel.receive({type: 'response.done', response: {id: 'obsolete-provider-response', status: 'completed'}});
  await settle();
  assert.equal(h.turnCalls.length, 0);
  h.functionCall('current-arguments', 'Run only this current request.',
    {responseId: 'current-provider-response', created: false});
  await eventually(() => h.turnCalls.length === 1, 'the current response must complete normally');
  assert.deepEqual(h.turnCalls.map(call => call.message), ['Run only this current request.']);
});

test('late created events preserve the original client response turn metadata', async t => {
  const h = await harness(t).connect();
  const startup = h.channel.sent.find(event => event.type === 'response.create');
  assert.ok(startup.response.metadata, 'client-created responses must carry their original turn');
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'interrupted-greeting'});
  h.channel.receive({type: 'response.created', response: {
    id: 'late-greeting-response', metadata: startup.response.metadata
  }});
  h.functionCall('late-greeting', 'An obsolete connection-check request.',
    {responseId: 'late-greeting-response', created: false});
  await settle();
  assert.equal(h.turnCalls.length, 0, 'late response creation cannot relabel an obsolete turn as current');
});

test('unknown provider responses cannot dispatch workspace actions', async t => {
  const h = await harness(t).connect();
  h.functionCall('unknown-response', 'An uncorrelated action.', {created: false});
  await settle();
  assert.equal(h.turnCalls.length, 0);
});

test('ambiguous final function output cannot change a staged workspace request', async t => {
  const h = await harness(t).connect();
  h.functionCall('changed-final', 'The first request.', {complete: false});
  h.channel.receive({type: 'response.done', response: {id: 'response-changed-final', status: 'completed', output: [{
    type: 'function_call', call_id: 'changed-final', name: 'delegate_to_reign',
    arguments: JSON.stringify({message: 'A conflicting request.'})
  }]}});
  await settle();
  assert.equal(h.turnCalls.length, 0, 'ambiguous provider data must not cause a workspace action');
});

test('interrupting spoken audio clears playback and suppresses late captions', async t => {
  const h = await harness(t).connect();
  h.functionCall('interrupted-answer');
  await eventually(() => outputs(h.channel, 'interrupted-answer').length === 1, 'answer must be verified first');
  h.channel.receive({type: 'response.created', response: {id: 'narration-response'}});
  h.channel.receive({type: 'response.output_audio_transcript.delta', response_id: 'narration-response', delta: 'The result is'});
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'narration-response'});
  assert.equal(h.controller.snapshot().phase, 'speaking');
  const captionCount = h.captions.length;
  h.channel.sent.length = 0;
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'new-speech'});
  assert.equal(h.audio.muted, true, 'barge-in must silence buffered speech immediately');
  assert.ok(h.channel.sent.some(event => event.type === 'response.cancel'));
  assert.ok(h.channel.sent.some(event => event.type === 'output_audio_buffer.clear'));
  assert.equal(h.controller.snapshot().phase, 'listening');
  h.channel.receive({type: 'response.output_audio_transcript.done', response_id: 'narration-response', transcript: 'An answer the user interrupted.'});
  assert.equal(h.captions.length, captionCount, 'interrupted response must not replace the current caption');
  assert.equal(h.cancelled.length, 0, 'interrupting speech must not undo a completed task');
});

test('interrupting during graph execution preserves the task and avoids narrating its stale answer', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise}).connect();
  h.channel.sent.length = 0;
  h.functionCall('still-working');
  await eventually(() => h.turnCalls.length === 1, 'graph must be running');
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'next-request'});
  assert.equal(h.turnCalls[0].context.signal.aborted, false);
  assert.equal(h.cancelled.length, 0);
  assert.match(h.controller.snapshot().status, /still running|current task|working/i);
  graph.resolve({status: 'completed', answer: 'The earlier task finished.'});
  await eventually(() => outputs(h.channel, 'still-working').length === 1, 'earlier task must report its real result');
  assert.equal(h.channel.sent.filter(event => event.type === 'response.create').length, 0,
    'an earlier turn must not speak over the new user turn');
});

test('speech stays active until buffered output finishes, even after response.done', async t => {
  const h = await harness(t).connect();
  h.functionCall('buffered-audio');
  await eventually(() => outputs(h.channel, 'buffered-audio').length === 1, 'answer must be verified first');
  h.channel.receive({type: 'response.created', response: {id: 'buffered-response'}});
  h.channel.receive({type: 'output_audio_buffer.started', response_id: 'buffered-response'});
  assert.equal(h.controller.snapshot().phase, 'speaking');
  h.channel.receive({type: 'response.done', response: {id: 'buffered-response', status: 'completed'}});
  assert.equal(h.controller.snapshot().phase, 'speaking', 'generation completion must not claim playback ended');
  h.channel.receive({type: 'output_audio_buffer.stopped', response_id: 'buffered-response'});
  assert.equal(h.controller.snapshot().phase, 'listening');
});

test('a late cancelled response cannot release the newer provider response', async t => {
  const h = await harness(t).connect();
  h.channel.sent.length = 0;
  h.channel.receive({type: 'response.created', response: {id: 'old-response'}});
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'replacement-speech'});
  h.channel.receive({type: 'response.created', response: {id: 'new-response'}});
  h.functionCall('new-request', undefined, {responseId: 'new-response', created: false, complete: false});
  h.channel.receive({type: 'response.done', response: {id: 'old-response', status: 'cancelled'}});
  assert.equal(h.turnCalls.length, 0);
  assert.equal(h.channel.sent.filter(event => event.type === 'response.create').length, 0,
    'must wait for the current response, not a cancelled predecessor');
  h.channel.receive({type: 'response.done', response: {id: 'new-response', status: 'completed'}});
  await eventually(() => outputs(h.channel, 'new-request').length === 1, 'new graph result must return');
  assert.equal(h.channel.sent.filter(event => event.type === 'response.create').length, 1);
});

test('microphone permission timeout cleans up a late grant', async t => {
  const permission = deferred();
  const h = harness(t, {getUserMedia: () => permission.promise});
  const starting = h.controller.start();
  await eventually(() => h.microphoneRequests === 1, 'microphone permission must be pending');
  h.clock.advance(20001);
  await starting;
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.match(h.controller.snapshot().status, /microphone.*timed out/i);
  permission.resolve(h.microphoneStream);
  await eventually(() => h.microphone.readyState === 'ended', 'timed-out permission result must release tracks');
  assert.equal(h.peers.length, 0);
});

test('provider negotiation failure releases the acquired microphone', async t => {
  const h = harness(t, {api: async path => {
    if (path === '/api/voice') return {configured: true, model: 'test-model'};
    throw new Error('The voice provider is temporarily unavailable.');
  }});
  await h.controller.start();
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.equal(h.microphone.readyState, 'ended');
  assert.equal(h.channel.readyState, 'closed');
  assert.equal(h.peer.connectionState, 'closed');
  assert.match(h.controller.snapshot().status, /unavailable/i);
});

test('captured media and provider callbacks from an ended call cannot alter a new call', async t => {
  let acquisition = 0;
  const oldMicrophone = track('old-microphone'), freshMicrophone = track('fresh-microphone');
  const h = await harness(t, {getUserMedia: () => stream(acquisition++ ? freshMicrophone : oldMicrophone)}).connect();
  const oldMessage = h.channel.onmessage, oldTrack = h.peer.ontrack, oldConnection = h.peer.onconnectionstatechange;
  await h.controller.stop();
  await h.connect();
  const remote = stream(track('current-remote'));
  h.peer.emit('track', {streams: [remote]});
  await settle();
  const expected = h.controller.snapshot();
  oldTrack({streams: [stream(track('obsolete-remote'))]});
  oldConnection();
  oldMessage({data: JSON.stringify({type: 'response.function_call_arguments.done', call_id: 'obsolete-call',
    name: 'delegate_to_reign', arguments: JSON.stringify({message: 'An obsolete request'})})});
  oldMessage({data: JSON.stringify({type: 'error', error: {code: 'obsolete_error'}})});
  await settle();
  assert.deepEqual(h.controller.snapshot(), expected);
  assert.equal(h.audio.srcObject, remote);
  assert.equal(h.turnCalls.length, 0);
  assert.equal(oldMicrophone.readyState, 'ended');
  assert.equal(freshMicrophone.readyState, 'live');
});

test('stop shuts down media immediately while owned task cancellation is pending', async t => {
  const graph = deferred(), cancellation = deferred();
  const h = await harness(t, {runTurn: () => graph.promise, cancelTurn: () => cancellation.promise}).connect();
  h.functionCall('owned-task');
  await eventually(() => h.turnCalls.length === 1, 'owned task must be active');
  h.functionCall('queued-task');
  const stopping = h.controller.stop({reason: 'User ended the call'});
  assert.equal(h.microphone.readyState, 'ended');
  assert.equal(h.audio.srcObject, null);
  assert.equal(h.channel.readyState, 'closed');
  assert.equal(h.controller.snapshot().active, false);
  assert.equal(h.controller.snapshot().phase, 'ending');
  assert.equal(h.controller.snapshot().queueLength, 0);
  cancellation.resolve();
  await stopping;
  graph.resolve({answer: 'A late answer', status: 'completed'});
  await settle();
  assert.equal(h.turnCalls.length, 1, 'a queued request must not start after stop');
  assert.deepEqual(h.cancelled, [h.turnCalls[0].context.voiceCallId]);
  assert.equal(h.controller.snapshot().phase, 'ended');
});

test('a cancellation failure is visible while microphone and peer remain closed', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise,
    cancelTurn: async () => { throw new Error('Cannot reach the graph service'); }}).connect();
  h.functionCall('cannot-confirm-cancel');
  await eventually(() => h.turnCalls.length === 1, 'owned task must be active');
  await h.controller.stop();
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.match(h.controller.snapshot().status, /cancellation.*not.*confirmed|could not.*cancel/i);
  assert.equal(h.microphone.readyState, 'ended');
  assert.equal(h.peer.connectionState, 'closed');
  graph.resolve({answer: 'Unconfirmed task result', status: 'completed'});
  await settle();
  assert.equal(outputs(h.channel, 'cannot-confirm-cancel').length, 0);
});

test('a paused graph remains pending and resumes before reporting a completed answer', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise}).connect();
  h.channel.sent.length = 0;
  h.functionCall('paused-task');
  await eventually(() => h.turnCalls.length === 1, 'owned task must be active');
  h.turnCalls[0].context.onStatus('paused');
  assert.equal(h.controller.snapshot().graphStatus, 'paused');
  assert.equal(h.controller.snapshot().delegating, true);
  assert.match(h.controller.snapshot().status, /paused|control|resume/i);
  assert.equal(outputs(h.channel, 'paused-task').length, 0);
  assert.equal(h.channel.sent.filter(event => event.type === 'response.create').length, 0);
  h.turnCalls[0].context.onStatus('running');
  graph.resolve({status: 'completed', answer: 'The resumed task finished.'});
  await eventually(() => outputs(h.channel, 'paused-task').length === 1, 'resumed task must report the real answer');
  assert.equal(JSON.parse(outputs(h.channel, 'paused-task')[0].item.output).status, 'completed');
});

test('a disconnected microphone ends the call and releases its peer', async t => {
  const h = await harness(t).connect();
  h.microphone.onended();
  await settle();
  assert.equal(h.controller.snapshot().phase, 'failed');
  assert.match(h.controller.snapshot().status, /microphone disconnected/i);
  assert.equal(h.peer.connectionState, 'closed');
  assert.equal(h.microphone.readyState, 'ended');
});

test('a late speaker-enable result cannot claim an ended call is ready to speak', async t => {
  const h = await harness(t).connect();
  const playback = deferred();
  h.audio.play = () => playback.promise;
  const enabling = h.controller.enableSpeaker();
  await settle();
  await h.controller.stop({reason: 'User ended the call'});
  const ended = h.controller.snapshot();
  playback.resolve();
  await enabling;
  assert.deepEqual(h.controller.snapshot(), ended);
});

test('invalid or excess tool calls never execute workspace actions', async t => {
  const graph = deferred();
  const h = await harness(t, {runTurn: () => graph.promise}).connect();
  for (const [id, name, args] of [
    ['wrong-tool', 'arbitrary_tool', {message: 'Do a workspace action'}],
    ['extra-properties', 'delegate_to_reign', {message: 'Do a workspace action', command: 'not allowed'}]
  ]) {
    h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'input-' + id});
    h.channel.receive({type: 'response.created', response: {id: 'response-' + id}});
    h.channel.receive({type: 'response.function_call_arguments.done', response_id: 'response-' + id,
      call_id: id, name, arguments: JSON.stringify(args)});
    h.channel.receive({type: 'response.done', response: {id: 'response-' + id, status: 'completed'}});
  }
  assert.equal(h.turnCalls.length, 0);
  for (const id of ['wrong-tool', 'extra-properties']) {
    assert.equal(JSON.parse(outputs(h.channel, id)[0].item.output).status, 'failed');
  }
  h.channel.receive({type: 'input_audio_buffer.speech_started', item_id: 'valid-queued-input'});
  h.functionCall('one'); h.functionCall('two'); h.functionCall('three'); h.functionCall('overflow');
  await settle();
  assert.equal(h.turnCalls.length, 1);
  assert.ok(h.controller.snapshot().queueLength <= 3);
  const rejected = JSON.parse(outputs(h.channel, 'overflow')[0].item.output);
  assert.equal(rejected.status, 'failed');
  assert.match(rejected.answer, /not queued|wait|queue/i);
  await h.controller.stop();
  graph.resolve({status: 'cancelled', answer: 'Stopped'});
});
