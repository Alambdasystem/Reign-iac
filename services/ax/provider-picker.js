const opener = typeof document !== 'undefined' && document.querySelector('[data-provider-picker]');

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function providerCard(provider) {
  const card = element('li', '', 'provider-picker-card');
  const icon = element('span', provider.name.slice(0, 1), 'provider-picker-icon');
  icon.setAttribute('aria-hidden', 'true');
  icon.dataset.provider = provider.id;
  const details = element('div');
  details.append(element('h3', provider.name), element('p', provider.description));
  card.append(icon, details, element('span', 'Unavailable', 'provider-picker-state'));
  return card;
}

if (opener) {
  const skillsOpener = document.querySelector('[data-skill-picker]');
  const dialog = element('dialog', '', 'provider-picker');
  const title = element('h2', 'Account connections');
  title.id = 'provider-picker-title';
  dialog.setAttribute('aria-labelledby', title.id);
  const close = button('Close');
  close.type = 'submit';
  close.autofocus = true;
  const form = element('form');
  form.method = 'dialog';
  form.append(close);
  const header = element('div', '', 'provider-picker-header');
  header.append(title, form);
  const navigation = element('nav', '', 'provider-picker-nav');
  navigation.setAttribute('aria-label', 'Tool settings');
  const connectionsTab = button('Connections');
  const skillsTab = button('Skills');
  navigation.append(connectionsTab, skillsTab);
  const status = element('p', '', 'provider-picker-message');
  status.setAttribute('role', 'status');
  const content = element('div', '', 'provider-picker-content');
  const retry = button('Try again');
  retry.hidden = true;
  dialog.append(header, navigation, status, content, retry);
  document.body.append(dialog);

  const workspace = opener.closest('#console') || document.getElementById('workspace');
  let pending, view, returnFocus, savedSkills, busy = false;
  let expanded = new Set();
  const skillNames = {gmail: 'Gmail', outlook: 'Outlook', aws: 'AWS IAM Identity Center'};
  const teamPath = '/api/team/skills';

  function button(label, handler) {
    const node = element('button', label, 'provider-picker-button');
    node.type = 'button';
    if (handler) node.addEventListener('click', handler);
    return node;
  }

  function list() {
    const node = element('ul', '', 'provider-picker-list');
    node.setAttribute('role', 'list');
    return node;
  }

  function showProviders(catalog) {
    status.textContent = 'Account linking is currently unavailable.';
    const providers = list();
    providers.append(...catalog.providers.map(providerCard));
    content.replaceChildren(providers);
  }

  function showSkills(state, focusSkill = null) {
    savedSkills = state;
    const cards = list();
    const ordered = [...state.skills].sort((a, b) =>
      Number(state.pinned.includes(b.id)) - Number(state.pinned.includes(a.id)));
    let focus;
    for (const skill of ordered) {
      const card = element('li', '', 'provider-picker-card skill-card');
      const details = element('details');
      details.open = expanded.has(skill.id);
      details.addEventListener('toggle', () => {
        if (details.open) expanded.add(skill.id);
        else expanded.delete(skill.id);
      });
      const label = element('summary', skill.title);
      const statusLabel = element('span', skill.availability === 'ready' ? 'Ready' : 'Needs setup', 'provider-picker-state');
      label.append(statusLabel);
      const steps = element('ol');
      steps.append(...skill.steps.map(step => element('li', step)));
      details.append(label, element('p', skill.description), steps);
      if (skill.providers.length) details.append(
        element('p', 'Connections required · ' + skill.providers.map(id => skillNames[id]).join(', ')),
        button('View connections', () => load('connections')));
      if (skill.tool) details.append(element('p', 'MCP · ' + skill.tool));
      const pinned = state.pinned.includes(skill.id);
      const pin = button(pinned ? 'Unpin' : 'Pin', () => save({
        pinned: pinned ? state.pinned.filter(id => id !== skill.id) : [...state.pinned, skill.id],
      }, skill.id));
      pin.setAttribute('aria-label', (pinned ? 'Unpin ' : 'Pin ') + skill.title);
      if (focusSkill === skill.id) focus = pin;
      card.append(details, pin);
      cards.append(card);
    }
    content.replaceChildren(cards, checklist(state.items));
    focus?.focus();
  }

  function checklist(items) {
    const panel = element('section', '', 'skill-checklist');
    const heading = element('h3', 'Your checklist');
    const completed = items.filter(item => item.done).length;
    const progress = element('p', completed + ' of ' + items.length + ' complete', 'provider-picker-message');
    const rows = element('ul', '', 'skill-checklist-items');
    items.forEach((item, index) => {
      const row = element('li');
      const label = element('label');
      const check = element('input');
      check.type = 'checkbox';
      check.checked = item.done;
      check.addEventListener('change', () => {
        const done = check.checked;
        check.checked = item.done;
        save({items: items.map((entry, i) => i === index ? {...entry, done} : entry)});
      });
      label.append(check, document.createTextNode(item.label));
      const remove = button('Remove', () => save({items: items.filter((_, i) => i !== index)}));
      remove.setAttribute('aria-label', 'Remove ' + item.label);
      row.append(label, remove);
      rows.append(row);
    });
    const add = element('form', '', 'skill-checklist-add');
    const input = element('textarea');
    input.rows = 2;
    input.maxLength = 3000;
    input.required = true;
    input.placeholder = 'Paste or type one action per line';
    input.setAttribute('aria-label', 'New checklist items');
    const submit = button('Add');
    submit.type = 'submit';
    input.disabled = submit.disabled = items.length >= 20;
    add.addEventListener('submit', event => {
      event.preventDefault();
      const additions = input.value.split(/\r?\n/).map(line =>
        line.trim().replace(/^(?:[-*]\s+)?\[[ xX]\]\s*|^[-*]\s+/, '')).filter(Boolean);
      if (!additions.length) return;
      if (items.length + additions.length > 20 || additions.some(label => label.length > 140)) {
        status.textContent = 'Use up to 20 items, with 140 characters per item.';
        return;
      }
      save({items: [...items, ...additions.map(label => ({label, done: false}))]});
    });
    add.append(input, submit);
    const copy = button('Copy checklist', async () => {
      try {
        await navigator.clipboard.writeText(items.map(item => (item.done ? '[x] ' : '[ ] ') + item.label).join('\n'));
        status.textContent = 'Checklist copied.';
      } catch {
        status.textContent = 'Clipboard unavailable.';
      }
    });
    copy.disabled = !items.length;
    panel.append(heading, progress, rows, add, copy);
    return panel;
  }

  async function save(changes, focusSkill) {
    if (busy || !savedSkills || view !== 'skills') return;
    busy = true;
    const state = savedSkills;
    const request = pending;
    const csrf = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith('ax_csrf='))?.slice(8) || '';
    status.textContent = 'Saving…';
    try {
      const response = await fetch(teamPath, {
        method: 'PUT', cache: 'no-store', credentials: 'same-origin', signal: request.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({revision: state.revision, pinned: state.pinned, items: state.items, ...changes}),
      });
      if (request.signal.aborted || view !== 'skills') return;
      if (response.status === 409) {
        await load('skills', 'Skills changed in another tab. Your update was not applied.');
        return;
      }
      if (!response.ok) throw new Error('Save failed');
      const result = await response.json();
      if (request.signal.aborted || view !== 'skills') return;
      showSkills({...state, ...result}, focusSkill);
      status.textContent = 'Saved.';
    } catch {
      if (!request.signal.aborted) status.textContent = 'Could not save. Try again.';
    } finally {
      busy = false;
    }
  }

  async function load(nextView, message = '') {
    view = nextView;
    const skills = view === 'skills';
    title.textContent = skills ? 'Skills' : 'Account connections';
    skillsTab.setAttribute('aria-pressed', String(skills));
    connectionsTab.setAttribute('aria-pressed', String(!skills));
    const request = new AbortController();
    pending?.abort();
    pending = request;
    savedSkills = null;
    retry.hidden = true;
    content.replaceChildren();
    status.textContent = skills ? 'Loading skills…' : 'Loading providers…';
    try {
      const response = await fetch(skills ? teamPath : '/api/connections/catalog', {
        cache: 'no-store', credentials: 'same-origin', signal: request.signal,
      });
      if (!response.ok) throw new Error('Catalog request failed');
      const result = await response.json();
      if (request.signal.aborted) return;
      if (skills) {
        status.textContent = message;
        showSkills(result);
      } else showProviders(result);
    } catch {
      if (request.signal.aborted) return;
      status.textContent = skills ? 'Could not load skills. Try again.' : 'Could not load providers. Try again.';
      retry.hidden = false;
    }
  }

  retry.addEventListener('click', () => load(view));
  connectionsTab.addEventListener('click', () => load('connections'));
  skillsTab.addEventListener('click', () => load('skills'));
  for (const [trigger, initial] of [[opener, 'connections'], [skillsOpener, 'skills']]) {
    if (!trigger) continue;
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.addEventListener('click', () => {
      returnFocus = trigger;
      expanded = new Set();
      dialog.showModal();
      load(initial);
    });
  }
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    pending?.abort();
    savedSkills = null;
    content.replaceChildren();
    if (!workspace?.hidden) returnFocus?.focus();
  });
  if (workspace) {
    new MutationObserver(() => {
      if (workspace.hidden && dialog.open) dialog.close();
    }).observe(workspace, {attributeFilter: ['hidden']});
  }
}
