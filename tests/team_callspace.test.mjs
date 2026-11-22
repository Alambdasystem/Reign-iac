import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../services/ax/team-callspace.js', import.meta.url), 'utf8');
const {createCallspaceWorkspace, callspaceModeForProgress} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

// A small moving-node DOM exercises the presentation controller itself. Queries
// walk the current tree, so replacing a media node cannot pass an identity check.
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) { this.listeners.get(type)?.delete(handler); }
  emit(type, extra = {}) {
    const event = {type, target: this, currentTarget: this, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra};
    let target = this;
    do {
      event.currentTarget = target;
      for (const listener of [...target.listeners.get(type) || []]) listener(event);
      target = event.stopped || !event.bubbles ? null : target.parentNode;
    } while (target);
    return event;
  }
}
const dataKey = name => name.slice(5).replace(/-([a-z])/g, (_, value) => value.toUpperCase());
class Node extends Target {
  constructor(tag, doc) {
    super(); this.tagName = tag.toUpperCase(); this.nodeType = tag === '#comment' ? 8 : 1;
    this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; this.attributes = new Map();
    this.id = ''; this.className = ''; this.dataset = {}; this.hidden = false; this.disabled = false;
    this.inert = false; this.open = false; this.value = ''; this.rows = 1; this.placeholder = '';
    this.scrollTop = 0; this.scrollHeight = 1000; this.clientHeight = 400; this._text = '';
    this.style = {setProperty(name, value) { this[name] = String(value); },
      removeProperty(name) { delete this[name]; }, getPropertyValue(name) { return this[name] || ''; }};
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(value => !names.includes(value)).join(' '); },
    };
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get parentElement() { return this.parentNode?.nodeType === 1 ? this.parentNode : null; }
  get isConnected() { let node = this; while (node.parentNode) node = node.parentNode; return node.nodeType === 9; }
  get textContent() { return this._text + this.childNodes.map(node => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get innerHTML() { return this._html || ''; }
  set innerHTML(value) { this.replaceChildren(); this._html = String(value); }
  getAttribute(name) {
    if (name === 'id') return this.id || null;
    if (name === 'class') return this.className || null;
    if (name.startsWith('data-')) return this.dataset[dataKey(name)] ?? null;
    if (['hidden', 'disabled', 'open', 'inert'].includes(name)) return this[name] ? '' : null;
    if (name === 'placeholder') return this.attributes.has(name) ? this.placeholder : null;
    if (name === 'rows') return this.attributes.has(name) ? String(this.rows) : null;
    return this.attributes.get(name) ?? null;
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'id') this.id = String(value);
    else if (name === 'class') this.className = String(value);
    else if (name.startsWith('data-')) this.dataset[dataKey(name)] = String(value);
    else if (['hidden', 'disabled', 'open', 'inert'].includes(name)) this[name] = true;
    else if (name === 'placeholder') this.placeholder = String(value);
    else if (name === 'rows') this.rows = Number(value);
  }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name.startsWith('data-')) delete this.dataset[dataKey(name)];
    else if (['hidden', 'disabled', 'open', 'inert'].includes(name)) this[name] = false;
    else if (name === 'id') this.id = '';
    else if (name === 'placeholder') this.placeholder = '';
  }
  append(...nodes) {
    for (const node of nodes) { node.remove(); node.parentNode = this; this.childNodes.push(node); }
  }
  insertBefore(node, before) {
    if (!before) { this.append(node); return node; }
    node.remove(); const index = this.childNodes.indexOf(before); assert.ok(index >= 0);
    node.parentNode = this; this.childNodes.splice(index, 0, node); return node;
  }
  replaceChildren(...nodes) { for (const node of this.childNodes) node.parentNode = null; this.childNodes = []; this._text = ''; this.append(...nodes); }
  replaceWith(node) { const parent = this.parentNode; if (parent) { parent.insertBefore(node, this); this.remove(); } }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this); this.parentNode = null; }
  contains(node) { while (node) { if (node === this) return true; node = node.parentNode; } return false; }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      const attrs = [...part.matchAll(/\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]/g)];
      part = part.replace(/\[[^\]]+\]/g, '');
      if (attrs.some(([, key, value]) => value === undefined ? this.getAttribute(key) === null : this.getAttribute(key) !== value)) return false;
      if (part.startsWith('#')) return this.id === part.slice(1);
      if (part.startsWith('.')) return part.slice(1).split('.').every(name => this.classList.contains(name));
      return !part || part === '*' || part.toUpperCase() === this.tagName;
    });
  }
  querySelectorAll(selector) {
    return this.children.flatMap(node => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let node = this; node?.nodeType === 1; node = node.parentNode) if (node.matches(selector)) return node; return null; }
  focus() { this.ownerDocument.activeElement = this; this.emit('focus'); }
  click() { if (!this.disabled) this.emit('click', {bubbles: true}); }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
  getBoundingClientRect() { return {x: 0, y: 0, top: 0, left: 0, width: 390, height: 844, right: 390, bottom: 844}; }
  setPointerCapture(id) { this.pointerId = id; }
  hasPointerCapture(id) { return this.pointerId === id; }
  releasePointerCapture(id) { if (this.pointerId === id) { this.pointerId = null; this.emit('lostpointercapture'); } }
  requestSubmit(submitter) { this.emit('submit', {submitter}); }
}

