/* A presentation layer for the existing Workspace controllers.
 * Moving a view never starts, stops, or replaces a room or a media session. */
const instances = new WeakMap();
const clamp = value => Math.max(0, Math.min(1, Number.isFinite(Number(value)) ? Number(value) : 0));
const modeProgress = { chat: 0, split: .5, call: 1 };

export function callspaceModeForProgress(value) {
  const progress = clamp(value);
  return progress < .25 ? 'chat' : progress < .75 ? 'split' : 'call';
}

const icons = {
  settings: '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="3"/><circle cx="9" cy="17" r="3"/>',
  menu: '<path d="M4 8h16M4 16h10"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  mic: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3m-4 0h8"/>',
  micOff: '<path d="m3 3 18 18M9 9v3a3 3 0 0 0 5 2M9 5a3 3 0 0 1 6 1v4M5 11v1a7 7 0 0 0 12 5m2-5v-1M12 19v3m-4 0h8"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  chat: '<path d="M20 15a3 3 0 0 1-3 3H9l-5 3V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3z"/>',
  split: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M4 12h16"/>',
  call: '<rect x="5" y="3" width="14" height="18" rx="6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.chat}</svg>`;

export function createCallspaceWorkspace(deps = {}) {
  const doc = deps.document || globalThis.document;
  const win = deps.window || doc?.defaultView || globalThis.window;
  if (!doc || !win) return null;
  if (instances.has(doc)) return instances.get(doc);
  const $ = id => doc.getElementById(id);
  const workspace = $('workspace');
  const roomView = $('roomView');
  const roomShell = doc.querySelector('.room-shell');
  const conversation = doc.querySelector('.conversation');
  const card = $('reignCard');
  const form = $('chatForm');
  const input = $('messageInput');
  if (!workspace || !roomView || !roomShell || !conversation || !card || !form || !input) return null;

  let progress = 0;
  let mode = 'chat';
  let activePanel = null;
  let panelReturnFocus = null;
  let drag = null;
  let suppressClick = false;
  let destroyed = false;
  let resizeFrame = null;
  let scrollFrame = null;
  let savedFeedScroll = null;
  let roomRevision = 0;
  let menuSection = 'rooms';
  let composerLayout = null;
  const pendingTimers = new Set();
  const disposers = [];
  const moves = [];
  const created = [];
  const originalConversationId = conversation.id;
  const originalPlaceholder = input.getAttribute('placeholder');
  const originalRows = input.getAttribute('rows');
  const originalHeader = doc.querySelector('.app-header');
  const originalSidebar = doc.querySelector('.sidebar');
  const reducedMotion = win.matchMedia?.('(prefers-reduced-motion: reduce)');

  function element(tag, className, id, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (id) node.id = id;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function listen(node, name, handler, options) {
    if (!node) return;
    node.addEventListener(name, handler, options);
    disposers.push(() => node.removeEventListener(name, handler, options));
  }
  function later(handler) {
    const timer = win.setTimeout(() => { pendingTimers.delete(timer); if (!destroyed) handler(); }, 0);
    pendingTimers.add(timer);
  }
  function move(node, destination) {
    if (!node || !destination) return;
    const marker = doc.createComment('Workspace layout position');
    node.parentNode?.insertBefore(marker, node);
    moves.push({ node, marker });
    destination.append(node);
  }
  function button(id, label, iconName, className = '') {
    const node = element('button', `callspace-icon ${className}`.trim(), id);
    node.type = 'button';
    node.setAttribute('aria-label', label);
    node.title = label;
    node.innerHTML = icon(iconName);
    return node;
  }
  function textButton(id, label, handler) {
    const node = element('button', 'callspace-text-button', id, label);
    node.type = 'button';
    if (handler) listen(node, 'click', handler);
    return node;
  }

  const shell = element('div', 'callspace-shell', 'callspaceShell');
  shell.dataset.mode = 'chat';
  roomShell.append(shell);
  created.push(shell);
  const topbar = element('nav', 'callspace-topbar');
  topbar.setAttribute('aria-label', 'Workspace controls');
  const settingsButton = button('callspaceSettings', 'Call settings and view', 'settings');
  const menuButton = button('callspaceMenu', 'Rooms and workspace', 'menu');
  topbar.append(settingsButton, menuButton);
  shell.append(topbar);
  move($('roomEmpty'), shell);
  move(roomView, shell);

  const face = element('div', 'callspace-face', 'callspaceFace');
  face.tabIndex = 0;
  face.setAttribute('role', 'slider');
  face.setAttribute('aria-label', 'REIGN view size');
  face.setAttribute('aria-valuemin', '0');
  face.setAttribute('aria-valuemax', '2');
  face.setAttribute('aria-orientation', 'vertical');
  face.setAttribute('aria-controls', 'callspaceTranscript');
  face.setAttribute('aria-describedby', 'callspaceGestureHelp');
  const gestureHelp = element('span', 'visually-hidden', 'callspaceGestureHelp', 'Drag down to enlarge REIGN and up to return to chat. Use arrow keys, Home or End, or choose a view in settings.');
  roomView.append(face, gestureHelp);
  created.push(face, gestureHelp);
  const copy = card.querySelector('.reign-copy');
  move(card, face);
  const handle = element('span', 'callspace-drag-handle');
  handle.setAttribute('aria-hidden', 'true');
  face.append(handle);

  conversation.id = 'callspaceTranscript';
  move(conversation, roomView);
  const roomHeader = doc.querySelector('.room-header');
  if (roomHeader) {
    move(roomHeader, conversation);
    conversation.insertBefore(roomHeader, conversation.firstChild);
  }
  const activity = element('p', 'callspace-activity', 'callspaceActivity');
  activity.setAttribute('aria-live', 'polite');
  roomView.append(activity);
  created.push(activity);

  const dock = element('div', 'callspace-dock', 'callspaceDock');
  const feedback = element('div', 'callspace-feedback');
  const dockRow = element('div', 'callspace-dock-row');
  const endButton = button('callspaceEnd', 'End call', 'close', 'callspace-end');
  const micButton = button('callspaceMic', 'Turn on microphone to talk with REIGN', 'micOff', 'callspace-mic');
  const composer = element('div', 'callspace-composer');
  const actionsButton = button('callspaceActions', 'Add a view or open tools', 'plus', 'callspace-add');
  move(form, composer);
  form.append(actionsButton);
  input.placeholder = 'Follow up';
  input.rows = 1;
  move($('messageStatus'), feedback);
  move($('retryMessage'), feedback);
  dockRow.append(endButton, micButton, composer);
  dock.append(feedback, dockRow);
  roomView.append(dock);
  created.push(dock);

  const panels = {};
  const triggers = { menu: menuButton, settings: settingsButton, actions: actionsButton };
  const panelNames = { menu: 'Workspace', settings: 'Call settings', actions: 'Add to the conversation' };
  for (const name of ['menu', 'settings', 'actions']) {
    const dialog = element('dialog', `callspace-panel callspace-panel-${name}`, `callspace${name[0].toUpperCase()}${name.slice(1)}Panel`);
    dialog.dataset.callspacePanel = name;
    dialog.setAttribute('aria-labelledby', `callspace${name}Heading`);
    const heading = element('div', 'callspace-panel-heading');
    const title = element('h2', '', `callspace${name}Heading`, panelNames[name]);
    const close = button(`callspaceClose${name[0].toUpperCase()}${name.slice(1)}`, `Close ${panelNames[name].toLowerCase()}`, 'close');
    const body = element('div', 'callspace-panel-body');
    heading.append(title, close);
    dialog.append(heading, body);
    doc.body.append(dialog);
    created.push(dialog);
    panels[name] = { dialog, body, close };
    triggers[name].setAttribute('aria-controls', dialog.id);
    triggers[name].setAttribute('aria-haspopup', 'dialog');
    triggers[name].setAttribute('aria-expanded', 'false');
    listen(triggers[name], 'click', () => activePanel === name ? closePanel() : openPanel(name));
    listen(close, 'click', () => closePanel());
    listen(dialog, 'cancel', event => { event.preventDefault(); closePanel(); });
    listen(dialog, 'click', event => { if (event.target === dialog) closePanel(); });
    listen(dialog, 'close', () => { if (activePanel === name) finishPanelClose(); });
  }

  const menuNav = element('nav', 'callspace-menu-nav');
  menuNav.setAttribute('aria-label', 'Workspace sections');
  const menuSections = {};
  for (const [name, label] of [['rooms', 'Rooms & people'], ['documents', 'Documents'], ['browser', 'Shared browser']]) {
    const section = element('section', 'callspace-menu-section', `callspaceSection${name[0].toUpperCase()}${name.slice(1)}`);
    section.dataset.section = name;
    section.setAttribute('aria-label', label);
    menuSections[name] = section;
    menuNav.append(textButton(`callspaceNav${name[0].toUpperCase()}${name.slice(1)}`, label, () => showMenuSection(name)));
    panels.menu.body.append(section);
  }
  panels.menu.dialog.insertBefore(menuNav, panels.menu.body);
  move(originalSidebar, menuSections.rooms);
  move(doc.querySelector('.documents'), menuSections.documents);
  move($('browserPanel'), menuSections.browser);
  move(originalHeader, panels.menu.body);

  const viewSettings = element('section', 'callspace-view-settings');
  viewSettings.append(element('h3', '', '', 'Your view'));
  const modeControls = element('div', 'callspace-mode-controls');
  const modeButtons = {};
  for (const [name, label] of [['chat', 'Chat'], ['split', 'Together'], ['call', 'Face']]) {
    const node = textButton(`callspaceMode${name[0].toUpperCase()}${name.slice(1)}`, label, () => setMode(name));
    node.innerHTML = `${icon(name)}<span>${label}</span>`;
    node.setAttribute('aria-pressed', String(name === 'chat'));
    modeButtons[name] = node;
    modeControls.append(node);
  }
  viewSettings.append(modeControls, element('p', 'callspace-panel-note', '', 'Drag REIGN down to expand the face. Drag up to bring the conversation back. Your call keeps going.'));
  panels.settings.body.append(viewSettings);
  move(doc.querySelector('.live-voice-panel'), panels.settings.body);
  move(copy, panels.settings.body);
  const speechChoice = $('speakReply')?.closest('label');
  move(speechChoice, panels.settings.body);
  move(doc.querySelector('.call-panel'), panels.settings.body);

  // The optional live camera retains the same video and MediaStream when views change.
  move($('liveCameraView'), roomView);
  move(doc.querySelector('.dictation-tools'), panels.actions.body);
  move(doc.querySelector('.share-actions'), panels.actions.body);
  const actionNav = element('div', 'callspace-action-links');
  actionNav.append(
    textButton('callspaceOpenDocuments', 'Room documents', () => { openPanel('menu'); showMenuSection('documents'); }),
    textButton('callspaceOpenBrowser', 'Shared browser', () => { openPanel('menu'); showMenuSection('browser'); })
  );
  panels.actions.body.append(actionNav);
  move(conversation.querySelector('.composer-note'), panels.actions.body);

  function showMenuSection(name) {
    if (!menuSections[name]) return;
    menuSection = name;
    for (const [key, section] of Object.entries(menuSections)) {
      section.hidden = key !== name;
      $(`callspaceNav${key[0].toUpperCase()}${key.slice(1)}`)?.setAttribute('aria-current', key === name ? 'page' : 'false');
    }
    if ($('browserPanel')) $('browserPanel').open = name === 'browser' && activePanel === 'menu';
    panels.menu.body.scrollTop = 0;
  }

  function finishPanelClose(restoreFocus = true) {
    if ($('browserPanel')) $('browserPanel').open = false;
    if (activePanel) triggers[activePanel].setAttribute('aria-expanded', 'false');
    activePanel = null;
    shell.removeAttribute('data-panel');
    if (restoreFocus && panelReturnFocus?.isConnected) panelReturnFocus.focus({ preventScroll: true });
    panelReturnFocus = null;
  }
  function closePanel(restoreFocus = true) {
    if (!activePanel) return;
    const panel = panels[activePanel].dialog;
    finishPanelClose(restoreFocus);
    if (typeof panel.close === 'function' && panel.open) panel.close();
    else panel.removeAttribute('open');
  }
  function openPanel(name) {
    if (!panels[name] || destroyed || workspace.hidden) return false;
    if (activePanel === name) return true;
    const returnTo = activePanel ? panelReturnFocus : doc.activeElement;
    closePanel(false);
    activePanel = name;
    panelReturnFocus = returnTo || triggers[name];
    shell.dataset.panel = name;
    triggers[name].setAttribute('aria-expanded', 'true');
    const panel = panels[name].dialog;
    if (typeof panel.showModal === 'function') panel.showModal();
    else panel.setAttribute('open', '');
    if (name === 'menu' && menuSection === 'browser' && $('browserPanel')) $('browserPanel').open = true;
    panels[name].close.focus({ preventScroll: true });
    return true;
  }

  function dimensions() {
    const rect = shell.getBoundingClientRect();
    return { width: rect.width || win.innerWidth || 390, height: rect.height || win.innerHeight || 844 };
  }
  function renderGeometry() {
    if (destroyed) return;
    const visualHeight = win.visualViewport?.height;
    if (Number.isFinite(visualHeight) && (!win.visualViewport.scale || Math.abs(win.visualViewport.scale - 1) < .01)) shell.style.setProperty('--callspace-viewport-height', `${visualHeight}px`);
    const { width, height } = dimensions();
    const padding = width < 600 ? 16 : 28;
    const compactW = Math.min(150, Math.max(96, width * 188 / 709));
    const compactH = compactW * 162 / 188;
    const compactX = width < 600 ? width * 384 / 709 : width - compactW - 112;
    const compact = { x: Math.min(width - compactW - 70, compactX), y: 22, w: compactW, h: compactH, r: 23 };
    const togetherW = Math.min(width - padding * 2, 720);
    const together = { x: (width - togetherW) / 2, y: 86, w: togetherW, h: Math.max(150, Math.min(height * .4, 420)), r: 30 };
    const full = { x: 0, y: 0, w: width, h: height, r: 0 };
    const from = progress <= .5 ? compact : together;
    const to = progress <= .5 ? together : full;
    const mix = progress <= .5 ? progress * 2 : (progress - .5) * 2;
    const frame = {};
    for (const key of ['x', 'y', 'w', 'h', 'r']) frame[key] = from[key] + (to[key] - from[key]) * mix;
    for (const [key, value] of Object.entries(frame)) shell.style.setProperty(`--callspace-face-${key}`, `${value.toFixed(2)}px`);
    shell.style.setProperty('--callspace-progress', String(progress));
    let transcriptTop = Math.max(174, frame.y + frame.h + 24);
    const participants = $('callParticipants');
    if (mode === 'chat' && visible($('liveCameraView')) && participants?.children.length) {
      const participantBottom = participants.getBoundingClientRect().bottom - shell.getBoundingClientRect().top;
      // Camera and participant tiles share the small-view header. Reserve their
      // actual height before placing either the activity line or conversation.
      transcriptTop = Math.max(transcriptTop, participantBottom + 46);
    }
    shell.style.setProperty('--callspace-transcript-top', `${transcriptTop.toFixed(2)}px`);
    shell.style.setProperty('--callspace-transcript-opacity', String(progress <= .5 ? 1 : Math.max(0, 1 - (progress - .5) * 4)));
  }

  function setProgress(value) {
    if (destroyed) return progress;
    const nextProgress = clamp(value);
    const nextMode = callspaceModeForProgress(nextProgress);
    const feed = $('eventFeed');
    const retainedScroll = mode === 'call' && savedFeedScroll !== null ? savedFeedScroll : feed?.scrollTop;
    if (nextMode === 'call' && mode !== 'call') {
      savedFeedScroll = feed?.scrollTop ?? null;
      if (conversation.contains(doc.activeElement)) face.focus({ preventScroll: true });
    }
    const previousMode = mode;
    progress = nextProgress;
    mode = nextMode;
    shell.dataset.mode = mode;
    conversation.inert = mode === 'call';
    conversation.setAttribute('aria-hidden', String(mode === 'call'));
    if (mode === 'call') conversation.setAttribute('inert', '');
    else conversation.removeAttribute('inert');
    face.setAttribute('aria-valuenow', String(mode === 'chat' ? 0 : mode === 'split' ? 1 : 2));
    face.setAttribute('aria-valuetext', mode === 'chat' ? 'Small face with conversation' : mode === 'split' ? 'Face and conversation together' : 'Full face. Conversation is hidden');
    for (const [name, node] of Object.entries(modeButtons)) node.setAttribute('aria-pressed', String(name === mode));
    if (previousMode === 'call' && mode !== 'call') {
      savedFeedScroll = null;
    }
    renderGeometry();
    if (mode !== 'call' && feed && Number.isFinite(retainedScroll)) {
      feed.scrollTop = retainedScroll;
      if (scrollFrame !== null) win.cancelAnimationFrame(scrollFrame);
      const revision = roomRevision;
      scrollFrame = win.requestAnimationFrame(() => {
        scrollFrame = null;
        if (!destroyed && revision === roomRevision && mode !== 'call') feed.scrollTop = retainedScroll;
      });
    }
    syncControls();
    return progress;
  }
  function setMode(value) {
    if (!Object.hasOwn(modeProgress, value)) return mode;
    setProgress(modeProgress[value]);
    return mode;
  }

  function visible(node) { return !!node && !node.hidden && node.getAttribute('aria-hidden') !== 'true'; }
  function available(node) { return visible(node) && !node.disabled; }
  function controlState() {
    const diagnostics = $('liveVoiceDiagnostics');
    const voiceActive = diagnostics?.dataset.active === 'true' || visible($('stopReignMic'));
    const groupActive = visible($('leaveCall'));
    const transports = [];
    if (voiceActive) transports.push({
      scope: 'voice', mic: $('muteReignMic'), end: $('stopReignMic'),
      muted: diagnostics?.dataset.muted === 'true' || $('muteReignMic')?.getAttribute('aria-pressed') === 'true',
    });
    if (groupActive) transports.push({
      scope: 'group', mic: $('toggleMicrophone'), end: $('leaveCall'),
      muted: $('toggleMicrophone')?.getAttribute('aria-pressed') !== 'true',
    });
    if (!transports.length) return { scope: 'voice', active: false, micTargets: [$('talkToReign')], endTargets: [], muted: true };
    const muted = transports.every(transport => transport.muted);
    return {
      scope: transports.length > 1 ? 'both' : transports[0].scope,
      active: true,
      muted,
      // A mixed state first mutes every microphone still on. Only an all-muted
      // state unmutes, so one transport can never silently keep transmitting.
      micTargets: transports.filter(transport => muted || !transport.muted).map(transport => transport.mic),
      endTargets: transports.map(transport => transport.end),
    };
  }
  function syncControls() {
    if (destroyed) return;
    const state = controlState();
    const micLabel = state.active ? `${state.muted ? 'Unmute' : 'Mute'} ${state.scope === 'both' ? 'REIGN and group call microphones' : `${state.scope === 'voice' ? 'REIGN' : 'group call'} microphone`}` : 'Turn on microphone to talk with REIGN';
    micButton.disabled = !state.micTargets.length || state.micTargets.some(target => !available(target));
    micButton.setAttribute('aria-label', micLabel);
    micButton.setAttribute('aria-pressed', String(state.active && !state.muted));
    micButton.title = micLabel;
    micButton.dataset.scope = state.scope;
    micButton.dataset.muted = String(state.muted);
    const nextIcon = state.muted ? 'micOff' : 'mic';
    if (micButton.dataset.icon !== nextIcon) { micButton.innerHTML = icon(nextIcon); micButton.dataset.icon = nextIcon; }
    endButton.disabled = state.active ? !state.endTargets.length || state.endTargets.some(target => !available(target)) : mode === 'chat';
    const endLabel = state.active ? state.scope === 'both' ? 'End REIGN and group calls' : state.scope === 'voice' ? 'End REIGN call' : 'Leave group call' : 'Return to conversation';
    endButton.setAttribute('aria-label', endLabel);
    endButton.title = endLabel;
    endButton.dataset.scope = state.active ? state.scope : 'view';
    const status = state.active ? (state.scope === 'voice' ? $('liveVoiceStatus') : $('callStatus')) : $('assistantStatus');
    const statusText = state.scope === 'both' ? `REIGN and group call · ${state.muted ? 'Microphones muted' : 'Microphone on'}` : status?.textContent?.trim() || 'REIGN';
    if (activity.textContent !== statusText) activity.textContent = statusText;
    activity.dataset.active = String(state.active);
    shell.dataset.callActive = String(state.active);
    actionsButton.disabled = !!input.disabled;
  }
  listen(micButton, 'click', () => {
    const targets = controlState().micTargets;
    if (targets.length && targets.every(available)) for (const target of targets) target.click();
    syncControls();
  });
  listen(endButton, 'click', () => {
    const state = controlState();
    if (state.active) { if (state.endTargets.length && state.endTargets.every(available)) for (const target of state.endTargets) target.click(); }
    else setMode('chat');
    syncControls();
  });

  function updateComposer() {
    composer.dataset.hasDraft = String(!!input.value.trim() || ($('attachmentList') && !$('attachmentList').hidden));
    input.style.height = 'auto';
    const height = Math.min(128, Math.max(49, input.scrollHeight || 49));
    input.style.height = `${height}px`;
    shell.style.setProperty('--callspace-composer-height', `${height}px`);
  }
  function updateComposerForLayout() {
    if (destroyed) return;
    const { width, height } = dimensions();
    const layout = [width, height, composer.getBoundingClientRect().width,
      win.visualViewport?.width || 0, win.visualViewport?.height || 0].join(':');
    if (layout === composerLayout) return;
    // Track container / viewport dimensions, never the textarea's own height.
    // A wrapped draft can grow after a resize without feeding a resize loop.
    composerLayout = layout;
    updateComposer();
  }
  listen(input, 'input', updateComposer);
  listen(input, 'focus', () => { if (mode === 'call') setMode('chat'); updateComposer(); });
  // Enter retains the textarea's ordinary newline behavior. The original Send
  // and Ask REIGN submitters remain separate, explicit actions.
  listen(form, 'submit', () => later(updateComposer));
  listen(originalSidebar, 'click', event => {
    if (event.target.closest?.('.room-button') && !event.target.closest('.room-button').disabled) closePanel();
  });
  for (const id of ['manageMembersButton', 'openCamera', 'shareWithReign']) {
    listen($(id), 'click', () => closePanel(false), true);
  }

  function releaseDrag(restore) {
    if (!drag) return;
    const old = drag;
    drag = null;
    shell.removeAttribute('data-dragging');
    try { if (face.hasPointerCapture?.(old.pointerId)) face.releasePointerCapture(old.pointerId); } catch { /* The browser may already have cancelled capture. */ }
    if (restore) setProgress(old.startProgress);
    else if (old.moved) setMode(callspaceModeForProgress(progress));
    suppressClick = old.moved && !restore;
    if (suppressClick) later(() => { suppressClick = false; });
  }
  listen(face, 'pointerdown', event => {
    if (event.isPrimary === false || (event.button !== undefined && event.button !== 0) || event.target.closest?.('button,a,input,textarea,select')) return;
    if (drag) return;
    drag = { pointerId: event.pointerId, startY: event.clientY, startProgress: progress, moved: false };
    try { face.setPointerCapture?.(event.pointerId); } catch { /* Keyboard or synthetic events need no pointer capture. */ }
  });
  listen(face, 'pointermove', event => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const delta = event.clientY - drag.startY;
    if (Math.abs(delta) > 5) drag.moved = true;
    if (!drag.moved) return;
    event.preventDefault();
    shell.dataset.dragging = 'true';
    setProgress(drag.startProgress + delta / Math.max(260, dimensions().height * .48));
  });
  listen(face, 'pointerup', event => { if (drag && event.pointerId === drag.pointerId) releaseDrag(false); });
  listen(face, 'pointercancel', event => { if (drag && event.pointerId === drag.pointerId) releaseDrag(true); });
  listen(face, 'lostpointercapture', () => { if (drag) releaseDrag(true); });
  listen(face, 'click', event => {
    if (suppressClick) { suppressClick = false; event.preventDefault(); return; }
    if (event.target.closest?.('button,a,input,textarea,select')) return;
    setMode(mode === 'chat' ? 'split' : mode === 'split' ? 'call' : 'chat');
  });
  listen(face, 'keydown', event => {
    let next;
    if (event.key === 'Home') next = 'chat';
    else if (event.key === 'End') next = 'call';
    else if (['ArrowDown', 'ArrowRight'].includes(event.key)) next = mode === 'chat' ? 'split' : 'call';
    else if (['ArrowUp', 'ArrowLeft'].includes(event.key)) next = mode === 'call' ? 'split' : 'chat';
    else if (event.key === 'Enter' || event.key === ' ') next = mode === 'chat' ? 'split' : mode === 'split' ? 'call' : 'chat';
    if (next) { event.preventDefault(); setMode(next); }
  });
  listen(doc, 'keydown', event => {
    if (event.key !== 'Escape' || !activePanel) return;
    // A nested account picker is the topmost modal: let it handle Escape.
    // Closing the workspace menu here would also hide its focus return target.
    if (event.target && !panels[activePanel].dialog.contains(event.target)) return;
    event.preventDefault();
    closePanel();
  });
  listen(win, 'reign:team-room', () => {
    roomRevision++;
    savedFeedScroll = null;
    if (scrollFrame !== null) { win.cancelAnimationFrame(scrollFrame); scrollFrame = null; }
    // The room controller has restored this room's own draft and scroll.
    updateComposer();
  });

  function syncWorkspaceAccess() {
    if (workspace.hidden) {
      closePanel(false);
      if (originalHeader && originalHeader.parentNode !== doc.body) doc.body.insertBefore(originalHeader, workspace);
      doc.body.dataset.callspaceUnavailable = 'true';
    } else {
      if (originalHeader && originalHeader.parentNode === doc.body) panels.menu.body.append(originalHeader);
      delete doc.body.dataset.callspaceUnavailable;
    }
  }

  function onResize() {
    if (drag) releaseDrag(true);
    if (resizeFrame !== null) return;
    resizeFrame = win.requestAnimationFrame(() => { resizeFrame = null; renderGeometry(); updateComposerForLayout(); });
  }
  listen(win, 'resize', onResize);
  listen(win, 'orientationchange', onResize);
  listen(win.visualViewport, 'resize', onResize);
  const Resize = deps.ResizeObserver || win.ResizeObserver;
  const resizeObserver = Resize ? new Resize(() => { renderGeometry(); updateComposerForLayout(); }) : null;
  resizeObserver?.observe(shell);
  if ($('callParticipants')) resizeObserver?.observe($('callParticipants'));
  const Mutation = deps.MutationObserver || win.MutationObserver;
  const observer = Mutation ? new Mutation(() => { syncWorkspaceAccess(); syncControls(); updateComposer(); renderGeometry(); }) : null;
  observer?.observe(workspace, { attributes: true, attributeFilter: ['hidden'] });
  for (const id of ['talkToReign', 'muteReignMic', 'stopReignMic', 'joinCall', 'leaveCall', 'toggleMicrophone', 'liveVoiceDiagnostics', 'liveVoiceStatus', 'callStatus', 'assistantStatus', 'messageInput', 'attachmentList', 'toggleLiveCamera', 'speakerphone', 'callParticipants', 'liveCameraView']) {
    const node = $(id);
    if (node) observer?.observe(node, { attributes: true, attributeFilter: ['disabled', 'hidden', 'aria-pressed', 'aria-hidden', 'data-active', 'data-muted', 'data-phase'], childList: true, subtree: true, characterData: true });
  }

  function destroy() {
    if (destroyed) return;
    closePanel(false);
    if (drag) releaseDrag(true);
    destroyed = true;
    observer?.disconnect();
    resizeObserver?.disconnect();
    if (resizeFrame !== null) win.cancelAnimationFrame(resizeFrame);
    if (scrollFrame !== null) win.cancelAnimationFrame(scrollFrame);
    for (const timer of pendingTimers) win.clearTimeout(timer);
    pendingTimers.clear();
    for (const dispose of disposers) dispose();
    conversation.inert = false;
    conversation.removeAttribute('aria-hidden');
    conversation.removeAttribute('inert');
    conversation.id = originalConversationId;
    if (originalPlaceholder !== null) input.setAttribute('placeholder', originalPlaceholder);
    else input.removeAttribute('placeholder');
    if (originalRows !== null) input.setAttribute('rows', originalRows);
    input.style.removeProperty('height');
    actionsButton.remove();
    for (const { node, marker } of moves.reverse()) if (marker.parentNode) marker.replaceWith(node);
    for (const node of created.reverse()) node.remove();
    doc.body.classList.remove('callspace-ready');
    delete doc.body.dataset.callspaceUnavailable;
    instances.delete(doc);
  }

  const controller = { setMode, getMode: () => mode, setProgress, openPanel, closePanel, destroy };
  instances.set(doc, controller);
  doc.body.classList.add('callspace-ready');
  shell.dataset.reducedMotion = String(!!reducedMotion?.matches);
  showMenuSection('rooms');
  setMode('chat');
  syncWorkspaceAccess();
  updateComposerForLayout();
  return controller;
}
