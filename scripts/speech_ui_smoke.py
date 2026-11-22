"""Real browser audio-clock acceptance with explicitly synthetic room/PCM data.

This supplements the live-provider server gate. It tests the shipped page and
real Web Audio output graph, not a physical speaker or the model's voice quality.
The input fixture uses a generated tone through a real MediaRecorder, with a
scripted transcription/model response. No physical microphone, camera,
production login or saved room is used by these fixtures.
"""
import asyncio
import io
import json
import math
import re
import struct
import wave
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import expect


AUDIO_OBSERVER = r"""(() => {
  const NativeAudio = window.AudioContext;
  const probe = window.__speechOutput = {contexts: [], sources: [], frames: [],
    destinationConnections: 0, deviceRequests: 0, audioRequests: []};
  const fetch = window.fetch;
  window.fetch = function(input, ...args) {
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/audio/')) probe.audioRequests.push({at: performance.now(), url});
    return fetch.call(this, input, ...args);
  };
  const connect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function(destination, ...args) {
    if (destination instanceof AudioDestinationNode) probe.destinationConnections++;
    return connect.call(this, destination, ...args);
  };
  window.AudioContext = class extends NativeAudio {
    constructor(...args) { super(...args); probe.contexts.push(this); }
    createBufferSource() {
      const source = super.createBufferSource();
      const meter = super.createAnalyser(); meter.fftSize = 256;
      source.connect(meter);
      const entry = {context: this, source, meter, start: null, startedAt: null, ended: false, endedAt: null};
      probe.sources.push(entry);
      const start = source.start.bind(source);
      source.start = (when = 0, ...args) => {
        entry.start = Math.max(when, this.currentTime);
        entry.startedAt = performance.now();
        return start(when, ...args);
      };
      source.addEventListener('ended', () => { entry.ended = true; entry.endedAt = performance.now(); });
      return source;
    }
  };
  const media = navigator.mediaDevices;
  if (media?.getUserMedia) {
    const get = media.getUserMedia.bind(media);
    media.getUserMedia = (...args) => { probe.deviceRequests++; return get(...args); };
  }
  const samples = new Float32Array(256);
  function observe() {
    const entry = probe.sources.at(-1), canvas = document.querySelector('#reignAvatar');
    if (entry && entry.start !== null && canvas && probe.frames.length < 2400) {
      entry.meter.getFloatTimeDomainData(samples);
      const timestamp = entry.context.getOutputTimestamp?.();
      const output = timestamp?.contextTime > 0 ? timestamp.contextTime :
        Math.max(0, entry.context.currentTime - (entry.context.outputLatency || 0));
      probe.frames.push({source: probe.sources.length - 1, at: performance.now(),
        time: output - entry.start, contextTime: entry.context.currentTime,
        state: entry.context.state, ended: entry.ended,
        rms: Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length),
        mouth: Number(canvas.dataset.mouth || 0), expression: canvas.dataset.expression,
        speaking: canvas.dataset.speaking === 'true'});
    }
    requestAnimationFrame(observe);
  }
  requestAnimationFrame(observe);
})();"""


def synthetic_pcm():
    """Known silence/tone/silence windows make timing errors observable."""
    rate, duration = 24000, 2.6
    pcm = bytearray()
    for i in range(int(rate * duration)):
        t = i / rate
        amplitude = .24 if .5 <= t < 1.8 else 0
        pcm.extend(struct.pack('<h', round(32767 * amplitude * math.sin(2 * math.pi * 220 * t))))
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return out.getvalue()