function harness({reducedMotion = false, withLiveCamera = true} = {}) {
  const doc = new Node('#document'); doc.nodeType = 9; doc.ownerDocument = doc;
  doc.createElement = tag => new Node(tag, doc);
  doc.createComment = () => new Node('#comment', doc);
  doc.getElementById = id => doc.querySelector('#' + id);
  doc.body = doc.createElement('body'); doc.append(doc.body); doc.activeElement = doc.body;
  const win = new Target(), frames = new Map(), timers = new Map(), observers = []; let job = 0;
  Object.assign(win, {innerWidth: 390, innerHeight: 844, visualViewport: new Target(),
    matchMedia: () => ({matches: reducedMotion}),
    requestAnimationFrame: fn => { frames.set(++job, fn); return job; }, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => { timers.set(++job, fn); return job; }, clearTimeout: id => timers.delete(id),
    MutationObserver: class {
      constructor(callback) { this.callback = callback; this.connected = true; this.targets = new Map(); observers.push(this); }
      observe(target, options) { this.targets.set(target, options); } disconnect() { this.connected = false; }
    },
    ResizeObserver: class { observe() {} disconnect() {} },
  });
  doc.defaultView = win;
  const make = (tag, id, parent, className = '') => {
    const node = doc.createElement(tag); node.id = id; node.className = className; parent.append(node); return node;
  };
  const header = make('header', '', doc.body, 'app-header'); make('button', 'logoutButton', header);
  const workspace = make('main', 'workspace', doc.body);
  const sidebar = make('aside', '', workspace, 'sidebar');
  make('button', 'manageMembersButton', sidebar);
  make('button', 'testRoom', sidebar, 'room-button');
  const roomShell = make('section', '', workspace, 'room-shell');
  make('header', '', roomShell, 'room-header');
  make('div', 'roomEmpty', roomShell);
  const roomView = make('div', 'roomView', roomShell);
  const stage = make('section', '', roomView, 'room-stage');
  const card = make('article', 'reignCard', stage);
  const media = make('div', 'reignMedia', card); const canvas = make('canvas', 'reignAvatar', media);
  canvas.width = canvas.height = 256;
  const copy = make('div', '', card, 'reign-copy'); make('strong', 'assistantStatus', copy).textContent = 'Ready';
  make('p', 'assistantDetail', copy); make('button', 'listenReign', copy);
  const callPanel = make('div', '', stage, 'call-panel');
  for (const id of ['joinCall', 'leaveCall', 'toggleMicrophone', 'toggleCamera']) make('button', id, callPanel);
  make('p', 'callStatus', callPanel).textContent = 'Call ready';
  const sharing = make('div', '', callPanel, 'share-actions'); make('button', 'shareWithReign', sharing);
  const participants = make('div', 'callParticipants', roomView); make('video', 'remoteParticipant', participants);
  const livePanel = make('section', '', roomView, 'live-voice-panel');
  for (const id of ['talkToReign', 'muteReignMic', 'stopReignMic']) make('button', id, livePanel);
  make('p', 'liveVoiceStatus', livePanel).textContent = 'Microphone off';
  make('span', 'liveVoiceDiagnostics', livePanel); make('audio', 'reignLiveAudio', livePanel);
  const tools = make('div', '', roomView, 'dictation-tools'); make('button', 'openCamera', tools);
  const grid = make('div', '', roomView, 'work-grid');
  const conversation = make('section', '', grid, 'conversation');
  const feed = make('div', 'eventFeed', conversation); make('article', 'savedMessage', feed).textContent = 'A saved room message';
  const form = make('form', 'chatForm', conversation);
  const input = make('textarea', 'messageInput', form); input.setAttribute('rows', '3'); input.setAttribute('placeholder', 'Message the room');
  input.value = 'An unsent draft\nwith a second line'; input.selectionStart = 4; input.selectionEnd = 9;
  make('div', 'attachmentList', form).hidden = true;
  const composerActions = make('div', '', form, 'composer-actions');
  const label = make('label', '', composerActions); make('input', 'speakReply', label);
  make('button', 'sendMessage', composerActions); make('button', 'askReign', composerActions);
  make('p', 'messageStatus', conversation); make('button', 'retryMessage', conversation);
  make('p', '', conversation, 'composer-note');
  make('section', 'roomDocuments', grid, 'documents'); make('details', 'browserPanel', roomView);
  const dialog = make('dialog', 'cameraDialog', doc.body); make('video', 'cameraPreview', dialog);
  if (withLiveCamera) { const view = make('div', 'liveCameraView', roomView); make('video', 'liveCameraPreview', view); }
  const node = id => { const found = doc.getElementById(id); assert.ok(found, 'missing node ' + id); return found; };
  node('talkToReign').disabled = true; node('muteReignMic').hidden = true; node('stopReignMic').hidden = true;
  node('leaveCall').hidden = true; node('toggleMicrophone').disabled = true;
  const mediaNodes = [canvas, node('reignLiveAudio'), node('remoteParticipant'), node('cameraPreview')];
  if (withLiveCamera) mediaNodes.push(node('liveCameraPreview'));
  const mediaTracks = [];
  for (const mediaNode of mediaNodes.filter(value => value.tagName !== 'CANVAS')) {
    const track = {readyState: 'live', enabled: true, stops: 0, stop() { this.stops++; this.readyState = 'ended'; }};
    mediaTracks.push(track); mediaNode.srcObject = {getTracks: () => [track]}; mediaNode.currentTime = 12.5; mediaNode.paused = false;
    mediaNode.pause = () => { throw Error('Presentation must not pause media'); };
    mediaNode.play = () => { throw Error('Presentation must not restart media'); };
  }
  const originalNodes = [header, sidebar, roomView, conversation, card, form, input, feed, ...mediaNodes];
  const parents = new Map(originalNodes.map(value => [value, value.parentNode]));
  const clicks = new Map();
  for (const id of ['talkToReign', 'muteReignMic', 'stopReignMic', 'toggleMicrophone', 'leaveCall']) {
    node(id).addEventListener('click', () => clicks.set(id, (clicks.get(id) || 0) + 1));
  }
  const controller = createCallspaceWorkspace({document: doc, window: win});
  return {controller, doc, win, node, mediaNodes, mediaTracks, parents, originalNodes, clicks,
    sync(changedNode) {
      for (const observer of observers) {
        if (!observer.connected) continue;
        if (!changedNode || [...observer.targets].some(([target, options]) => target === changedNode || (options.subtree && target.contains(changedNode)))) observer.callback([]);
      }
    },
    frame() { for (const [id, fn] of [...frames]) { frames.delete(id); fn(); } },
  };
}

