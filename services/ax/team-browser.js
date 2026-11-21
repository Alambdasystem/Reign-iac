// One room-owned browser view. Opening the panel discovers an existing binding;
// only the explicit Open action may allocate a new browser session.
export function createTeamBrowser(deps = {}) {
  const doc = deps.document || document, win = deps.window || window, api = deps.api;
  const timers = deps.timers || globalThis, urls = deps.URL || URL;
  const $ = id => doc.getElementById(id);
  let room = null, generation = 0, current = null, timer, abort, busy = false, closed = false, frameUrl = null, frameReady = false;
  const listeners = [];
  const live = (revision, id) => !closed && generation === revision && room?.roomId === id;
  const path = () => '/api/team/rooms/' + encodeURIComponent(room.roomId) + '/browser';
  const active = () => current?.status === 'active' && !!current.session;
  const ownsControl = () => active() && current.session.mode === 'human' && current.control?.connection_id === room?.connectionId && current.control?.user_id === room?.me?.id;
  function notice(text, error = false) { $('browserStatus').textContent = text; $('browserStatus').dataset.error = String(error); }
  function clearFrame() {
    frameReady = false;
    $('browserFrame').removeAttribute('src'); $('browserFrame').hidden = true;
    if (frameUrl) urls.revokeObjectURL(frameUrl); frameUrl = null;
  }
  function controls() {
    const available = !!room?.enabled && !closed && !busy;
    $('openBrowser').hidden = active(); $('navigateBrowser').hidden = !active();
    $('openBrowser').disabled = !available || ['opening', 'uncertain'].includes(current?.status);
    $('refreshBrowser').disabled = !room?.enabled || closed || busy;
    $('browserUrl').disabled = !available || (active() && current.session.mode !== 'agent');
    $('navigateBrowser').disabled = !available || !active() || current.session.mode !== 'agent';
    $('closeBrowser').disabled = !available || !active() || (current.session.mode === 'human' && !ownsControl());
    $('takeBrowser').hidden = ownsControl(); $('returnBrowser').hidden = !ownsControl();
    $('takeBrowser').disabled = !available || !active() || current.session.mode !== 'agent' || !frameReady;
    $('returnBrowser').disabled = !available || !ownsControl();
    for (const id of ['browserText', 'sendBrowserText', 'browserEnter', 'browserTab', 'browserScrollUp', 'browserScrollDown']) $(id).disabled = !available || !ownsControl() || !frameReady;
    $('browserFrame').classList.toggle('controllable', available && ownsControl() && frameReady);
  }
  function apply(value) {
    const old = current?.session;
    current = value;
    if (!active() || old?.id !== value.session.id || old?.epoch !== value.session.epoch) clearFrame();
    $('browserTitle').textContent = active() ? value.session.title || 'Shared browser' : 'Shared browser';
    $('browserLocation').textContent = active() ? value.session.url || '' : '';
    if (Array.isArray(value.allowed_hosts)) $('browserHosts').textContent = 'Allowed sites: ' + value.allowed_hosts.join(', ') + ' · One browser can run at a time.';
    if (!room?.enabled) notice('Shared browser access isn’t available in this workspace.');
    else if (ownsControl()) notice('You have control. Click the view or use the controls below.');
    else if (active() && value.session.mode === 'human') notice((value.control?.display_name || 'A teammate') + ' has control. Ask them to return it when they’re done.');
    else if (active()) notice('This browser is shared with the room. Take control to interact, or ask REIGN for help in the conversation.');
    else if (value.status === 'opening') notice('The room browser is opening…');
    else if (value.status === 'uncertain') notice('The browser state couldn’t be confirmed. Check again before opening another session.', true);
    else if (value.status === 'expired') notice('The earlier browser has expired. Your saved room is intact; open a new browser when you need one.');
    else notice('No live browser in this room. Enter an allowed site and choose Open browser.');
    controls();
  }
  function stopPolling() { timers.clearTimeout(timer); timer = undefined; abort?.abort(); abort = null; }
  async function poll() {
    if (!room?.enabled || closed || busy || !$('browserPanel').open || doc.hidden) return;
    const revision = generation, id = room.roomId, controller = new AbortController(); abort = controller;
    try {
      const value = await api(path(), 'GET', undefined, {signal: controller.signal, timeout: 10000});
      if (!live(revision, id) || controller.signal.aborted || !$('browserPanel').open) return;
      apply(value);
      if (active() && !busy) {
        const sessionId = current.session.id, epoch = current.session.epoch;
        const blob = await api(path() + '/frame', 'GET', undefined, {responseType: 'blob', signal: controller.signal, timeout: 10000});
        if (!live(revision, id) || controller.signal.aborted || !$('browserPanel').open || current?.session?.id !== sessionId || current?.session?.epoch !== epoch) return;
        const previous = frameUrl;
        frameUrl = urls.createObjectURL(blob); $('browserFrame').src = frameUrl; $('browserFrame').hidden = false;
        frameReady = true; if (previous) urls.revokeObjectURL(previous); controls();
      }
    } catch (error) {
      if (live(revision, id) && error.name !== 'AbortError') { frameReady = false; notice(error.message || 'The shared browser could not refresh. Try again.', true); controls(); }
    } finally {
      if (abort === controller) abort = null;
      if (live(revision, id) && !controller.signal.aborted && $('browserPanel').open && !doc.hidden) timer = timers.setTimeout(poll, active() ? 1200 : 5000);
    }
  }
  async function action(suffix, method, body) {
    if (!room?.enabled || closed || busy) return;
    const revision = generation, id = room.roomId;
    busy = true; stopPolling(); controls(); notice('Updating the shared browser…');
    try {
      const value = await api(path() + suffix, method, body, {timeout: method === 'POST' && ['', '/navigate'].includes(suffix) ? 70000 : 15000});
      if (live(revision, id)) apply(value);
    } catch (error) { if (live(revision, id)) notice(error.message || 'The browser action could not be confirmed. Refresh its status before trying again.', true); }
    finally { if (live(revision, id)) { busy = false; controls(); timer = timers.setTimeout(poll, 400); } }
  }
  function userInput(kind, payload = {}) {
    if (!ownsControl() || !frameReady || busy) return;
    void action('/input', 'POST', {connection_id: room.connectionId, epoch: current.session.epoch, kind, ...payload});
  }
  function listen(target, type, handler) { target.addEventListener(type, handler); listeners.push(() => target.removeEventListener(type, handler)); }
  listen($('browserPanel'), 'toggle', () => { stopPolling(); if ($('browserPanel').open) void poll(); else clearFrame(); });
  listen($('browserForm'), 'submit', event => {
    event.preventDefault(); const url = $('browserUrl').value.trim(); if (!url) return;
    if (active()) { if (current.session.mode === 'agent') void action('/navigate', 'POST', {url, epoch: current.session.epoch}); }
    else if (!['opening', 'uncertain'].includes(current?.status)) void action('', 'POST', {url});
  });
  listen($('refreshBrowser'), 'click', () => { stopPolling(); void poll(); });
  listen($('closeBrowser'), 'click', () => {
    if (active() && win.confirm('Close the shared browser for everyone in this room? Saved messages and documents will remain.')) void action('', 'DELETE');
  });
  listen($('takeBrowser'), 'click', () => { if (active() && current.session.mode === 'agent') void action('/control', 'POST', {connection_id: room.connectionId, epoch: current.session.epoch, mode: 'human'}); });
  listen($('returnBrowser'), 'click', () => { if (ownsControl()) void action('/control', 'POST', {connection_id: room.connectionId, epoch: current.session.epoch, mode: 'agent'}); });
  listen($('browserInputForm'), 'submit', event => { event.preventDefault(); const text = $('browserText').value; if (text) { userInput('text', {text}); $('browserText').value = ''; } });
  listen($('browserEnter'), 'click', () => userInput('key', {key: 'Enter'}));
  listen($('browserTab'), 'click', () => userInput('key', {key: 'Tab'}));
  listen($('browserScrollUp'), 'click', () => userInput('scroll', {dy: -500}));
  listen($('browserScrollDown'), 'click', () => userInput('scroll', {dy: 500}));
  listen($('browserFrame'), 'click', event => {
    if (!frameReady || !ownsControl() || busy) return;
    const bounds = $('browserFrame').getBoundingClientRect(), viewport = current.session.viewport;
    if (!bounds.width || !bounds.height || !viewport?.width || !viewport?.height) return;
    const x = Math.max(0, Math.min(viewport.width - 1, Math.floor((event.clientX - bounds.left) * viewport.width / bounds.width)));
    const y = Math.max(0, Math.min(viewport.height - 1, Math.floor((event.clientY - bounds.top) * viewport.height / bounds.height)));
    userInput('click', {x, y});
  });
  listen(doc, 'visibilitychange', () => { stopPolling(); if (!doc.hidden) void poll(); else clearFrame(); });
  function setRoom(value) {
    ++generation; stopPolling(); clearFrame(); room = value; current = null; busy = false;
    $('browserText').value = ''; $('browserUrl').value = ''; $('browserLocation').textContent = ''; $('browserHosts').textContent = '';
    apply({status: 'none', session: null, control: null});
    if (room?.enabled && $('browserPanel').open) void poll();
  }
  function dispose() { if (closed) return; closed = true; setRoom(null); for (const remove of listeners.splice(0)) remove(); }
  controls();
  return {setRoom, dispose};
}
