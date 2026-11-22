"""Real loopback WebRTC media with a scripted provider and shared-room API.

The shipped UI/controller negotiate actual RTCPeerConnections and carry real
generated microphone/output samples. Provider turn detection, transcription,
model replies and room persistence are scripted; physical devices and the live
provider remain separate acceptance claims.
"""
import asyncio
import json
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import expect


LOOPBACK_VOICE = r"""(() => {
  const NativePeer = window.RTCPeerConnection, NativeRecorder = window.MediaRecorder;
  const fixture = window.__teamLiveVoice = {clients: [], providers: [], captures: [],
    mediaRequests: [], recorderCount: 0, sent: [], outputs: [], sequence: 0,
    utterances: [], errors: []};
  window.RTCPeerConnection = class extends NativePeer {
    constructor(...args) { super(...args); fixture.clients.push(this); }
  };
  window.MediaRecorder = class extends NativeRecorder {
    constructor(...args) { super(...args); fixture.recorderCount++; }
  };
  navigator.mediaDevices.getUserMedia = async constraints => {
    fixture.mediaRequests.push(constraints);
    if (!constraints.audio || constraints.video) throw Error('Live voice requested unexpected media');
    const context = new AudioContext(), source = context.createOscillator();
    const gain = context.createGain(), destination = context.createMediaStreamDestination();
    source.frequency.value = 440; gain.gain.value = .18;
    source.connect(gain); gain.connect(destination); source.start();
    await context.resume();
    const capture = {context, source, gain, stream: destination.stream, startedAt: performance.now()};
    fixture.captures.push(capture);
    for (const track of capture.stream.getTracks()) {
      const stop = track.stop.bind(track);
      track.stop = () => { stop(); try { source.stop(); } catch {} void context.close().catch(() => {}); };
    }
    return destination.stream;
  };
  const send = (provider, event) => {
    if (provider.channel?.readyState === 'open') provider.channel.send(JSON.stringify(event));
  };
  fixture.answer = async ({sdp, id}) => {
    const peer = new NativePeer(), context = new AudioContext();
    const source = context.createOscillator(), gain = context.createGain();
    const destination = context.createMediaStreamDestination();
    source.frequency.value = 220; gain.gain.value = 0;
    source.connect(gain); gain.connect(destination); source.start(); await context.resume();
    const provider = {id, peer, context, source, gain, destination, inputRms: 0,
      inputFrames: 0, firstInputAt: null, maximumRms: 0, outputId: null, closed: false};
    fixture.providers.push(provider);
    peer.ontrack = event => {
      provider.input = event.streams[0] || new MediaStream([event.track]);
      const input = context.createMediaStreamSource(provider.input), meter = context.createAnalyser();
      meter.fftSize = 1024; input.connect(meter); provider.meter = meter;
      const samples = new Float32Array(meter.fftSize);
      const sample = () => {
        if (provider.closed) return;
        meter.getFloatTimeDomainData(samples);
        provider.inputRms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        provider.maximumRms = Math.max(provider.maximumRms, provider.inputRms);
        if (provider.inputRms > .01) { provider.firstInputAt ??= performance.now(); provider.inputFrames++; }
        provider.timer = setTimeout(sample, 25);
      };
      sample();
    };
    peer.ondatachannel = event => {
      provider.channel = event.channel;
      provider.channel.onopen = () => send(provider, {type:'session.created', session:{id:'synthetic-' + id}});
      provider.channel.onmessage = event => {
        const message = JSON.parse(event.data); fixture.sent.push({session:id, ...message});
        if (message.type === 'conversation.item.create' && message.item?.type === 'function_call_output') {
          fixture.outputs.push(message.item); return;
        }
        if (message.type === 'output_audio_buffer.clear') {
          gain.gain.value = 0;
          if (provider.outputId) send(provider, {type:'output_audio_buffer.cleared', response_id:provider.outputId});
          provider.outputId = null; return;
        }
        if (message.type !== 'response.create') return;
        if (message.response.tool_choice !== 'none') {
          fixture.errors.push('Unexpected connection greeting or provider delegation request'); return;
        }
        const marker = 'VERIFIED_SENTENCE_JSON: ', instruction = message.response.instructions || '';
        const text = instruction.includes(marker) ? JSON.parse(instruction.split(marker).at(-1)) :
          JSON.parse(fixture.outputs.at(-1).output).answer;
        const responseId = 'loopback-output-' + ++fixture.sequence;
        provider.outputId = responseId; gain.gain.value = .2;
        send(provider, {type:'response.created', response:{id:responseId, metadata:message.response.metadata}});
        send(provider, {type:'output_audio_buffer.started', response_id:responseId});
        send(provider, {type:'response.output_audio_transcript.done', response_id:responseId, transcript:text});
        send(provider, {type:'response.done', response:{id:responseId, status:'completed'}});
      };
    };
    for (const track of destination.stream.getTracks()) peer.addTrack(track, destination.stream);
    await peer.setRemoteDescription({type:'offer', sdp});
    await peer.setLocalDescription(await peer.createAnswer());
    if (peer.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error('Loopback ICE gathering timed out')), 10000);
      const check = () => {
        if (peer.iceGatheringState === 'complete') {
          clearTimeout(timeout); peer.removeEventListener('icegatheringstatechange', check); resolve();
        }
      };
      peer.addEventListener('icegatheringstatechange', check); check();
    });
    return peer.localDescription.sdp;
  };
  fixture.begin = () => {
    const provider = fixture.providers.at(-1), index = fixture.utterances.length + 1;
    const utterance = {index, startedAt:performance.now(), finishedAt:null}; fixture.utterances.push(utterance);
    send(provider, {type:'input_audio_buffer.speech_started', item_id:'loopback-input-' + index});
  };
  fixture.finish = text => {
    const provider = fixture.providers.at(-1), utterance = fixture.utterances.at(-1);
    utterance.finishedAt = performance.now();
    const id = 'loopback-delegate-' + ++fixture.sequence;
    const call = {type:'function_call', call_id:'loopback-call-' + fixture.sequence,
      name:'delegate_to_reign', arguments:JSON.stringify({message:text})};
    send(provider, {type:'input_audio_buffer.speech_stopped', item_id:'loopback-input-' + utterance.index});
    send(provider, {type:'conversation.item.input_audio_transcription.completed', transcript:text});
    send(provider, {type:'response.created', response:{id}});
    send(provider, {type:'response.function_call_arguments.done', response_id:id,
      call_id:call.call_id, name:call.name, arguments:call.arguments});
    send(provider, {type:'response.done', response:{id, status:'completed', output:[call]}});
  };
  fixture.finishOutput = () => {
    const provider = fixture.providers.at(-1), id = provider.outputId;
    provider.gain.gain.value = 0; provider.outputId = null;
    if (id) send(provider, {type:'output_audio_buffer.stopped', response_id:id});
  };
  fixture.close = id => {
    const provider = fixture.providers.find(value => value.id === id);
    if (!provider || provider.closed) return;
    provider.closed = true; clearTimeout(provider.timer);
    provider.peer.close(); provider.destination.stream.getTracks().forEach(track => track.stop());
    try { provider.source.stop(); } catch {} void provider.context.close().catch(() => {});
  };
  fixture.snapshot = async () => {
    const provider = fixture.providers.at(-1), report = provider ? await provider.peer.getStats() : new Map();
    let bytes = 0, packets = 0, energy = 0;
    report.forEach(item => {
      if (item.type === 'inbound-rtp' && (item.kind === 'audio' || item.mediaType === 'audio')) {
        bytes += item.bytesReceived || 0; packets += item.packetsReceived || 0; energy += item.totalAudioEnergy || 0;
      }
    });
    return {clients:fixture.clients.length, providers:fixture.providers.length,
      microphoneRequests:fixture.mediaRequests.length, recorderCount:fixture.recorderCount,
      bytes, packets, energy, inputRms:provider?.inputRms, inputFrames:provider?.inputFrames,
      firstInputAt:provider?.firstInputAt, maximumRms:provider?.maximumRms,
      connected:provider?.peer.connectionState, elapsedMs:performance.now() - (fixture.captures[0]?.startedAt || performance.now()),
      liveTracks:fixture.captures.flatMap(value => value.stream.getTracks()).filter(track => track.readyState === 'live').length,
      enabledTracks:fixture.captures.flatMap(value => value.stream.getTracks()).filter(track => track.enabled && track.readyState === 'live').length,
      clientsClosed:fixture.clients.every(peer => peer.connectionState === 'closed'),
      outputClears:fixture.sent.filter(value => value.type === 'output_audio_buffer.clear').length,
      utterances:fixture.utterances, errors:fixture.errors};
  };
})();"""