function assertPreserved(h, original) {
  for (const node of h.originalNodes) assert.ok(node.isConnected, 'original node detached: ' + (node.id || node.className));
  for (const {node, stream, currentTime} of original) {
    assert.equal(h.node(node.id), node, 'media node replaced'); assert.equal(node.srcObject, stream, 'stream replaced');
    assert.equal(node.currentTime, currentTime, 'playback clock reset');
  }
  assert.ok(h.mediaTracks.every(track => track.readyState === 'live' && track.stops === 0));
  assert.equal(h.clicks.size, 0, 'presentation change dispatched a media lifecycle action');
}
const snapshotMedia = h => h.mediaNodes.map(node => ({node, stream: node.srcObject, currentTime: node.currentTime}));

test('view thresholds clamp invalid input and choose the three supported sizes', () => {
  for (const value of [-1, 0, .249, NaN, undefined, Infinity]) assert.equal(callspaceModeForProgress(value), 'chat');
  for (const value of [.25, .5, .749]) assert.equal(callspaceModeForProgress(value), 'split');
  for (const value of [.75, 1, 3]) assert.equal(callspaceModeForProgress(value), 'call');
});

test('initializing and switching views keep the actual canvas, streams, draft, feed and scroll position', () => {
  const h = harness(), media = snapshotMedia(h), feed = h.node('eventFeed'), message = h.node('savedMessage');
  const draft = h.node('messageInput').value;
  assert.equal(createCallspaceWorkspace({document: h.doc, window: h.win}), h.controller, 'boot must be idempotent');
  assert.equal(h.doc.querySelectorAll('#callspaceShell').length, 1);
  assert.equal(h.node('reignCard').parentNode, h.node('callspaceFace'));
  feed.scrollTop = 172;
  h.controller.setMode('split'); assert.equal(h.controller.getMode(), 'split');
  message.focus(); h.controller.setMode('call');
  assert.equal(h.node('callspaceTranscript').inert, true);
  assert.equal(h.node('callspaceTranscript').getAttribute('aria-hidden'), 'true');
  assert.equal(h.doc.activeElement, h.node('callspaceFace'), 'focus must leave hidden transcript');
  feed.scrollTop = 0; // Model the browser clamping a hidden scroll container.
  h.controller.setMode('chat');
  assert.equal(feed.scrollTop, 172); assert.equal(feed.children[0], message);
  assert.equal(h.node('messageInput').value, draft);
  assert.equal(h.node('messageInput').selectionStart, 4); assert.equal(h.node('messageInput').selectionEnd, 9);
  assert.equal(h.node('callspaceTranscript').inert, false);
  assert.equal(h.node('callspaceTranscript').getAttribute('aria-hidden'), 'false');
  assert.equal(h.node('reignAvatar').width, 256); assert.equal(h.node('reignAvatar').height, 256);
  assertPreserved(h, media); h.controller.destroy();
});

