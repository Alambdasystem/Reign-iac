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
  const dialog = element('dialog', '', 'provider-picker');
  const title = element('h2', 'Account connections');
  title.id = 'provider-picker-title';
  dialog.setAttribute('aria-labelledby', title.id);
  const close = element('button', 'Close', 'provider-picker-button');
  close.type = 'submit';
  close.autofocus = true;
  const form = element('form');
  form.method = 'dialog';
  form.append(close);
  const header = element('div', '', 'provider-picker-header');
  header.append(title, form);
  const status = element('p', '', 'provider-picker-message');
  status.setAttribute('role', 'status');
  const providers = element('ul', '', 'provider-picker-list');
  providers.setAttribute('role', 'list');
  const retry = element('button', 'Try again', 'provider-picker-button');
  retry.type = 'button';
  retry.hidden = true;
  dialog.append(header, status, providers, retry);
  document.body.append(dialog);
  opener.setAttribute('aria-haspopup', 'dialog');

  const consolePane = opener.closest('#console');
  let pending;
  async function loadCatalog() {
    const request = new AbortController();
    pending?.abort();
    pending = request;
    retry.hidden = true;
    providers.replaceChildren();
    status.textContent = 'Loading providers…';
    try {
      const response = await fetch('/api/connections/catalog', {
        cache: 'no-store', credentials: 'same-origin', signal: request.signal,
      });
      if (!response.ok) throw new Error('Provider catalog request failed');
      const catalog = await response.json();
      if (request.signal.aborted) return;
      providers.replaceChildren(...catalog.providers.map(providerCard));
      status.textContent = 'Account linking is currently unavailable.';
    } catch {
      if (request.signal.aborted) return;
      status.textContent = 'Could not load providers. Try again.';
      retry.hidden = false;
    }
  }

  retry.addEventListener('click', loadCatalog);
  opener.addEventListener('click', () => {
    dialog.showModal();
    loadCatalog();
  });
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    pending?.abort();
    if (!consolePane?.hidden) opener.focus();
  });
  if (consolePane) {
    new MutationObserver(() => {
      if (consolePane.hidden && dialog.open) dialog.close();
    }).observe(consolePane, {attributeFilter: ['hidden']});
  }
}
