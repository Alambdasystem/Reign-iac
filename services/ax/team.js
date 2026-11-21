import { createTeamCall } from '/assets/team-call.js';
import { createTeamAvatar } from '/assets/team-avatar.js';
import { createTeamRecorder } from '/assets/team-recorder.js';
import { createTeamBrowser } from '/assets/team-browser.js';

// The shared workspace uses only room-scoped endpoints. Owner tools and private
// browser/conversation recovery intentionally have no dependency in this page.
export function createTeamWorkspace(deps = {}) {
  const doc = deps.document || document, win = deps.window || window;
  const fetcher = deps.fetch || fetch, clock = deps.timers || globalThis;
  const uuid = deps.uuid || (() => crypto.randomUUID());
  const confirm = deps.confirm || (message => win.confirm(message));
  const $ = id => doc.getElementById(id);
  const later = (callback, delay) => clock.setTimeout(callback, delay);
  const cancel = id => { if (id !== undefined) clock.clearTimeout(id); };
  const drafts = new Map(), selectedDrafts = new Map(), composers = new Map(), outgoing = new Map();
  const mediaElements = new Map();
  const state = {profile: null, capabilities: {}, members: [], rooms: [], roomId: null, room: null,
    events: new Map(), seq: 0, presence: [], documents: [], assistant: {status: 'idle'},
    roomGeneration: 0, editorGeneration: 0, editor: null, connectionId: null,
    pollAbort: null, pollTimer: undefined, presenceTimer: undefined, loadingRoom: false,
    closed: false, accessLost: false, loggingOut: false, memberBusy: false, memberUncertain: false,
    call: {active: false, phase: 'idle', microphone: false, camera: false, screen: false},
    streams: {local: {}, remote: []}, attachments: [], capture: null, captureUrl: null, captureRevision: 0,
    audioEnabled: false, avatarState: 'loading', avatarMessage: '', logoutFailed: false,
    dictation: 'idle', dictationGeneration: 0, talkToReign: false};
  const callMe = {};
  let call;
  call = (deps.createCall || createTeamCall)({api: request, me: callMe, onState: renderCall,
    onStreams: renderStreams, onPresence: value => applyPresence(value?.presence || value)});
  const avatar = (deps.createAvatar || createTeamAvatar)({canvas: $('reignAvatar'), api: request,
    onStatus: value => { state.avatarState = value.state; state.avatarMessage = value.message || ''; renderAssistant(); }});
  const recorder = (deps.createRecorder || createTeamRecorder)({onState: value => {
    state.dictation = value.state; notice('dictationStatus', value.message || '', value.state === 'error'); refreshControls();
  }});
  const sharedBrowser = (deps.createBrowser || createTeamBrowser)({api: request, document: doc, window: win, timers: clock});
  function owner() { return state.profile?.me?.role === 'owner'; }
  function usable() { return !!state.roomId && !state.closed && !state.accessLost && !state.loggingOut && !state.logoutFailed && !state.loadingRoom; }
  const roomPath = (roomId = state.roomId) => '/api/team/rooms/' + encodeURIComponent(roomId);
  const escapedId = value => encodeURIComponent(String(value));

  function element(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function notice(id, message = '', error = false) {
    $(id).textContent = message; $(id).dataset.error = String(error);
  }
  function globalNotice(message, retry = false) {
    $('globalNoticeText').textContent = message;
    $('globalNotice').hidden = !message; $('retryBoot').hidden = !retry;
  }
  function initials(name) {
    return String(name || '?').trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
  }
  function timeLabel(value) {
    const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  }
  function connection(message, status = '') {
    $('connectionStatus').textContent = message; $('connectionStatus').dataset.state = status;
  }
  function csrf() {
    return doc.cookie.split(';').map(part => part.trim()).find(part => part.startsWith('ax_csrf='))?.slice(8) || '';
  }
  async function request(path, method = 'GET', body, options = {}) {
    const abort = new AbortController(), headers = {};
    if (method !== 'GET') { headers['X-CSRF-Token'] = csrf(); if (body !== undefined) headers['Content-Type'] = 'application/json'; }
    const stop = () => abort.abort();
    if (options.signal?.aborted) stop(); else options.signal?.addEventListener('abort', stop, {once: true});
    let timedOut = false;
    const timeout = later(() => { timedOut = true; abort.abort(); }, options.timeout || 30000);
    try {
      const response = await fetcher(path, {method, headers, credentials: 'same-origin', cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body), signal: abort.signal, keepalive: !!options.keepalive});
      if (response.ok && options.responseType === 'blob') return await response.blob();
      let value = {};
      try { value = await response.json(); } catch { if (response.ok && response.status !== 204) throw Error('The server returned an unreadable response.'); }
      if (!response.ok) {
        const message = typeof value.detail === 'string' ? value.detail.slice(0, 400) : response.status === 409 ? 'A newer version is already saved.' : 'That request could not be completed. Please try again.';
        const error = Object.assign(new Error(message), {status: response.status, status_code: response.status, code: value.code});
        const operationDenied = response.status === 403 && ['team_connection_required', 'team_owner_required'].includes(value.code);
        if (options.handleAuth !== false && (response.status === 401 || (response.status === 403 && !operationDenied))) void loseAccess(response.status);
        throw error;
      }
      return value;
    } catch (error) {
      if (timedOut) throw Object.assign(new Error('The request timed out. Its outcome could not be confirmed.'), {status: 0});
      throw error;
    } finally { cancel(timeout); options.signal?.removeEventListener('abort', stop); }
  }
  function refreshControls() {
    const available = usable(), pending = outgoing.get(state.roomId);
    $('newRoomButton').disabled = !state.profile || state.accessLost || state.loggingOut || state.logoutFailed;
    $('messageInput').disabled = !available || pending?.phase === 'sending' || state.talkToReign;
    $('sendMessage').disabled = !available || !!pending || state.talkToReign;
    $('askReign').disabled = !available || !!pending || state.talkToReign || state.capabilities.assistant === false || state.assistant.status === 'running';
    $('retryMessage').hidden = !pending || pending.phase !== 'uncertain';
    $('retryMessage').disabled = !available;
    $('speakReply').disabled = !available || state.capabilities.speech !== true;
    $('newDocument').disabled = !available || state.capabilities.documents === false;
    $('logoutButton').disabled = state.loggingOut;
    $('manageMembersButton').hidden = !owner();
    $('privateWorkspace').hidden = !owner();
    const editor = state.editor;
    $('saveDocument').disabled = !available || !editor || editor.saving || editor.conflict;
    $('documentTitle').disabled = !available || !!editor?.saving;
    $('documentContent').disabled = !available || !!editor?.saving;
    $('saveDocumentCopy').disabled = !available || !!editor?.saving;
    $('joinCall').disabled = !available || state.call.phase === 'joining' || state.capabilities.calls === false;
    $('listenReign').disabled = !available || state.capabilities.speech !== true;
    $('shareWithReign').disabled = !available || !!pending || state.capabilities.vision !== true || !state.call.active || (!state.call.camera && !state.call.screen);
    const recording = ['requesting', 'recording', 'processing', 'transcribing'].includes(state.dictation);
    $('dictateMessage').hidden = recording;
    $('dictateMessage').disabled = !available || !!pending || state.capabilities.transcription !== true;
    $('talkToReign').hidden = recording;
    $('talkToReign').disabled = !available || !!pending || state.capabilities.transcription !== true || state.capabilities.assistant !== true || state.capabilities.speech !== true || state.assistant.status === 'running';
    $('stopDictation').hidden = !['requesting', 'recording'].includes(state.dictation);
    $('cancelDictation').hidden = !recording;
  }
  function renderRooms() {
    $('roomList').replaceChildren();
    if (!state.rooms.length) $('roomList').append(element('p', 'empty-note', 'No rooms yet. Create the first one.'));
    for (const room of state.rooms) {
      const button = element('button', 'room-button'); button.type = 'button'; button.dataset.roomId = room.id;
      if (room.id === state.roomId) button.setAttribute('aria-current', 'page');
      button.append(element('span', 'room-symbol', '◇'), element('span', 'room-name', room.title));
      button.addEventListener('click', () => selectRoom(room.id)); $('roomList').append(button);
    }
  }
  function renderMembers() {
    $('memberList').replaceChildren(); $('managedMemberList').replaceChildren();
    for (const member of state.members) {
      if (member.active !== false) {
        const row = element('div', 'member-row'), text = element('div', 'member-text');
        text.append(element('strong', '', member.display_name), element('small', '', member.role === 'owner' ? 'Workspace owner' : member.username));
        row.append(element('span', 'initials', initials(member.display_name)), text); $('memberList').append(row);
      }
      if (!owner()) continue;
      const row = element('div', 'managed-member'), text = element('div');
      text.append(element('strong', '', member.display_name), element('small', '', `${member.username} · ${member.active === false ? 'Access revoked' : member.role === 'owner' ? 'Owner' : 'Employee'}`));
      row.append(text);
      if (member.role !== 'owner' && member.active !== false && member.id !== state.profile.me.id) {
        const revoke = element('button', 'danger', 'Revoke access'); revoke.type = 'button'; revoke.disabled = state.memberBusy;
        revoke.addEventListener('click', () => revokeMember(member)); row.append(revoke);
      }
      $('managedMemberList').append(row);
    }
  }
  function applyPresence(value) {
    if (!Array.isArray(value) || state.closed) return;
    state.presence = value;
    const people = new Map();
    for (const person of value) {
      const previous = people.get(person.user_id);
      people.set(person.user_id, {...person, microphone: !!person.microphone || !!previous?.microphone,
        camera: !!person.camera || !!previous?.camera, screen: !!person.screen || !!previous?.screen});
    }
    $('presenceCount').textContent = `${people.size} here`; $('presenceList').replaceChildren();
    for (const person of people.values()) {
      const media = [person.microphone && 'mic on', person.camera && 'camera', person.screen && 'screen'].filter(Boolean);
      $('presenceList').append(element('span', 'presence-chip', person.display_name + (media.length ? ' · ' + media.join(', ') : '')));
    }
    if (!people.size) $('presenceList').append(element('p', 'subtle-note', 'No active connections reported.'));
    call?.syncPresence?.({roomId: state.roomId, presence: value});
  }
  function renderAssistant(value) {
    if (value && typeof value === 'object') state.assistant = value;
    const phase = state.assistant.status || 'idle'; $('reignCard').dataset.state = phase;
    const available = state.capabilities.assistant !== false;
    $('assistantStatus').textContent = state.avatarState === 'speaking' ? 'Speaking with your team' : !available ? 'REIGN is unavailable' : phase === 'running' ? 'Thinking with your team' : phase === 'failed' ? 'REIGN needs a retry' : 'Ready for this room';
    $('assistantDetail').textContent = ['speaking', 'paused', 'error'].includes(state.avatarState) && state.avatarMessage ? state.avatarMessage : !available ? 'The assistant connection isn’t configured. Your room conversation and documents still work.' : phase === 'running' ? 'Working from this room’s discussion and explicitly shared views.' : phase === 'failed' ? (state.assistant.error || 'The last response could not finish. You can ask again explicitly.') : 'Ask a question, shape an idea, or bring the discussion together.';
    refreshControls();
  }
  function renderEvents(forceBottom = false) {
    const feed = $('eventFeed'), atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 70;
    feed.replaceChildren();
    const events = [...state.events.values()].sort((a, b) => a.seq - b.seq);
    if (!events.length) feed.append(element('p', 'empty-note', 'Start this room’s conversation. Everyone in Rain can pick up where you leave off.'));
    let messages = 0;
    for (const event of events) {
      const data = event.data || {};
      if (event.type === 'message' || event.type === 'assistant_message') {
        messages++;
        const assistant = event.type === 'assistant_message', row = element('article', 'message' + (assistant ? ' assistant' : ''));
        row.dataset.eventId = event.id; row.dataset.sequence = String(event.seq);
        const content = element('div', 'message-body'), meta = element('div', 'message-meta');
        meta.append(element('strong', '', assistant ? 'REIGN' : event.display_name || 'Teammate'), element('time', '', timeLabel(event.created_at)));
        const text = typeof data.text === 'string' ? data.text : Array.isArray(data.segments) ? data.segments.map(segment => segment.text || '').join('\n') : '';
        content.append(meta, element('div', 'message-content', text));
        if (assistant && data.speech_status === 'unavailable') content.append(element('p', 'subtle-note', 'The written reply is saved. Voice audio is unavailable for this reply.'));
        if (Array.isArray(data.attachment_ids) && data.attachment_ids.length) content.append(element('p', 'subtle-note', `${data.attachment_ids.length} explicitly shared image${data.attachment_ids.length === 1 ? '' : 's'}`));
        row.append(element('span', 'initials', assistant ? 'R' : initials(event.display_name)), content); feed.append(row);
      } else if (event.type === 'assistant_started') feed.append(element('p', 'room-event', 'REIGN was asked to join the discussion.'));
      else if (event.type === 'assistant_error') feed.append(element('p', 'room-event error', data.error || data.detail || 'REIGN could not finish that response.'));
      else if (event.type === 'document_saved') feed.append(element('p', 'room-event', `${event.display_name || 'A teammate'} saved a document.`));
    }
    $('chatCount').textContent = `${messages} message${messages === 1 ? '' : 's'}`;
    if (forceBottom || atBottom) { feed.scrollTop = feed.scrollHeight; $('newMessagesButton').hidden = true; }
    else $('newMessagesButton').hidden = false;
  }
  function applyEvents(events, live = false) {
    let changed = false;
    for (const event of Array.isArray(events) ? events : []) {
      if (!Number.isSafeInteger(event.seq) || event.seq < 1 || !event.id) continue;
      state.seq = Math.max(state.seq, event.seq);
      if (state.events.has(event.seq)) continue;
      state.events.set(event.seq, event); changed = true;
      const pending = outgoing.get(state.roomId);
      if (event.type === 'message' && pending && event.data?.client_message_id === pending.payload.client_message_id) finishMessage(state.roomId, pending);
      if (live && event.type === 'assistant_message') {
        if (!['requesting', 'recording'].includes(state.dictation)) void Promise.resolve(avatar.play(state.roomId, event)).catch(error => notice('callStatus', error.message, true));
        else notice('callStatus', 'REIGN’s written reply arrived. Its audio stayed off while your microphone was recording.');
        win.dispatchEvent(new CustomEvent('reign:team-assistant', {detail: {roomId: state.roomId, event}}));
      }
      if (live && event.type === 'document_saved') void refreshDocuments(state.roomId, state.roomGeneration);
    }
    while (state.events.size > 300) state.events.delete(Math.min(...state.events.keys()));
    if (changed) renderEvents(!live);
  }
  async function start() {
    if (state.closed || state.loggingOut || state.logoutFailed) return;
    state.accessLost = false; globalNotice(''); connection('Connecting');
    $('workspace').setAttribute('aria-busy', 'true');
    try {
      const profile = await request('/api/team');
      if (state.closed || state.accessLost) return;
      if (!profile.workspace?.id || !profile.me?.id) throw Error('The workspace profile could not be verified.');
      state.profile = profile; state.capabilities = profile.capabilities || {};
      Object.assign(callMe, profile.me);
      state.members = Array.isArray(profile.members) ? profile.members : [];
      state.rooms = Array.isArray(profile.rooms) ? profile.rooms : [];
      $('accountName').textContent = profile.me.display_name || profile.me.username;
      $('workspaceName').textContent = profile.workspace.name || 'Rain';
      $('storageStatus').textContent = profile.workspace.durable ? 'Conversations and documents are saved.' : 'Storage is unavailable. New work cannot be saved.';
      renderRooms(); renderMembers(); refreshControls();
      let remembered;
      try { remembered = win.sessionStorage.getItem('reign:team:last-room'); } catch {}
      const selected = state.rooms.find(room => room.id === state.roomId || room.id === remembered) || state.rooms[0];
      if (selected) await selectRoom(selected.id, {force: true});
      else { connection('Ready', 'connected'); $('roomEmpty').hidden = false; }
    } catch (error) {
      if (!state.accessLost && error.name !== 'AbortError') { globalNotice(error.message || 'The workspace is unavailable. Try again.', true); connection('Disconnected', 'error'); }
    } finally { $('workspace').setAttribute('aria-busy', 'false'); refreshControls(); }
  }
  function stopRoomPolling() {
    state.pollAbort?.abort(); state.pollAbort = null;
    cancel(state.pollTimer); state.pollTimer = undefined;
    cancel(state.presenceTimer); state.presenceTimer = undefined;
  }
  function releasePresence(roomId, connectionId) {
    if (!roomId || !connectionId) return;
    void request(roomPath(roomId) + '/presence/' + escapedId(connectionId), 'DELETE', undefined,
      {timeout: 5000, keepalive: true, handleAuth: false}).catch(() => {});
  }
  function rememberComposer() {
    if (state.roomId) composers.set(state.roomId, {text: $('messageInput').value, speak: $('speakReply').checked, attachments: state.attachments});
  }
  async function selectRoom(roomId, {force = false} = {}) {
    if (state.closed || state.accessLost || state.loggingOut || state.logoutFailed || (!force && roomId === state.roomId)) return;
    if (!state.rooms.some(room => room.id === roomId)) return;
    rememberComposer(); clearCapture(); closeDialog('shareDialog'); cancelDictation(); sharedBrowser.setRoom(null);
    avatar.stop();
    const previousRoom = state.roomId, previousConnection = state.connectionId;
    const generation = ++state.roomGeneration; state.editorGeneration++;
    stopRoomPolling(); releasePresence(previousRoom, previousConnection);
    state.roomId = roomId; state.connectionId = null; state.loadingRoom = true;
    state.editor = null; state.events = new Map(); state.seq = 0; state.documents = []; state.presence = [];
    $('roomView').hidden = true; $('roomEmpty').hidden = false;
    $('roomEmpty').querySelector('h3').textContent = 'Opening your saved room…';
    $('roomEmpty').querySelector('p').textContent = 'Restoring its conversation and documents.';
    $('roomTitle').textContent = state.rooms.find(room => room.id === roomId).title;
    connection('Opening'); renderRooms(); refreshControls();
    try {
      await call.leave({reason: 'Room changed.'});
      if (generation !== state.roomGeneration || state.closed) return;
      const snapshot = await request(roomPath(roomId));
      if (generation !== state.roomGeneration || state.closed || state.accessLost) return;
      state.room = snapshot.room; state.documents = Array.isArray(snapshot.documents) ? snapshot.documents : [];
      state.connectionId = uuid(); state.loadingRoom = false;
      state.seq = 0; applyEvents(snapshot.events);
      if (!state.events.size && Number.isSafeInteger(snapshot.last_seq)) state.seq = snapshot.last_seq;
      $('roomTitle').textContent = snapshot.room.title;
      $('roomSubtitle').textContent = 'A saved conversation, shared notes, and your team in one place.';
      $('roomEmpty').hidden = true; $('roomView').hidden = false;
      const composer = composers.get(roomId) || {};
      $('messageInput').value = composer.text || ''; $('speakReply').checked = !!composer.speak;
      state.attachments = composer.attachments || []; renderAttachments();
      notice('messageStatus', outgoing.get(roomId) ? 'A previous send still needs confirmation. Retry it with the same message ID.' : '');
      renderEvents(true); renderDocuments(); restoreEditor(roomId);
      renderAssistant(snapshot.assistant); applyPresence(snapshot.presence || []);
      sharedBrowser.setRoom({roomId, connectionId: state.connectionId, me: state.profile.me, enabled: state.capabilities.shared_browser === true});
      connection('Connected', 'connected'); refreshControls();
      try { win.sessionStorage.setItem('reign:team:last-room', roomId); } catch {}
      void heartbeat(roomId, generation); void pollRoom(roomId, generation);
      win.dispatchEvent(new CustomEvent('reign:team-room', {detail: {roomId, generation}}));
    } catch (error) {
      if (generation !== state.roomGeneration || state.closed || state.accessLost) return;
      state.loadingRoom = false; connection('Room unavailable', 'error');
      $('roomEmpty').querySelector('h3').textContent = 'This room couldn’t be opened.';
      $('roomEmpty').querySelector('p').textContent = error.message;
      globalNotice('The saved room is still there. Reconnect to try opening it again.', true);
      refreshControls();
    }
  }
  async function heartbeat(roomId, generation) {
    if (state.closed || state.accessLost || generation !== state.roomGeneration || !state.connectionId) return;
    try {
      const result = await request(roomPath(roomId) + '/presence', 'POST', {connection_id: state.connectionId,
        microphone: false, camera: false, screen: false, in_call: false}, {timeout: 12000});
      if (generation === state.roomGeneration && !state.closed) applyPresence(result.presence || []);
    } catch (error) {
      if (generation === state.roomGeneration && !state.accessLost) connection('Reconnecting', 'error');
    } finally {
      if (!state.closed && !state.accessLost && generation === state.roomGeneration) state.presenceTimer = later(() => heartbeat(roomId, generation), 10000);
    }
  }
  async function pollRoom(roomId, generation) {
    if (state.closed || state.accessLost || generation !== state.roomGeneration) return;
    const abort = new AbortController(); state.pollAbort = abort;
    let delay = 120;
    try {
      const result = await request(roomPath(roomId) + '/events?after=' + state.seq + '&wait=20', 'GET', undefined,
        {signal: abort.signal, timeout: 28000});
      if (state.closed || state.accessLost || generation !== state.roomGeneration) return;
      applyEvents(result.events, true); applyPresence(result.presence || []); renderAssistant(result.assistant);
      connection('Connected', 'connected');
    } catch (error) {
      if (error.name === 'AbortError' || generation !== state.roomGeneration || state.closed || state.accessLost) return;
      connection('Reconnecting', 'error'); delay = 2500;
    } finally {
      if (state.pollAbort === abort) state.pollAbort = null;
      if (!state.closed && !state.accessLost && !abort.signal.aborted && generation === state.roomGeneration) state.pollTimer = later(() => pollRoom(roomId, generation), delay);
    }
  }
  async function createRoom(event) {
    event.preventDefault(); if (!state.profile || state.closed) return;
    const title = $('roomTitleInput').value.trim(); if (!title) return;
    const button = $('roomCreateForm').querySelector('button'); button.disabled = true;
    notice('roomCreateStatus', 'Creating room…');
    try {
      const result = await request('/api/team/rooms', 'POST', {title});
      if (state.closed || state.accessLost || state.loggingOut || state.logoutFailed) return;
      if (!result.room?.id) throw Error('The room creation could not be confirmed. Refresh the room list before trying again.');
      state.rooms.push(result.room); $('roomTitleInput').value = ''; $('roomCreateForm').hidden = true;
      notice('roomCreateStatus'); renderRooms(); await selectRoom(result.room.id);
    } catch (error) { notice('roomCreateStatus', error.message, true); }
    finally { button.disabled = false; }
  }
  function finishMessage(roomId, pending) {
    if (outgoing.get(roomId) !== pending) return;
    outgoing.delete(roomId);
    const composer = composers.get(roomId);
    if (composer?.text.trim() === pending.payload.text) composers.set(roomId, {text: '', speak: composer.speak, attachments: []});
    if (state.roomId === roomId) {
      if ($('messageInput').value.trim() === pending.payload.text) $('messageInput').value = '';
      state.attachments = state.attachments.filter(item => !pending.payload.attachment_ids.includes(item.id));
      renderAttachments(); notice('messageStatus', ''); refreshControls();
    }
  }
  async function sendMessage(askReign = false, retry = false) {
    if (!usable()) return;
    const roomId = state.roomId, generation = state.roomGeneration;
    let pending = outgoing.get(roomId);
    if (pending && !retry) { notice('messageStatus', 'Confirm the previous send with Retry before sending another message.', true); return; }
    if (!pending) {
      const text = $('messageInput').value.trim(); if (!text) return;
      if (state.attachments.length && !askReign) { notice('messageStatus', 'Choose Ask REIGN to send this question with the images you selected, or remove the images to send a room message.', true); return; }
      if (text.length > 4000 || new TextEncoder().encode(text).length > 16384) { notice('messageStatus', 'This message is too long. Split it into smaller messages before sending.', true); return; }
      pending = {phase: 'sending', payload: {client_message_id: uuid(), text, ask_reign: !!askReign,
        speak: !!askReign && $('speakReply').checked && state.capabilities.speech === true,
        attachment_ids: state.attachments.map(item => item.id)}};
      outgoing.set(roomId, pending);
    }
    pending.phase = 'sending'; rememberComposer(); refreshControls(); notice('messageStatus', 'Sending…');
    try {
      const result = await request(roomPath(roomId) + '/messages', 'POST', pending.payload);
      finishMessage(roomId, pending);
      if (generation === state.roomGeneration && !state.closed) {
        if (result.event) applyEvents([result.event], true);
        if (result.run_id) renderAssistant({status: 'running', run_id: result.run_id});
        $('eventFeed').scrollTop = $('eventFeed').scrollHeight;
      }
    } catch (error) {
      if (outgoing.get(roomId) !== pending) return; // A room event already acknowledged this exact send.
      if (error.status && error.status < 500) outgoing.delete(roomId);
      else pending.phase = 'uncertain';
      if (generation === state.roomGeneration) notice('messageStatus', pending.phase === 'uncertain'
        ? 'This send wasn’t confirmed. Your text is kept. Retry sends the same message ID, so it won’t be duplicated.' : error.message, true);
    } finally { if (generation === state.roomGeneration) refreshControls(); }
  }
  function draftKey(roomId, id) { return roomId + '/' + id; }
  function restoreEditor(roomId) {
    state.editor = drafts.get(selectedDrafts.get(roomId)) || null;
    renderEditor();
  }
  function renderDocuments() {
    $('documentList').replaceChildren();
    for (const document of state.documents) {
      const button = element('button', 'document-tab', document.title); button.type = 'button';
      button.dataset.documentId = document.id;
      if (state.editor?.id === document.id) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => openDocument(document.id)); $('documentList').append(button);
    }
    for (const draft of drafts.values()) {
      if (draft.roomId !== state.roomId || draft.id) continue;
      const button = element('button', 'document-tab', (draft.title || 'Untitled document') + ' · unsaved'); button.type = 'button';
      if (state.editor === draft) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => { if (!usable()) return; state.editorGeneration++; state.editor = draft; selectedDrafts.set(state.roomId, draft.key); renderEditor(); });
      $('documentList').append(button);
    }
  }
  function renderEditor() {
    const editor = state.editor;
    $('documentForm').hidden = !editor; $('documentEmpty').hidden = !!editor;
    if (editor) {
      $('documentTitle').value = editor.title; $('documentContent').value = editor.content;
      $('documentVersion').textContent = editor.version ? 'Saved version ' + editor.version : 'Unsaved document';
      $('documentDirty').textContent = editor.dirty ? 'Unsaved changes' : 'Saved';
      $('documentConflict').hidden = !editor.conflict;
      notice('documentStatus', editor.status || '', !!editor.error);
    }
    renderDocuments(); refreshControls();
  }
  function editDocument() {
    const editor = state.editor; if (!editor || editor.saving) return;
    editor.title = $('documentTitle').value; editor.content = $('documentContent').value;
    editor.dirty = editor.title !== editor.savedTitle || editor.content !== editor.savedContent;
    $('documentDirty').textContent = editor.dirty ? 'Unsaved changes' : 'Saved';
  }
  function newDocument() {
    if (!usable()) return;
    const clientId = uuid(), key = draftKey(state.roomId, 'new-' + clientId);
    const editor = {key, roomId: state.roomId, id: null, clientId, title: '', content: '', version: null,
      savedTitle: '', savedContent: '', dirty: true, saving: false, conflict: false};
    drafts.set(key, editor); selectedDrafts.set(state.roomId, key); state.editor = editor;
    state.editorGeneration++; renderEditor(); $('documentTitle').focus();
  }
  async function openDocument(id, {reload = false} = {}) {
    if (!usable()) return;
    const roomId = state.roomId, roomGeneration = state.roomGeneration, generation = ++state.editorGeneration;
    const key = draftKey(roomId, id), saved = drafts.get(key);
    if (saved && !reload) {
      state.editor = saved; selectedDrafts.set(roomId, key); renderEditor(); return;
    }
    notice('documentStatus', 'Opening document…');
    try {
      const result = await request(roomPath(roomId) + '/documents/' + escapedId(id));
      if (roomGeneration !== state.roomGeneration || generation !== state.editorGeneration || state.closed) return;
      const value = result.document;
      if (!value || value.id !== id) throw Error('The document could not be verified.');
      const editor = {key, roomId, id, clientId: null, title: value.title, content: value.content, version: value.version,
        savedTitle: value.title, savedContent: value.content, dirty: false, saving: false, conflict: false};
      drafts.set(key, editor); selectedDrafts.set(roomId, key); state.editor = editor; renderEditor();
    } catch (error) { if (generation === state.editorGeneration) notice('documentStatus', error.message, true); }
  }
  async function saveDocument(event) {
    event?.preventDefault();
    const editor = state.editor; if (!usable() || !editor || editor.saving || editor.conflict) return;
    editDocument();
    const title = editor.title.trim();
    if (!title) { notice('documentStatus', 'Give the document a title before saving.', true); $('documentTitle').focus(); return; }
    if (new TextEncoder().encode(editor.content).length > 65536) { notice('documentStatus', 'This document is larger than 64 KB. Split it into smaller documents before saving.', true); return; }
    const payload = {title, content: editor.content};
    const path = roomPath(editor.roomId) + '/documents' + (editor.id ? '/' + escapedId(editor.id) : '');
    if (editor.id) payload.expected_version = editor.version; else payload.client_document_id = editor.clientId;
    editor.saving = true; editor.status = 'Saving…'; editor.error = false; renderEditor();
    try {
      const result = await request(path, editor.id ? 'PUT' : 'POST', payload);
      if (state.closed || state.accessLost || state.loggingOut || state.logoutFailed) return;
      const value = result.document;
      if (!value?.id || !Number.isSafeInteger(value.version)) throw Error('The save could not be confirmed. Your draft is still here.');
      drafts.delete(editor.key); editor.id = value.id; editor.version = value.version;
      editor.savedTitle = value.title; editor.savedContent = value.content;
      editor.title = title; editor.dirty = editor.title !== value.title || editor.content !== value.content;
      editor.key = draftKey(editor.roomId, value.id); drafts.set(editor.key, editor);
      selectedDrafts.set(editor.roomId, editor.key); editor.conflict = false;
      editor.status = editor.dirty ? 'The earlier save is confirmed. Your current edits still need saving.' : 'Saved to this room.';
      if (state.roomId === editor.roomId) {
        const existing = state.documents.findIndex(item => item.id === value.id);
        const metadata = {id: value.id, title: value.title, version: value.version, updated_at: value.updated_at};
        if (existing < 0) state.documents.push(metadata); else state.documents[existing] = metadata;
      }
    } catch (error) {
      editor.error = true; editor.dirty = true;
      if (error.status === 409) { editor.conflict = true; editor.status = 'Your draft was kept. A newer saved version needs review.'; }
      else editor.status = error.message || 'The save wasn’t confirmed. Your draft is still here.';
    } finally { editor.saving = false; if (!state.closed && !state.loggingOut && !state.accessLost && state.editor === editor) renderEditor(); }
  }
  async function refreshDocuments(roomId, generation) {
    try {
      const snapshot = await request(roomPath(roomId));
      if (roomId !== state.roomId || generation !== state.roomGeneration || state.closed) return;
      state.documents = snapshot.documents || []; renderDocuments();
      const editor = state.editor, updated = editor?.id && state.documents.find(item => item.id === editor.id);
      if (updated && updated.version > editor.version && !editor.saving) {
        editor.status = editor.dirty ? 'A teammate saved a newer version. Your draft is kept; saving will check for a conflict.' : 'A newer version is available. Load the saved version to read it.';
        notice('documentStatus', editor.status);
        if (!editor.dirty) { editor.conflict = true; $('documentConflict').hidden = false; refreshControls(); }
      }
    } catch (error) { if (generation === state.roomGeneration && !state.accessLost) notice('documentStatus', 'The document list could not refresh. Your open draft is kept.', true); }
  }
  function downloadDocument() {
    const editor = state.editor; if (!editor) return;
    editDocument();
    const filename = (editor.title.trim() || 'Untitled document').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').slice(0, 100) + '.md';
    const url = URL.createObjectURL(new Blob([editor.content], {type: 'text/markdown;charset=utf-8'}));
    const link = element('a'); link.href = url; link.download = filename; doc.body.append(link); link.click(); link.remove();
    later(() => URL.revokeObjectURL(url), 1000);
  }
  function saveDocumentCopy() {
    const previous = state.editor; if (!previous || previous.saving) return;
    editDocument(); const title = previous.title, content = previous.content;
    newDocument(); state.editor.title = title.slice(0, 113) + ' (copy)'; state.editor.content = content;
    renderEditor(); void saveDocument();
  }
  function openDialog(id) { const dialog = $(id); if (!dialog.open) dialog.showModal(); }
  function closeDialog(id) { const dialog = $(id); if (dialog.open) dialog.close(); }
  function clearCredential() {
    $('credentialUsername').value = ''; $('credentialPassword').value = ''; notice('credentialStatus');
  }
  async function refreshMembers() {
    if (!owner()) return;
    try {
      const result = await request('/api/team');
      if (state.closed || state.accessLost || state.loggingOut || state.logoutFailed) return;
      state.members = result.members || []; state.memberUncertain = false;
      renderMembers(); $('createMember').disabled = state.memberBusy;
      notice('memberStatus', 'Team access refreshed. Existing generated passwords cannot be displayed again.');
    } catch (error) { notice('memberStatus', error.message, true); }
  }
  async function createMember(event) {
    event.preventDefault(); if (!owner() || state.memberBusy || state.memberUncertain) return;
    clearCredential();
    const username = $('memberUsername').value.trim().toLowerCase(), display_name = $('memberDisplayName').value.trim();
    if (!username || !display_name) return;
    state.memberBusy = true; $('createMember').disabled = true; notice('memberStatus', 'Creating employee login…');
    try {
      const result = await request('/api/team/members', 'POST', {username, display_name});
      if (state.closed || state.accessLost || state.loggingOut || state.logoutFailed) { result.temporary_password = ''; return; }
      if (!result.member?.id || typeof result.temporary_password !== 'string') throw Error('The login creation could not be confirmed. Refresh the team before trying again.');
      state.members.push(result.member); renderMembers(); $('memberCreateForm').reset();
      $('credentialUsername').value = result.member.username; $('credentialPassword').value = result.temporary_password;
      result.temporary_password = ''; closeDialog('membersDialog'); openDialog('credentialDialog');
      notice('memberStatus', 'Employee login created. Share the copied details directly with your teammate.');
    } catch (error) {
      state.memberUncertain = !error.status || error.status >= 500;
      notice('memberStatus', state.memberUncertain ? 'Creation wasn’t confirmed. Refresh the team before trying again. A generated password cannot be retrieved later.' : error.message, true);
    } finally { state.memberBusy = false; $('createMember').disabled = state.memberUncertain; renderMembers(); }
  }
  async function revokeMember(member) {
    if (!owner() || state.memberBusy || !confirm(`Revoke ${member.display_name}’s access to Rain? Their saved messages and documents will remain.`)) return;
    state.memberBusy = true; renderMembers();
    try {
      const result = await request('/api/team/members/' + escapedId(member.id), 'PATCH', {active: false});
      const index = state.members.findIndex(value => value.id === member.id);
      if (index >= 0) state.members[index] = result.member || {...member, active: false};
      notice('memberStatus', `${member.display_name}’s access has been revoked.`);
    } catch (error) { notice('memberStatus', error.message, true); }
    finally { state.memberBusy = false; renderMembers(); }
  }
  async function copyCredential() {
    if (!$('credentialPassword').value) return;
    try {
      await win.navigator.clipboard.writeText(`REIGN Rain workspace\nSign in: ${win.location.origin}/\nUsername: ${$('credentialUsername').value}\nTemporary password: ${$('credentialPassword').value}`);
      notice('credentialStatus', 'Copied. Give these details directly to your teammate.');
    } catch { notice('credentialStatus', 'Clipboard access was blocked. Select and copy the username and password above.', true); }
  }
  function renderCall(value = {}) {
    state.call = {...state.call, ...value};
    if (state.capture && (!state.call.active || state.capture.connectionId !== state.call.connectionId || !state.call[state.capture.source])) {
      clearCapture(); notice('shareStatus', 'That view is no longer shared. Turn it on and capture a new image before attaching it.', true);
    }
    const active = !!state.call.active;
    $('joinCall').hidden = active; $('leaveCall').hidden = !active;
    $('leaveCall').disabled = state.call.phase === 'leaving';
    for (const [id, field, on, off] of [['toggleMicrophone', 'microphone', 'Mic on', 'Mic off'],
      ['toggleCamera', 'camera', 'Camera on', 'Camera off'], ['toggleScreen', 'screen', 'Stop sharing', 'Share screen']]) {
      $(id).disabled = !active || state.closed || state.loggingOut;
      $(id).setAttribute('aria-pressed', String(!!state.call[field]));
      $(id).textContent = state.call[field] ? on : off;
    }
    notice('callStatus', state.call.status || 'Join a call when you’re ready. Your camera and screen stay private until you share them.', !!state.call.error);
    $('callRelayNote').textContent = state.call.relayConfigured === false ? 'Direct connection · a relay isn’t configured, so some networks may not connect.' : state.call.relayConfigured === true ? 'Relay available for networks that need it.' : '';
    $('shareWithReign').disabled = !usable() || state.capabilities.vision !== true || !active || (!state.call.camera && !state.call.screen);
    refreshControls();
  }
  function addMediaTile(key, stream, label, kind, local, wanted) {
    if (!stream) return;
    wanted.add(key);
    let tile = mediaElements.get(key);
    if (!tile) {
      const figure = element('figure', 'media-tile'), media = element(kind === 'audio' ? 'audio' : 'video');
      media.autoplay = true; media.playsInline = true; media.muted = local || kind !== 'audio';
      const caption = element('figcaption'), name = element('span', '', label), play = element('button', 'quiet', kind === 'audio' ? 'Enable sound' : 'Play video');
      play.type = 'button'; play.hidden = true;
      play.addEventListener('click', () => { Promise.resolve(media.play()).then(() => { play.hidden = true; }).catch(() => { play.hidden = false; }); });
      caption.append(name, play); figure.append(media, caption); $('callParticipants').append(figure);
      tile = {figure, media, name, play}; mediaElements.set(key, tile);
    }
    tile.name.textContent = label;
    if (tile.media.srcObject !== stream) {
      tile.media.srcObject = stream;
      Promise.resolve(tile.media.play()).then(() => { tile.play.hidden = true; }).catch(() => { tile.play.hidden = false; });
    }
  }
  function renderStreams(value = {}) {
    state.streams = {local: value.local || {}, remote: value.remote || []};
    const wanted = new Set();
    addMediaTile('local/camera', state.streams.local.camera, 'You · camera', 'video', true, wanted);
    addMediaTile('local/screen', state.streams.local.screen, 'You · screen', 'video', true, wanted);
    for (const participant of state.streams.remote) {
      const name = participant.displayName || 'Teammate', id = participant.connectionId;
      addMediaTile(id + '/audio', participant.audio, name + ' · audio', 'audio', false, wanted);
      addMediaTile(id + '/camera', participant.cameraEnabled ? participant.camera : null, name + ' · camera', 'video', false, wanted);
      addMediaTile(id + '/screen', participant.screenEnabled ? participant.screen : null, name + ' · screen', 'video', false, wanted);
    }
    for (const [key, tile] of mediaElements) {
      if (wanted.has(key)) continue;
      tile.media.pause(); tile.media.srcObject = null; tile.figure.remove(); mediaElements.delete(key);
    }
  }
  async function enableReignAudio() {
    try {
      state.audioEnabled = !!(await avatar.unlock());
      $('listenReign').textContent = state.audioEnabled ? 'REIGN audio enabled' : 'Enable REIGN audio';
    } catch (error) { notice('callStatus', error.message || 'Audio could not be enabled. Try again.', true); }
  }
  function mediaAction(action) {
    if (!usable()) return;
    // Invoke directly from the gesture: screen capture cannot follow an await.
    try { Promise.resolve(action()).catch(error => notice('callStatus', error.message || 'The media change could not complete.', true)); }
    catch (error) { notice('callStatus', error.message || 'The media change could not complete.', true); }
  }
  function clearCapture() {
    ++state.captureRevision;
    if (state.captureUrl) URL.revokeObjectURL(state.captureUrl);
    state.captureUrl = null; state.capture = null;
    $('sharePreview').removeAttribute('src'); $('sharePreview').hidden = true;
    $('confirmShare').disabled = true; $('captureShare').disabled = !$('shareSource').options.length; notice('shareStatus');
  }
  function openShare() {
    if (!usable() || !state.call.active || state.capabilities.vision !== true) return;
    clearCapture(); $('shareSource').replaceChildren();
    for (const source of ['camera', 'screen']) {
      if (!state.call[source] || !state.streams.local[source]) continue;
      const option = element('option', '', source === 'camera' ? 'Your shared camera' : 'Your shared screen');
      option.value = source; $('shareSource').append(option);
    }
    $('captureShare').disabled = !$('shareSource').options.length;
    openDialog('shareDialog');
  }
  async function captureShare() {
    clearCapture();
    const revision = state.captureRevision;
    const roomId = state.roomId, generation = state.roomGeneration, source = $('shareSource').value;
    if (!['camera', 'screen'].includes(source) || !state.call[source]) { notice('shareStatus', 'Turn on and share that view before capturing it.', true); return; }
    $('captureShare').disabled = true;
    try {
      const result = await call.captureFrame({connectionId: state.call.connectionId, source});
      if (revision !== state.captureRevision || roomId !== state.roomId || generation !== state.roomGeneration || !$('shareDialog').open || state.closed) return;
      const blob = result.blob || result;
      if (!(blob instanceof Blob)) throw Error('That view could not be captured.');
      state.capture = {blob, source, connectionId: state.call.connectionId, roomId, capturedAt: Date.now() / 1000};
      state.captureUrl = URL.createObjectURL(blob); $('sharePreview').src = state.captureUrl; $('sharePreview').hidden = false;
      $('confirmShare').disabled = false;
      notice('shareStatus', 'Review this still image. Attach it only if you want REIGN to receive this view with your next question.');
    } catch (error) { if (revision === state.captureRevision) notice('shareStatus', error.message, true); }
    finally { if (revision === state.captureRevision) $('captureShare').disabled = false; }
  }
  function blobDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(Error('The captured image could not be read.')); reader.readAsDataURL(blob);
    });
  }
  async function confirmShare() {
    const capture = state.capture;
    if (!capture || capture.roomId !== state.roomId || !usable()) return;
    if (!state.call.active || capture.connectionId !== state.call.connectionId || !state.call[capture.source]) { clearCapture(); notice('shareStatus', 'Share that view again, then capture a new image.', true); return; }
    if (state.attachments.length >= 4) { notice('shareStatus', 'A question can include up to four images. Remove one before adding another.', true); return; }
    $('confirmShare').disabled = true; notice('shareStatus', 'Attaching the image to this room…');
    try {
      const data_url = await blobDataUrl(capture.blob);
      if (capture !== state.capture || capture.roomId !== state.roomId || state.closed) return;
      const result = await request(roomPath(capture.roomId) + '/attachments', 'POST', {data_url, source: capture.source,
        connection_id: capture.connectionId, captured_at: capture.capturedAt});
      if (capture !== state.capture || capture.roomId !== state.roomId || state.closed) return;
      if (!result.attachment?.id) throw Error('The shared image could not be confirmed.');
      state.attachments.push(result.attachment); renderAttachments(); clearCapture(); closeDialog('shareDialog');
      notice('messageStatus', 'Image attached. Add your question and choose Ask REIGN.'); $('messageInput').focus();
    } catch (error) { notice('shareStatus', error.message, true); $('confirmShare').disabled = false; }
  }
  function renderAttachments() {
    $('attachmentList').replaceChildren(); $('attachmentList').hidden = !state.attachments.length;
    for (const attachment of state.attachments) {
      const chip = element('span', 'attachment-chip', attachment.source === 'camera' ? 'Shared camera still' : 'Shared screen still');
      const remove = element('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove shared image');
      remove.addEventListener('click', () => { state.attachments = state.attachments.filter(value => value.id !== attachment.id); renderAttachments(); rememberComposer(); });
      chip.append(remove); $('attachmentList').append(chip);
    }
  }

  function cancelDictation() {
    ++state.dictationGeneration; recorder.cancel(); state.dictation = 'idle'; state.talkToReign = false;
    $('dictationTranscript').value = ''; $('dictationTranscript').hidden = true;
    notice('dictationStatus'); refreshControls();
  }
  async function dictateMessage(talk = false) {
    if (!usable() || state.capabilities.transcription !== true || outgoing.has(state.roomId)) return;
    if (talk && (state.capabilities.assistant !== true || state.capabilities.speech !== true || state.assistant.status === 'running')) return;
    if (talk && ($('messageInput').value.trim() || state.attachments.length)) { notice('dictationStatus', 'Send or clear your current draft before talking to REIGN. Your draft is kept.', true); return; }
    if (['requesting', 'recording', 'processing', 'transcribing'].includes(state.dictation)) return;
    const roomId = state.roomId, generation = ++state.dictationGeneration;
    state.talkToReign = talk;
    $('dictationTranscript').value = ''; $('dictationTranscript').hidden = true;
    try {
      avatar.stop(); // Stop speaker output before opening this microphone capture.
      // start() requests the user's own microphone directly from this gesture.
      const recording = await recorder.start({stream: state.streams.local.microphone});
      if (generation !== state.dictationGeneration || roomId !== state.roomId || !usable()) return;
      state.dictation = 'transcribing'; notice('dictationStatus', 'Transcribing your message…'); refreshControls();
      const result = await request(roomPath(roomId) + '/transcriptions', 'POST', recording, {timeout: 30000});
      recording.audio_base64 = '';
      if (generation !== state.dictationGeneration || roomId !== state.roomId || !usable()) return;
      if (typeof result.text !== 'string' || !result.text.trim()) throw Error('No speech was found. Try again or type your message.');
      const existing = $('messageInput').value;
      if (talk && existing.trim()) throw Error('A typed draft appeared while recording. It was kept; no voice turn was sent.');
      const combined = (existing ? existing + '\n' : '') + result.text.trim();
      if (combined.length > 4000 || new TextEncoder().encode(combined).length > 16384) {
        // Keep all of the transcript available without replacing the existing
        // draft or silently truncating what the person dictated.
        $('dictationTranscript').value = result.text; $('dictationTranscript').hidden = false;
        notice('dictationStatus', 'The transcript is too long to add to this draft. Copy it below and send it in smaller parts.', true);
      } else {
        $('messageInput').value = combined; rememberComposer();
        if (talk) {
          $('speakReply').checked = true;
          notice('dictationStatus', 'Sending your spoken question to REIGN…');
          await sendMessage(true);
          if (generation === state.dictationGeneration) notice('dictationStatus', outgoing.has(roomId) ? 'Your spoken question needs confirmation. Retry this message to confirm the same turn.' : $('messageInput').value ? 'Your spoken question wasn’t accepted. Review the message above before trying again.' : 'Your spoken question was sent. REIGN’s reply will appear in this room.');
        } else { notice('dictationStatus', 'Transcript added. Review it, then choose Send or Ask REIGN.'); $('messageInput').focus(); }
      }
    } catch (error) {
      if (generation === state.dictationGeneration && error.name !== 'AbortError') notice('dictationStatus', error.message || 'Transcription failed. Your message draft is kept.', true);
    } finally { if (generation === state.dictationGeneration) { state.dictation = 'idle'; state.talkToReign = false; refreshControls(); } }
  }

  function stopSession(reason) {
    ++state.roomGeneration; ++state.editorGeneration;
    stopRoomPolling();
    const roomId = state.roomId, connectionId = state.connectionId;
    state.connectionId = null;
    releasePresence(roomId, connectionId);
    clearCapture(); clearCredential(); cancelDictation(); sharedBrowser.setRoom(null);
    for (const id of ['shareDialog', 'credentialDialog', 'membersDialog']) closeDialog(id);
    // Media shutdown is immediate. Its best-effort presence DELETE must never
    // prevent the independent logout request from reaching the server.
    try { Promise.resolve(call.leave({reason})).catch(() => {}); } catch {}
    try { avatar.stop(); } catch {}
    renderStreams({local: {}, remote: []});
  }
  function loseAccess(status) {
    if (state.accessLost || state.closed || state.loggingOut) return;
    state.accessLost = true;
    stopSession('Your workspace access changed. Media has stopped.');
    $('workspace').hidden = true;
    connection('Access ended', 'error'); refreshControls();
    if (status === 401) win.location.replace('/?next=%2Fteam');
    else globalNotice('Your access to this workspace has changed. Ask the workspace owner to check your login, or log out to use another account.');
  }
  async function logout() {
    if (state.loggingOut || state.closed) return;
    editDocument();
    if (!state.logoutFailed && [...drafts.values()].some(draft => draft.dirty) && !confirm('Some document changes haven’t been saved. Log out and discard those local drafts?')) return;
    state.loggingOut = true; state.logoutFailed = false;
    $('logoutButton').textContent = 'Logging out…'; refreshControls();
    stopSession('You are logging out. Media has stopped.');
    try {
      await request('/logout', 'POST', undefined, {timeout: 15000, handleAuth: false});
      try { win.localStorage.setItem('reign:ax:signed-out', String(Date.now()) + ':' + uuid()); } catch {}
      drafts.clear(); selectedDrafts.clear(); composers.clear(); outgoing.clear();
      destroy(); win.location.replace('/?next=%2Fteam');
    } catch (error) {
      state.logoutFailed = true;
      globalNotice('Media has stopped, but log out wasn’t confirmed. Retry log out. ' + (error.message || 'Check your connection.'));
    } finally {
      state.loggingOut = false;
      $('logoutButton').textContent = state.logoutFailed ? 'Retry log out' : 'Log out'; refreshControls();
    }
  }
  const listeners = [];
  function listen(target, event, handler) {
    target.addEventListener(event, handler); listeners.push(() => target.removeEventListener(event, handler));
  }
  function destroy() {
    if (state.closed) return;
    state.closed = true; stopSession('The workspace page closed. Media has stopped.');
    try { Promise.resolve(call.dispose?.()).catch(() => {}); } catch {}
    try { avatar.destroy(); } catch {}
    try { recorder.dispose(); } catch {}
    try { sharedBrowser.dispose(); } catch {}
    for (const remove of listeners.splice(0)) remove();
  }
  listen($('newRoomButton'), 'click', () => { $('roomCreateForm').hidden = false; $('roomTitleInput').focus(); });
  listen($('cancelRoomCreate'), 'click', () => { $('roomCreateForm').hidden = true; });
  listen($('roomCreateForm'), 'submit', createRoom);
  listen($('retryBoot'), 'click', () => void start());
  listen($('logoutButton'), 'click', () => void logout());
  listen($('chatForm'), 'submit', event => { event.preventDefault(); void sendMessage(event.submitter?.dataset.action === 'ask'); });
  listen($('messageInput'), 'input', rememberComposer);
  listen($('speakReply'), 'change', rememberComposer);
  listen($('dictateMessage'), 'click', () => void dictateMessage());
  listen($('talkToReign'), 'click', () => { void enableReignAudio(); void dictateMessage(true); });
  listen($('stopDictation'), 'click', () => { void recorder.stop().catch(() => {}); });
  listen($('cancelDictation'), 'click', cancelDictation);
  listen($('retryMessage'), 'click', () => void sendMessage(false, true));
  listen($('newMessagesButton'), 'click', () => { $('eventFeed').scrollTop = $('eventFeed').scrollHeight; $('newMessagesButton').hidden = true; });
  listen($('newDocument'), 'click', newDocument);
  listen($('documentTitle'), 'input', editDocument); listen($('documentContent'), 'input', editDocument);
  listen($('documentForm'), 'submit', saveDocument);
  listen($('downloadDocument'), 'click', downloadDocument);
  listen($('saveDocumentCopy'), 'click', saveDocumentCopy);
  listen($('reloadDocument'), 'click', () => { if (state.editor?.id && (!state.editor.dirty || confirm('Discard this local draft and load the latest saved version?'))) void openDocument(state.editor.id, {reload: true}); });
  listen($('manageMembersButton'), 'click', () => { if (owner()) openDialog('membersDialog'); });
  listen($('closeMembers'), 'click', () => closeDialog('membersDialog'));
  listen($('memberCreateForm'), 'submit', createMember);
  listen($('refreshMembers'), 'click', () => void refreshMembers());
  listen($('closeCredential'), 'click', () => closeDialog('credentialDialog'));
  listen($('credentialDialog'), 'close', clearCredential);
  listen($('copyCredential'), 'click', () => void copyCredential());
  listen($('joinCall'), 'click', () => {
    if (!usable()) return;
    void enableReignAudio(); mediaAction(() => call.join({roomId: state.roomId}));
  });
  listen($('leaveCall'), 'click', () => mediaAction(() => call.leave({reason: 'You left the call. Your media is off; this room is still saved.'})));
  listen($('toggleMicrophone'), 'click', () => mediaAction(() => call.setMicrophone(!state.call.microphone)));
  listen($('toggleCamera'), 'click', () => mediaAction(() => call.setCamera(!state.call.camera)));
  listen($('toggleScreen'), 'click', () => mediaAction(() => call.setScreen(!state.call.screen)));
  listen($('listenReign'), 'click', () => void enableReignAudio());
  listen($('shareWithReign'), 'click', openShare);
  listen($('captureShare'), 'click', () => void captureShare());
  listen($('confirmShare'), 'click', () => void confirmShare());
  listen($('shareSource'), 'change', clearCapture);
  listen($('closeShare'), 'click', () => closeDialog('shareDialog'));
  listen($('shareDialog'), 'close', clearCapture);
  listen(win, 'beforeunload', event => { if (!state.closed && !state.loggingOut && [...drafts.values()].some(draft => draft.dirty)) { event.preventDefault(); event.returnValue = ''; } });
  listen(win, 'storage', event => { if (event.key === 'reign:ax:signed-out' && event.newValue) { destroy(); win.location.replace('/?next=%2Fteam'); } });
  // A page restored from the back/forward cache has deliberately disposed all
  // media and leases. Re-enter through authenticated boot, never auto-rejoin.
  win.addEventListener('pageshow', event => { if (event.persisted && state.closed) win.location.reload(); });
  listen(win, 'pagehide', destroy);
  listen(doc, 'visibilitychange', () => { if (doc.hidden) { clearCredential(); closeDialog('credentialDialog'); } });
  refreshControls();
  return {start, destroy, selectRoom, logout};
}

if (typeof document !== 'undefined') void createTeamWorkspace().start();