test('dragging enlarges and shrinks the same call; cancellation and resize restore the starting view', () => {
  const h = harness(), face = h.node('callspaceFace'), media = snapshotMedia(h);
  const pointer = (type, y, extra = {}) => face.emit(type, {pointerId: 7, clientY: y, button: 0, ...extra});
  pointer('pointerdown', 100, {button: 2}); pointer('pointermove', 600); pointer('pointerup', 600);
  assert.equal(h.controller.getMode(), 'chat', 'secondary buttons must not drag');
  pointer('pointerdown', 100); pointer('pointermove', 300, {pointerId: 8});
  assert.equal(h.controller.getMode(), 'chat', 'another pointer must not resize the call');
  pointer('pointermove', 430); assert.equal(h.controller.getMode(), 'call');
  pointer('pointerup', 430); face.click();
  assert.equal(h.controller.getMode(), 'call', 'the synthetic click after a drag must not toggle modes');
  pointer('pointerdown', 700); pointer('pointermove', 200); pointer('pointerup', 200); face.click();
  assert.equal(h.controller.getMode(), 'chat');
  h.controller.setMode('split');
  pointer('pointerdown', 200); pointer('pointermove', 500); pointer('pointercancel', 500);
  assert.equal(h.controller.getMode(), 'split'); assert.equal(face.hasPointerCapture(7), false);
  pointer('pointerdown', 200); pointer('pointermove', 500); h.win.emit('resize'); h.frame();
  assert.equal(h.controller.getMode(), 'split'); assert.equal(h.node('callspaceShell').getAttribute('data-dragging'), null);
  assertPreserved(h, media); h.controller.destroy();
});

test('keyboard full-face and shrink remain usable without a drawer or media restart', () => {
  const h = harness({reducedMotion: true}), face = h.node('callspaceFace'), media = snapshotMedia(h);
  for (const [key, expected] of [['ArrowDown', 'split'], ['End', 'call'], ['ArrowUp', 'split'], ['Home', 'chat'], ['Enter', 'split'], [' ', 'call']]) {
    assert.equal(face.emit('keydown', {key}).defaultPrevented, true);
    assert.equal(h.controller.getMode(), expected);
  }
  assert.equal(h.node('callspaceShell').dataset.reducedMotion, 'true');
  h.controller.setMode('unsupported'); assert.equal(h.controller.getMode(), 'call');
  h.node('callspaceEnd').click(); assert.equal(h.controller.getMode(), 'chat', 'ending an idle expanded view must only return to chat');
  h.controller.setMode('call');
  h.node('messageInput').focus(); assert.equal(h.controller.getMode(), 'chat', 'typing restores the conversation');
  assertPreserved(h, media); h.controller.destroy();
});