async def simulated_team_speech_ui(browser, storage_state, origin, evidence):
    context = await browser.new_context(ignore_https_errors=True, storage_state=storage_state)
    await context.add_init_script(AUDIO_OBSERVER)
    page = await context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    room_id = '00000000-0000-4000-8000-000000000010'
    other_id = '00000000-0000-4000-8000-000000000020'
    audio_ids = ['00000000-0000-4000-8000-000000000030',
                 '00000000-0000-4000-8000-000000000040']
    rooms = [{'id': room_id, 'title': 'Synthetic audio check'},
             {'id': other_id, 'title': 'Synthetic empty room'}]
    events, posts, audio_requests = [], [], []
    changed = asyncio.Event()
    later_audio_requested, later_audio_ready = asyncio.Event(), asyncio.Event()
    pcm = synthetic_pcm()

    async def route_team(route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        if path == '/api/team':
            result = {'workspace': {'id': 'rain', 'name': 'Workspace', 'durable': True},
                      'me': {'id': 'synthetic-owner', 'role': 'owner', 'username': 'synthetic',
                             'display_name': 'Synthetic audio tester'},
                      'members': [], 'rooms': rooms,
                      'capabilities': {'assistant': True, 'speech': True, 'calls': True,
                                       'documents': True, 'shared_browser': False}}
        elif '/audio/' in path:
            assert path.rsplit('/', 1)[-1] in audio_ids
            audio_requests.append(path)
            if path.endswith(audio_ids[1]):
                later_audio_requested.set()
                await later_audio_ready.wait()
            await route.fulfill(status=200, content_type='audio/wav', body=pcm)
            return
        elif path.endswith('/messages'):
            payload = request.post_data_json
            posts.append(payload)
            assert payload['ask_reign'] is True
            sequence = len(events) + 1
            message = {'id': 'synthetic-message-' + str(sequence), 'seq': sequence,
                       'type': 'message', 'display_name': 'Synthetic audio tester',
                       'created_at': 1700000000, 'data': payload}
            segments = [{'text': 'Synthetic warm sentence.', 'expression': 'warm'},
                        {'text': 'Synthetic concerned sentence.', 'expression': 'concerned'}]
            if payload['speak']:
                for segment, audio_id in zip(segments, audio_ids):
                    segment['audio_id'] = audio_id
            reply = {'id': 'synthetic-reply-' + str(sequence), 'seq': sequence + 1,
                     'type': 'assistant_message', 'display_name': 'REIGN', 'created_at': 1700000001,
                     'data': {'text': 'Synthetic warm sentence. Synthetic concerned sentence.',
                              'segments': segments, 'speech_status': 'streaming' if payload['speak'] else 'not_requested'}}
            events.extend([message, reply])
            changed.set()
            result = {'event': message}
        elif path.endswith('/events'):
            after = int(parse_qs(url.query).get('after', ['0'])[0])
            visible = [event for event in events if event['seq'] > after] if room_id in path else []
            if not visible:
                changed.clear()
                try:
                    await asyncio.wait_for(changed.wait(), 1)
                except asyncio.TimeoutError:
                    pass
                visible = [event for event in events if event['seq'] > after] if room_id in path else []
            result = {'events': visible, 'presence': [], 'assistant': {'status': 'idle'}}
        elif '/presence' in path:
            result = {'presence': []}
        elif path in ['/api/team/rooms/' + room['id'] for room in rooms]:
            room = next(room for room in rooms if path.endswith(room['id']))
            result = {'room': room, 'events': events if room['id'] == room_id else [],
                      'last_seq': len(events) if room['id'] == room_id else 0,
                      'documents': [], 'presence': [], 'assistant': {'status': 'idle'}}
        else:
            raise AssertionError('Unexpected synthetic team request: ' + request.method + ' ' + path)
        await route.fulfill(status=200, json=result)

    await context.route(origin + '/api/team**', route_team)
    result = {'status': 'failed', 'mode': 'real_chromium_web_audio_with_synthetic_room_and_pcm',
              'live_provider': 'not_tested', 'physical_speaker': 'not_tested'}
    try:
        await page.goto(origin + '/team')
        await expect(page.locator('#reignAvatar')).to_have_attribute('data-ready', 'true')
        await expect(page.locator('#speakReply')).to_be_checked()
        assert await page.evaluate('__speechOutput.contexts.length') == 0
        await page.locator('#messageInput').fill('Synthetic audio synchronization check.')
        await page.locator('#askReign').click()
        # A pending later segment must not delay already available first audio.
        # Release it only after real playback and mouth movement are observed.
        await page.wait_for_function("""() => __speechOutput.sources.length === 1 &&
            !__speechOutput.sources[0].ended &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) > .15""")
        await asyncio.wait_for(later_audio_requested.wait(), 2)
        assert await page.evaluate('!__speechOutput.sources[0].ended')
        later_audio_ready.set()
        await page.wait_for_function("""() => __speechOutput.sources.length === 2 &&
            __speechOutput.sources.every(source => source.ended) &&
            document.querySelector('#reignAvatar').dataset.speaking === 'false'""")
        assert len(posts) == 1 and posts[0]['speak'] is True
        assert len(audio_requests) == 2
        observed = await page.evaluate("""() => ({frames: __speechOutput.frames,
            audioRequests: __speechOutput.audioRequests,
            sources: __speechOutput.sources.map(source => ({start: source.start,
                startedAt: source.startedAt, endedAt: source.endedAt, duration: source.source.buffer.duration})),
            destinations: __speechOutput.destinationConnections,
            deviceRequests: __speechOutput.deviceRequests})""")
        (evidence / 'speech-clock-fixture.json').write_text(json.dumps(observed, indent=2) + '\n')
        assert observed['destinations'] >= 2, 'Speech was never connected to browser output'
        assert observed['deviceRequests'] == 0, 'Typed speech unexpectedly opened a microphone or camera'
        assert observed['audioRequests'][1]['at'] < observed['sources'][0]['endedAt'], \
            'The next spoken sentence was not prefetched while the first played'
        sentence_gap = (observed['sources'][1]['start'] - observed['sources'][0]['start'] -
                        observed['sources'][0]['duration'])
        assert 0 <= sentence_gap < .35, 'Synthetic sentence playback inserted an avoidable gap'
        timing = []
        for index, expression in enumerate(['warm', 'concerned']):
            frames = [frame for frame in observed['frames'] if frame['source'] == index and not frame['ended']]
            quiet = [frame for frame in frames if .15 < frame['time'] < .35]
            voiced = [frame for frame in frames if .8 < frame['time'] < 1.4]
            tail = [frame for frame in frames if 2.2 < frame['time'] < 2.45]
            assert quiet and voiced and tail, 'Browser did not observe each real playback window'
            assert max(frame['mouth'] for frame in quiet) < .08, 'Lips moved before PCM speech'
            assert min(frame['mouth'] for frame in voiced) > .12, 'Lips did not follow playing PCM'
            assert max(frame['rms'] for frame in voiced) > .05, 'Browser output graph was silent'
            assert max(frame['mouth'] for frame in tail) < .08, 'Lips continued during the silent PCM tail'
            assert all(frame['expression'] == expression for frame in voiced), 'Wrong sentence expression'
            timing.append({'expression': expression, 'observed_frames': len(frames),
                           'maximum_output_rms': max(frame['rms'] for frame in voiced),
                           'leading_silence_mouth_max': max(frame['mouth'] for frame in quiet),
                           'voiced_mouth_min': min(frame['mouth'] for frame in voiced),
                           'trailing_silence_mouth_max': max(frame['mouth'] for frame in tail)})

        # A real suspended AudioContext must stop facial speech immediately and
        # recover from the visible audio control without replaying old replies.
        await page.locator('#messageInput').fill('Synthetic suspension and room-change check.')
        await page.locator('#askReign').click()
        await page.wait_for_function("""() => __speechOutput.sources.length === 3 &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) > .15""")
        await page.evaluate('__speechOutput.contexts[0].suspend()')
        await page.wait_for_function("""() => document.querySelector('#reignAvatar').dataset.speaking === 'false' &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) < .001""")
        await expect(page.locator('#assistantDetail')).to_contain_text(re.compile('audio|listen|paused', re.I))
        await page.locator('#listenReign').click()
        await page.wait_for_function("""() => __speechOutput.contexts[0].state === 'running' &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) > .15""")
        await page.locator(f'[data-room-id="{other_id}"]').click()
        await expect(page.locator('#roomTitle')).to_have_text('Synthetic empty room')
        await expect(page.locator('#roomView')).to_be_visible()
        await expect(page.locator('#messageInput')).to_be_enabled()
        await page.wait_for_function("""() => __speechOutput.sources.every(source => source.ended) &&
            document.querySelector('#reignAvatar').dataset.speaking === 'false'""")
        assert await page.evaluate("Number(document.querySelector('#reignAvatar').dataset.mouth)") == 0
        before = await page.evaluate('__speechOutput.sources.length')
        requests_before = len(audio_requests)
        await page.locator(f'[data-room-id="{room_id}"]').click()
        await expect(page.locator('#roomTitle')).to_have_text('Synthetic audio check')
        await expect(page.locator('#roomView')).to_be_visible()
        await expect(page.locator('#messageInput')).to_be_enabled()
        # Wait for rendered archive application, not just the room title (which
        # changes before its asynchronous snapshot is loaded).
        await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert await page.evaluate('__speechOutput.sources.length') == before
        assert len(audio_requests) == requests_before, 'Restored archive requested old speech'
        await page.locator('#speakReply').uncheck()
        await page.locator('#messageInput').fill('Synthetic explicit text-only check.')
        await page.locator('#askReign').click()
        await expect(page.locator('#eventFeed')).to_contain_text('Synthetic explicit text-only check.')
        await expect(page.locator(f'[data-sequence="{len(events)}"]')).to_be_visible()
        assert posts[-1]['speak'] is False
        assert await page.evaluate('__speechOutput.sources.length') == before
        await page.locator(f'[data-room-id="{other_id}"]').click()
        await expect(page.locator('#roomTitle')).to_have_text('Synthetic empty room')
        await expect(page.locator('#roomView')).to_be_visible()
        await expect(page.locator('#messageInput')).to_be_enabled()
        await page.locator(f'[data-room-id="{room_id}"]').click()
        await expect(page.locator('#roomView')).to_be_visible()
        await expect(page.locator('#messageInput')).to_be_enabled()
        await expect(page.locator('#speakReply')).not_to_be_checked()
        await page.screenshot(path=str(evidence / 'workspace-speech-playback-check.png'), full_page=True)
        assert not errors, errors
        result.update(status='passed', timing=timing, sentence_gap_seconds=sentence_gap,
                      checks=['typed_ask_enables_output_with_one_gesture', 'voice_reply_default_and_explicit_opt_out',
                              'real_output_graph_non_silent', 'mouth_tracks_pcm_silence_and_voice',
                              'first_audio_plays_while_later_clip_pending',
                              'next_sentence_prefetched_during_playback', 'bounded_synthetic_sentence_gap',
                              'sentence_expression_follows_audio', 'suspend_and_resume',
                              'room_change_stops_audio', 'archives_do_not_replay', 'no_input_device_requested'])
        return result
    finally:
        changed.set()
        later_audio_ready.set()
        await context.close()


MICROPHONE_OBSERVER = r"""(() => {
  const probe = window.__microphoneInput = {requests: [], recordings: [],
    tracks: [], streams: [], posts: [], peers: 0};
  const NativeRecorder = window.MediaRecorder;
  window.MediaRecorder = class extends NativeRecorder {
    constructor(...args) {
      super(...args);
      const receipt = {startedAt: null, stoppedAt: null, bytes: 0, mimeType: this.mimeType};
      probe.recordings.push(receipt);
      this.addEventListener('start', () => { receipt.startedAt = performance.now(); });
      this.addEventListener('dataavailable', event => { receipt.bytes += event.data.size; });
      this.addEventListener('stop', () => { receipt.stoppedAt = performance.now(); });
    }
  };
  const Peer = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends Peer {
    constructor(...args) { super(...args); probe.peers++; }
  };
  const fetch = window.fetch;
  window.fetch = function(input, options, ...args) {
    const url = typeof input === 'string' ? input : input.url;
    if (options?.method === 'POST' && /\/(transcriptions|messages)$/.test(url))
      probe.posts.push({kind: url.split('/').at(-1), at: performance.now()});
    return fetch.call(this, input, options, ...args);
  };
  navigator.mediaDevices.getUserMedia = async constraints => {
    probe.requests.push(constraints);
    if (!constraints.audio || constraints.video !== false) throw Error('Unexpected input-device request');
    __speechOutput.deviceRequests++;
    const context = new AudioContext(), source = context.createOscillator();
    const gain = context.createGain(), destination = context.createMediaStreamDestination();
    const meter = context.createAnalyser(); meter.fftSize = 512;
    source.frequency.value = 260;
    source.connect(gain); gain.connect(destination); gain.connect(meter);
    gain.gain.value = 0; source.start();
    await context.resume();
    const start = context.currentTime;
    gain.gain.setValueAtTime(.22, start + .3);
    gain.gain.setValueAtTime(0, start + 1.2);
    const entry = {requestedAt: performance.now(), firstSoundAt: null,
      lastSoundAt: null, silenceAt: null, stoppedAt: null, maximumRms: 0};
    probe.streams.push(entry);
    const samples = new Float32Array(meter.fftSize);
    function sample() {
      if (context.state === 'closed') return;
      meter.getFloatTimeDomainData(samples);
      const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
      entry.maximumRms = Math.max(entry.maximumRms, rms);
      if (rms > .012) {
        entry.firstSoundAt ??= performance.now(); entry.lastSoundAt = performance.now();
      } else if (entry.firstSoundAt !== null) entry.silenceAt ??= performance.now();
      requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
    for (const track of destination.stream.getTracks()) {
      probe.tracks.push(track);
      const stop = track.stop.bind(track);
      track.stop = () => {
        entry.stoppedAt ??= performance.now(); stop();
        try { source.stop(); } catch {}
        void context.close().catch(() => {});
      };
    }
    return destination.stream;
  };
})();"""


async def simulated_team_microphone_ui(browser, storage_state, origin, evidence):
    """Exercise the real recorder, silence endpoint and output clock with fake speech APIs."""
    context = await browser.new_context(ignore_https_errors=True, storage_state=storage_state)
    await context.add_init_script(AUDIO_OBSERVER + MICROPHONE_OBSERVER)
    page = await context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    room_id = '00000000-0000-4000-8000-000000000050'
    transcription_id = '00000000-0000-4000-8000-000000000060'
    audio_id = '00000000-0000-4000-8000-000000000070'
    text = 'Synthetic spoken question from the microphone.'
    room = {'id': room_id, 'title': 'Synthetic microphone check'}
    events, uploads, posts = [], [], []
    changed = asyncio.Event()

    async def route_team(route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        if path == '/api/team':
            result = {'workspace': {'id': 'rain', 'name': 'Workspace', 'durable': True},
                      'me': {'id': 'synthetic-owner', 'role': 'owner', 'username': 'synthetic',
                             'display_name': 'Synthetic microphone tester'},
                      'members': [], 'rooms': [room],
                      'capabilities': {'assistant': True, 'speech': True, 'transcription': True,
                                       'calls': True, 'documents': True, 'shared_browser': False}}
        elif path.endswith('/transcriptions'):
            payload = request.post_data_json
            assert payload['mime_type'] in ['audio/webm', 'audio/mp4', 'audio/ogg']
            assert 100 < len(payload['audio_base64']) < 750000
            uploads.append({'mime_type': payload['mime_type'], 'base64_bytes': len(payload['audio_base64'])})
            result = {'text': text, 'transcription_id': transcription_id}
        elif path.endswith('/messages'):
            payload = request.post_data_json
            assert payload['text'] == text and payload['ask_reign'] is True and payload['speak'] is True
            assert payload['input_source'] == 'voice' and payload['transcription_id'] == transcription_id
            posts.append(payload)
            message = {'id': 'synthetic-voice-message', 'seq': 1, 'type': 'message',
                       'display_name': 'Synthetic microphone tester', 'created_at': 1700000000, 'data': payload}
            reply = {'id': 'synthetic-voice-reply', 'seq': 2, 'type': 'assistant_message',
                     'display_name': 'REIGN', 'created_at': 1700000001,
                     'data': {'text': 'Synthetic spoken reply.', 'speech_status': 'ready',
                              'segments': [{'text': 'Synthetic spoken reply.', 'expression': 'warm',
                                            'audio_id': audio_id}]}}
            events.extend([message, reply]); changed.set()
            result = {'event': message}
        elif path.endswith('/audio/' + audio_id):
            await route.fulfill(status=200, content_type='audio/wav', body=synthetic_pcm())
            return
        elif path.endswith('/events'):
            after = int(parse_qs(url.query).get('after', ['0'])[0])
            visible = [event for event in events if event['seq'] > after]
            if not visible:
                changed.clear()
                try:
                    await asyncio.wait_for(changed.wait(), 1)
                except asyncio.TimeoutError:
                    pass
                visible = [event for event in events if event['seq'] > after]
            result = {'events': visible, 'presence': [], 'assistant': {'status': 'idle'}}
        elif '/presence' in path:
            result = {'presence': []}
        elif path.endswith('/rooms/' + room_id):
            result = {'room': room, 'events': events, 'last_seq': len(events),
                      'documents': [], 'presence': [], 'assistant': {'status': 'idle'}}
        else:
            raise AssertionError('Unexpected synthetic microphone request: ' + request.method + ' ' + path)
        await route.fulfill(status=200, json=result)

    await context.route(origin + '/api/team**', route_team)
    try:
        await page.goto(origin + '/team')
        await expect(page.locator('#talkToReign')).to_be_enabled()
        await expect(page.locator('#reignAvatar')).to_have_attribute('data-ready', 'true')
        assert await page.evaluate('__microphoneInput.requests.length') == 0
        # Talk alone owns capture and unlocks output. No separate Send or Finish
        # gesture is used to advance this genuine MediaRecorder recording.
        await page.locator('#talkToReign').click()
        await expect(page.locator('#stopDictation')).to_be_visible()
        await page.wait_for_function("""() => __speechOutput.sources.length === 1 &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) > .15""")
        observed = await page.evaluate("""() => ({requests: __microphoneInput.requests,
            recordings: __microphoneInput.recordings, streams: __microphoneInput.streams,
            posts: __microphoneInput.posts, peers: __microphoneInput.peers,
            firstAudioAt: __speechOutput.sources[0].startedAt,
            firstNonSilentOutputAt: __speechOutput.frames.find(frame => frame.source === 0 && frame.rms > .05)?.at,
            tracksEnded: __microphoneInput.tracks.every(track => track.readyState === 'ended')})""")
        assert len(uploads) == len(posts) == len(observed['requests']) == 1
        assert observed['peers'] == 0 and observed['tracksEnded']
        stream = observed['streams'][0]
        recording = observed['recordings'][0]
        assert recording['bytes'] > 100 and recording['stoppedAt'] is not None
        assert stream['maximumRms'] > .1 and stream['silenceAt'] is not None
        silence_to_upload = observed['posts'][0]['at'] - stream['silenceAt']
        silence_to_message = observed['posts'][1]['at'] - stream['silenceAt']
        silence_to_audio = observed['firstAudioAt'] - stream['silenceAt']
        silence_to_sound = observed['firstNonSilentOutputAt'] - stream['silenceAt']
        assert 450 <= silence_to_upload < 1900, 'Talk did not endpoint promptly after actual captured silence'
        assert silence_to_message < 2500, 'Synthetic transcription did not reach the message promptly'
        assert silence_to_audio < 3500, 'Synthetic spoken turn waited too long before output'
        assert silence_to_sound < 4000, 'Synthetic spoken turn waited too long before non-silent output'
        assert recording['stoppedAt'] - recording['startedAt'] < 5000, 'Talk waited for the old 20-second recording cap'
        await expect(page.locator('.voice-message-label')).to_have_text('Spoken message')
        await page.wait_for_function("""() => __speechOutput.sources[0].ended &&
            document.querySelector('#reignAvatar').dataset.speaking === 'false'""")

        # Cancellation releases its own mic and never submits that partial take,
        # including after its would-be auto endpoint has elapsed.
        await page.locator('#talkToReign').click()
        await expect(page.locator('#stopDictation')).to_be_visible()
        await page.locator('#cancelDictation').click()
        await page.wait_for_function('__microphoneInput.tracks.every(track => track.readyState === "ended")')
        await asyncio.sleep(2)
        assert len(uploads) == len(posts) == 1
        assert await page.evaluate('__microphoneInput.requests.length') == 2
        await page.locator('#messageInput').fill('Keep this typed draft.')
        await page.locator('#talkToReign').click()
        await expect(page.locator('#messageInput')).to_have_value('Keep this typed draft.')
        assert await page.evaluate('__microphoneInput.requests.length') == 2
        await expect(page.locator('#dictationStatus')).to_contain_text('draft')
        assert not errors, errors
        result = {'status': 'passed', 'mode': 'real_media_recorder_synthetic_tone_and_scripted_speech_apis',
                  'live_provider': 'not_tested', 'physical_microphone_or_speaker': 'not_tested',
                  'silence_to_upload_ms': round(silence_to_upload),
                  'silence_to_message_ms': round(silence_to_message),
                  'silence_to_output_start_ms': round(silence_to_audio),
                  'silence_to_non_silent_output_ms': round(silence_to_sound),
                  'recording_bytes': recording['bytes'], 'uploads': uploads,
                  'checks': ['talk_gesture_starts_owned_microphone_and_unlocks_output',
                             'real_recording_stops_on_silence', 'one_upload_one_spoken_message',
                             'issued_transcription_receipt_attached', 'real_output_and_mouth_advance',
                             'cancel_prevents_late_upload', 'typed_draft_preserved',
                             'microphone_tracks_released', 'no_camera_or_call_peer']}
        (evidence / 'microphone-latency-fixture.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        changed.set()
        await context.close()