async def continuous_team_voice_ui(browser, storage_state, origin, evidence):
    context = await browser.new_context(ignore_https_errors=True, storage_state=storage_state)
    await context.add_init_script(LOOPBACK_VOICE)
    page = await context.new_page(); page.set_default_timeout(20000)
    errors, offers, turns, deletes, forbidden = [], [], [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    room_ids = ['00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000082']
    rooms = [{'id': room_ids[0], 'title': 'Continuous voice fixture'},
             {'id': room_ids[1], 'title': 'Other voice fixture room'}]
    events = {room_id: [] for room_id in room_ids}; changed = asyncio.Event()
    logout_started, logout_release = asyncio.Event(), asyncio.Event()
    close_started, close_release = asyncio.Event(), asyncio.Event()
    all_sessions_closed = asyncio.Event()
    hold_close = False
    stage = 'boot'

    async def route_team(route):
        nonlocal hold_close
        request, url = route.request, urlsplit(route.request.url); path = url.path
        if path == '/api/team':
            result = {'workspace': {'id':'rain', 'name':'Workspace', 'durable':True},
                      'me': {'id':'synthetic-owner', 'username':'synthetic', 'role':'owner', 'display_name':'Synthetic caller'},
                      'members': [], 'rooms': rooms,
                      'capabilities': {'assistant':True, 'speech':True, 'live_voice':True, 'transcription':True,
                                       'calls':True, 'documents':True, 'shared_browser':False}}
        else:
            room_id = next((value for value in room_ids if '/rooms/' + value in path), None)
            assert room_id, 'Unexpected room API: ' + path
            if path.endswith('/voice/offer'):
                payload = request.post_data_json; offers.append(payload)
                answer = await page.evaluate('__teamLiveVoice.answer', {'sdp':payload['sdp'], 'id':payload['client_session_id']})
                result = {'sdp':answer, 'type':'answer', 'voice_session_id':payload['client_session_id'],
                          'model':'scripted-loopback-provider', 'max_duration_seconds':3600}
            elif path.endswith('/voice'):
                result = {'configured':True, 'transport':'webrtc', 'model':'scripted-loopback-provider', 'max_duration_seconds':3600}
            elif '/voice/sessions/' in path and request.method == 'DELETE':
                session_id = path.rsplit('/',1)[-1]; deletes.append(session_id); close_started.set()
                if len(set(deletes)) == 3: all_sessions_closed.set()
                if hold_close: await close_release.wait()
                await page.evaluate('__teamLiveVoice.close', session_id)
                result = {'closed':True}
            elif path.endswith('/turns'):
                payload = request.post_data_json; turns.append(payload)
                session_id = path.split('/voice/sessions/')[1].split('/')[0]
                run_id = '00000000-0000-4000-8000-' + f'{100 + len(turns):012d}'
                sequence = len(events[room_id]) + 1
                message = {'id':'loopback-message-' + str(sequence), 'seq':sequence, 'type':'message',
                           'display_name':'Synthetic caller', 'created_at':1700000000,
                           'data':{**payload, 'input_source':'voice', 'voice_session_id':session_id}}
                expression = ['warm','concerned','thoughtful'][(len(turns)-1)%3]
                answer = 'Synthetic room answer number ' + str(len(turns)) + '.'
                reply = {'id':'loopback-reply-' + str(sequence), 'seq':sequence+1, 'type':'assistant_message',
                         'display_name':'REIGN', 'created_at':1700000001,
                         'data':{'run_id':run_id, 'voice_session_id':session_id, 'text':answer, 'speech_status':'ready',
                                 'segments':[{'text':answer,'expression':expression,
                                              'audio_id':'00000000-0000-4000-8000-000000000099'}]}}
                events[room_id].extend([message,reply]); changed.set()
                result = {'event':message, 'run_id':run_id}
            elif path.endswith('/transcriptions') or '/audio/' in path or path.endswith('/messages'):
                forbidden.append(path); raise AssertionError('Live microphone used a recording or duplicate playback path: ' + path)
            elif path.endswith('/events'):
                after = int(parse_qs(url.query).get('after',['0'])[0])
                visible = [event for event in events[room_id] if event['seq'] > after]
                if not visible:
                    changed.clear()
                    try: await asyncio.wait_for(changed.wait(),1)
                    except asyncio.TimeoutError: pass
                    visible = [event for event in events[room_id] if event['seq'] > after]
                result = {'events':visible, 'presence':[], 'assistant':{'status':'idle'}}
            elif '/presence' in path:
                result = {'presence':[]}
            elif path.endswith('/rooms/' + room_id):
                result = {'room':next(room for room in rooms if room['id']==room_id),
                          'events':events[room_id], 'last_seq':len(events[room_id]),
                          'documents':[], 'presence':[], 'assistant':{'status':'idle'}}
            else: raise AssertionError('Unexpected continuous voice API: ' + path)
        await route.fulfill(status=200, json=result)

    async def logout_route(route):
        logout_started.set(); await logout_release.wait()
        await route.fulfill(status=200, json={'ok':True})

    await context.route(origin + '/api/team**', route_team)
    await context.route(origin + '/logout', logout_route)
    try:
        await page.goto(origin + '/team')
        await expect(page.locator('#reignAvatar')).to_have_attribute('data-ready','true')
        stage = 'real_webrtc_negotiation_and_upstream_audio'
        await page.locator('#talkToReign').click()
        await expect(page.locator('#liveVoiceDiagnostics')).to_have_attribute('data-active','true')
        await page.wait_for_function("""() => __teamLiveVoice.providers.at(-1)?.inputRms > .025 &&
            __teamLiveVoice.providers.at(-1)?.channel?.readyState === 'open'""")
        await expect(page.locator('#dictateMessage')).to_be_disabled()
        assert len(offers) == 1 and not turns
        initial = await page.evaluate('__teamLiveVoice.snapshot()')
        await page.evaluate('__teamLiveVoice.begin()')
        await page.wait_for_function('frames => __teamLiveVoice.providers[0].inputFrames > frames + 8',
                                     arg=initial['inputFrames'])
        before_end = await page.evaluate('__teamLiveVoice.snapshot()')
        assert before_end['bytes'] > 0 and before_end['packets'] > 0 and before_end['maximumRms'] > .025
        assert before_end['bytes'] > initial['bytes'] and before_end['inputFrames'] > initial['inputFrames']
        assert before_end['utterances'][0]['finishedAt'] is None and not turns, 'Audio did not stream before utterance completion'
        stage = 'first_verified_reply'
        await page.evaluate("__teamLiveVoice.finish('Synthetic first spoken question.')")
        await page.wait_for_function("""() => Number(document.querySelector('#reignAvatar').dataset.mouth) > .05 &&
            document.querySelector('#reignAvatar').dataset.expression === 'warm'""")
        playback_time = await page.locator('#reignLiveAudio').evaluate("""audio => {
            if (audio.paused || audio.muted || !audio.srcObject?.active) throw Error('Live native output was not playing');
            return audio.currentTime;
        }""")
        await page.wait_for_function("time => document.querySelector('#reignLiveAudio').currentTime > time + .1", arg=playback_time)

        # Barge-in travels over the actual SCTP channel while incoming audio
        # continues on the same real media connection.
        clears = (await page.evaluate('__teamLiveVoice.snapshot()'))['outputClears']
        stage = 'barge_in_and_subsequent_turns'
        await page.evaluate('__teamLiveVoice.begin()')
        await page.wait_for_function("""count => __teamLiveVoice.sent.filter(value => value.type === 'output_audio_buffer.clear').length > count &&
            Number(document.querySelector('#reignAvatar').dataset.mouth) < .001""", arg=clears)
        await page.evaluate("__teamLiveVoice.finish('Synthetic second spoken question.')")
        await page.wait_for_function("""() => Number(document.querySelector('#reignAvatar').dataset.mouth) > .05 &&
            document.querySelector('#reignAvatar').dataset.expression === 'concerned'""")
        await page.evaluate('__teamLiveVoice.finishOutput()')
        await page.wait_for_function("document.querySelector('#reignAvatar').dataset.speaking === 'false'")
        await page.evaluate('__teamLiveVoice.begin()')
        await page.evaluate("__teamLiveVoice.finish('Synthetic third spoken question.')")
        await page.wait_for_function("""() => Number(document.querySelector('#reignAvatar').dataset.mouth) > .05 &&
            document.querySelector('#reignAvatar').dataset.expression === 'thoughtful'""")
        await page.evaluate('__teamLiveVoice.finishOutput()')
        await page.wait_for_function("document.querySelector('#reignAvatar').dataset.speaking === 'false'")
        stage = 'continuous_capture_beyond_twenty_seconds'
        await page.wait_for_function("""() => performance.now() - __teamLiveVoice.captures[0].startedAt > 21500 &&
            __teamLiveVoice.providers[0].inputRms > .025""", timeout=30000)
        continuous = await page.evaluate('__teamLiveVoice.snapshot()')
        assert continuous['bytes'] > before_end['bytes'] and continuous['inputFrames'] > before_end['inputFrames']
        assert continuous['elapsedMs'] > 21500 and continuous['liveTracks'] == 1
        assert len(offers) == 1 and len(turns) == 3 and continuous['microphoneRequests'] == 1
        assert continuous['recorderCount'] == 0 and not forbidden
        stage = 'mute_and_unmute'
        await page.locator('#muteReignMic').click()
        await expect(page.locator('#muteReignMic')).to_have_attribute('aria-pressed','true')
        await page.wait_for_function('__teamLiveVoice.providers.at(-1).inputRms < .002')
        assert (await page.evaluate('__teamLiveVoice.snapshot()'))['enabledTracks'] == 0
        await page.locator('#muteReignMic').click()
        await page.wait_for_function('__teamLiveVoice.providers.at(-1).inputRms > .025')
        assert len(offers) == 1

        hold_close = True
        stage = 'off_and_room_change_cleanup'
        await page.locator('#stopReignMic').click()
        await asyncio.wait_for(close_started.wait(),5)
        await page.wait_for_function("""() => __teamLiveVoice.captures.every(value => value.stream.getTracks().every(track => track.readyState === 'ended')) &&
            __teamLiveVoice.clients.every(peer => peer.connectionState === 'closed') &&
            document.querySelector('#reignLiveAudio').srcObject === null""")
        close_release.set(); hold_close = False
        await expect(page.locator('#talkToReign')).to_be_enabled()
        await page.locator('#talkToReign').click()
        await page.wait_for_function('__teamLiveVoice.providers.length === 2 && __teamLiveVoice.providers[1].inputRms > .025')
        await page.locator(f'[data-room-id="{room_ids[1]}"]').click()
        await expect(page.locator('#roomTitle')).to_have_text(rooms[1]['title'])
        await page.wait_for_function("__teamLiveVoice.captures.every(value => value.stream.getTracks().every(track => track.readyState === 'ended'))")
        await expect(page.locator('#talkToReign')).to_be_enabled()
        await page.locator('#talkToReign').click()
        await page.wait_for_function('__teamLiveVoice.providers.length === 3 && __teamLiveVoice.providers[2].inputRms > .025')
        stage = 'logout_cleanup'
        await page.locator('#logoutButton').click()
        await asyncio.wait_for(logout_started.wait(),5)
        await page.wait_for_function("""() => __teamLiveVoice.captures.every(value => value.stream.getTracks().every(track => track.readyState === 'ended')) &&
            __teamLiveVoice.clients.every(peer => peer.connectionState === 'closed') &&
            document.querySelector('#reignLiveAudio').srcObject === null""")
        await asyncio.wait_for(all_sessions_closed.wait(),5)
        assert not forbidden and not errors and not await page.evaluate('__teamLiveVoice.errors')
        assert len(offers) == 3 and len(set(deletes)) == 3
        result = {'status':'passed', 'mode':'real_loopback_webrtc_scripted_provider_and_room_api',
                  'physical_microphone_or_speaker':'not_tested', 'live_provider':'not_tested',
                  'streaming_before_utterance_end':{'received_bytes':before_end['bytes'], 'received_packets':before_end['packets'],
                                                    'maximum_input_rms':before_end['maximumRms']},
                  'continuous_capture':{'elapsed_ms':round(continuous['elapsedMs']), 'received_bytes':continuous['bytes'],
                                        'received_packets':continuous['packets'], 'input_frames':continuous['inputFrames'],
                                        'turns':3, 'offers':1, 'microphone_requests':1, 'recorders':0},
                  'checks':['actual_peer_sdp_and_rtp_audio','audio_reaches_receiver_before_utterance_end',
                            'continuous_microphone_over_twenty_seconds','three_turns_one_connection',
                            'barge_in_clears_native_output_and_face','native_audio_clock_and_model_expressions',
                            'no_recordings_or_transcription_uploads','no_duplicate_cached_reply_audio',
                            'mute_and_unmute_without_reconnect','off_cleanup_before_server_ack',
                            'room_change_releases_microphone','logout_releases_microphone_before_ack']}
        (evidence / 'continuous-team-voice-fixture.json').write_text(json.dumps(result,indent=2) + '\n')
        logout_release.set()
        return result
    except Exception:
        diagnostics = {'status':'failed', 'stage':stage, 'offers':len(offers), 'turns':len(turns),
                       'session_deletes':len(set(deletes)), 'forbidden_request_count':len(forbidden),
                       'page_errors':errors}
        try:
            diagnostics['browser'] = await page.evaluate("""() => ({
              clients: __teamLiveVoice.clients.map(peer => ({connection:peer.connectionState, ice:peer.iceConnectionState})),
              providers: __teamLiveVoice.providers.map(value => ({connection:value.peer.connectionState,
                ice:value.peer.iceConnectionState, inputFrames:value.inputFrames, inputRms:value.inputRms,
                maximumRms:value.maximumRms, channel:value.channel?.readyState})),
              recorderCount:__teamLiveVoice.recorderCount,
              mediaRequests:__teamLiveVoice.mediaRequests.length,
              errors:__teamLiveVoice.errors,
              status:document.querySelector('#liveVoiceStatus')?.textContent,
              avatar:document.querySelector('#reignAvatar')?.dataset
            })""")
        except Exception:
            diagnostics['browser'] = 'unavailable'
        (evidence / 'continuous-team-voice-failure.json').write_text(json.dumps(diagnostics,indent=2) + '\n')
        print('CONTINUOUS_VOICE_FIXTURE_FAILURE ' + json.dumps(diagnostics), flush=True)
        raise
    finally:
        changed.set(); close_release.set(); logout_release.set()
        await context.close()