test('the microphone and end proxies retain capability and in-progress action fences', () => {
  const h = harness(), mic = h.node('callspaceMic'), end = h.node('callspaceEnd');
  assert.equal(mic.disabled, true); assert.equal(end.disabled, true);
  mic.emit('click'); assert.equal(h.clicks.size, 0);
  h.node('talkToReign').disabled = false; h.sync();
  assert.equal(mic.disabled, false); mic.click(); assert.equal(h.clicks.get('talkToReign'), 1);
  h.node('talkToReign').hidden = true; h.node('stopReignMic').hidden = false;
  h.node('muteReignMic').hidden = false; h.node('liveVoiceDiagnostics').dataset.active = 'true';
  h.sync(); assert.equal(mic.dataset.scope, 'voice'); assert.equal(end.disabled, false);
  mic.click(); assert.equal(h.clicks.get('muteReignMic'), 1);
  h.node('muteReignMic').disabled = true; h.node('stopReignMic').disabled = true; h.sync();
  assert.equal(mic.disabled, true); assert.equal(end.disabled, true);
  mic.emit('click'); end.emit('click');
  assert.equal(h.clicks.get('muteReignMic'), 1); assert.equal(h.clicks.get('stopReignMic'), undefined);
  h.node('muteReignMic').disabled = false; h.node('stopReignMic').disabled = false;
  h.node('muteReignMic').setAttribute('aria-pressed', 'true'); h.sync();
  assert.match(mic.getAttribute('aria-label'), /Unmute/);
  end.click(); assert.equal(h.clicks.get('stopReignMic'), 1);
  h.controller.destroy();
});

test('group and simultaneous calls are controlled through existing buttons without partially muting or ending', () => {
  const h = harness(), mic = h.node('callspaceMic'), end = h.node('callspaceEnd');
  h.node('leaveCall').hidden = false; h.node('toggleMicrophone').disabled = false;
  h.node('toggleMicrophone').setAttribute('aria-pressed', 'true'); h.sync();
  assert.equal(mic.dataset.scope, 'group'); mic.click(); assert.equal(h.clicks.get('toggleMicrophone'), 1);
  h.node('stopReignMic').hidden = false; h.node('muteReignMic').hidden = false;
  h.node('liveVoiceDiagnostics').dataset.active = 'true'; h.sync();
  assert.equal(mic.dataset.scope, 'both');
  h.node('toggleMicrophone').disabled = true; h.node('leaveCall').disabled = true; h.sync();
  assert.equal(mic.disabled, true); assert.equal(end.disabled, true);
  mic.emit('click'); end.emit('click');
  assert.equal(h.clicks.get('muteReignMic'), undefined); assert.equal(h.clicks.get('stopReignMic'), undefined);
  assert.equal(h.clicks.get('leaveCall'), undefined);
  h.node('toggleMicrophone').disabled = false; h.node('leaveCall').disabled = false; h.sync();
  mic.click(); assert.equal(h.clicks.get('muteReignMic'), 1); assert.equal(h.clicks.get('toggleMicrophone'), 2);
  h.node('muteReignMic').setAttribute('aria-pressed', 'true'); h.sync();
  mic.click();
  assert.equal(h.clicks.get('muteReignMic'), 1, 'muting a partly muted call must not unmute REIGN');
  assert.equal(h.clicks.get('toggleMicrophone'), 3);
  h.node('toggleMicrophone').setAttribute('aria-pressed', 'false'); h.sync();
  assert.match(mic.getAttribute('aria-label'), /Unmute/); mic.click();
  assert.equal(h.clicks.get('muteReignMic'), 2); assert.equal(h.clicks.get('toggleMicrophone'), 4);
  end.click(); assert.equal(h.clicks.get('stopReignMic'), 1); assert.equal(h.clicks.get('leaveCall'), 1);
  h.controller.destroy();
});

test('drawers and disposal restore original nodes and listeners without touching owned media', () => {
  const h = harness({withLiveCamera: false}), media = snapshotMedia(h), trigger = h.node('callspaceMenu');
  trigger.focus(); trigger.click();
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  assert.equal(h.node('callspaceMenuPanel').open, true);
  h.node('callspaceNavDocuments').click();
  assert.equal(h.node('callspaceSectionDocuments').hidden, false);
  assert.equal(h.node('callspaceSectionDocuments').contains(h.node('roomDocuments')), true);
  h.doc.emit('keydown', {key: 'Escape'});
  assert.equal(trigger.getAttribute('aria-expanded'), 'false'); assert.equal(h.doc.activeElement, trigger);
  h.controller.openPanel('settings'); h.node('callspaceModeCall').click(); h.controller.closePanel();
  assert.equal(h.controller.getMode(), 'call');
  const face = h.node('callspaceFace'); h.controller.destroy();
  assert.equal(h.doc.getElementById('callspaceShell'), null);
  assert.equal(h.doc.getElementById('callspaceMenuPanel'), null);
  for (const [node, parent] of h.parents) assert.equal(node.parentNode, parent, 'original parent not restored: ' + (node.id || node.className));
  assert.equal(h.node('messageInput').placeholder, 'Message the room'); assert.equal(h.node('messageInput').rows, 3);
  assert.equal(h.doc.querySelector('.conversation').inert, false);
  face.emit('keydown', {key: 'Home'}); assert.equal(h.controller.getMode(), 'call', 'disposed listeners still ran');
  assertPreserved(h, media);
  const next = createCallspaceWorkspace({document: h.doc, window: h.win});
  assert.notEqual(next, h.controller); assert.equal(next.getMode(), 'chat'); next.destroy();
});

test('leaving the shared-browser drawer stops its visible-frame polling without closing the room browser', () => {
  const h = harness(), browser = h.node('browserPanel');
  h.controller.openPanel('menu'); h.node('callspaceNavBrowser').click();
  assert.equal(browser.open, true);
  h.node('callspaceNavDocuments').click();
  assert.equal(browser.open, false, 'hidden browser section retained its polling signal');
  h.node('callspaceNavBrowser').click(); assert.equal(browser.open, true);
  h.controller.closePanel(); assert.equal(browser.open, false, 'closed menu retained its polling signal');
  assert.equal(h.node('browserPanel'), browser);
  assert.equal(h.clicks.size, 0); h.controller.destroy();
});

test('a room change during full-face view cannot restore the previous room scroll or draft', () => {
  const h = harness(), feed = h.node('eventFeed');
  feed.scrollTop = 172; h.controller.setMode('call');
  const nextMessage = h.doc.createElement('article'); nextMessage.textContent = 'Another room conversation';
  feed.replaceChildren(nextMessage); feed.scrollTop = 31;
  h.node('messageInput').value = 'The other room draft';
  h.win.emit('reign:team-room', {detail: {roomId: 'room-b', generation: 2}});
  h.controller.setMode('chat');
  assert.equal(feed.scrollTop, 31); assert.equal(feed.children[0], nextMessage);
  assert.equal(h.node('messageInput').value, 'The other room draft');
  assert.equal(h.clicks.size, 0); h.controller.destroy();
});

test('access loss closes moved controls and leaves the original account logout reachable', () => {
  const h = harness(), logout = h.node('logoutButton'); let signedOut = 0;
  logout.addEventListener('click', () => signedOut++);
  h.controller.openPanel('settings');
  h.node('workspace').hidden = true; h.sync(h.node('workspace'));
  for (const panel of h.doc.querySelectorAll('[data-callspace-panel]')) assert.equal(panel.open, false, 'stale drawer stayed open after access loss');
  for (const id of ['callspaceMenu', 'callspaceSettings', 'callspaceActions']) assert.equal(h.node(id).getAttribute('aria-expanded'), 'false');
  assert.equal(h.node('logoutButton'), logout);
  for (let parent = logout; parent?.nodeType === 1; parent = parent.parentNode) {
    assert.equal(parent.hidden || parent.inert || (parent.tagName === 'DIALOG' && !parent.open), false,
      'logout is trapped in a hidden workspace or closed drawer');
  }
  logout.click(); assert.equal(signedOut, 1);
  h.controller.openPanel('settings');
  assert.equal(h.node('callspaceSettingsPanel').open, false, 'access loss allowed reopening stale room controls');
  h.controller.destroy();
});
