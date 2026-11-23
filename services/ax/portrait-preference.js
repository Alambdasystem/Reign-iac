/* Small, browser-local Lab preferences. No credentials or server uploads. */
export const MAX_PORTRAIT_BYTES = 512 * 1024;
export const MAX_PORTRAIT_EDGE = 1024;
export const MAX_PORTRAIT_ACCOUNTS = 16;
const DATABASE = 'workspace-portrait-preferences-v1';
const STORE = 'portraits';
const MIME_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

export class PortraitPreferenceError extends Error {
  constructor(code = 'unavailable') { super('Local portrait preference ' + code); this.code = code; }
}

export function portraitAccountKey(profile) {
  if (!profile?.authenticated || !['owner', 'avatar', 'employee'].includes(profile.role)) throw new PortraitPreferenceError('account');
  if (profile.role === 'owner') return 'owner';
  if (typeof profile.username !== 'string' || !/^[a-z][a-z0-9_.-]{0,63}$/.test(profile.username)) throw new PortraitPreferenceError('account');
  return profile.role + ':' + profile.username;
}
function accountKey(value) {
  if (typeof value !== 'string' || !/^(?:owner|(?:avatar|employee):[a-z][a-z0-9_.-]{0,63})$/.test(value)) throw new PortraitPreferenceError('account');
  return value;
}
export function portraitCrop(value = {zoom: 1, x: 0, y: 0}) {
  if (!value || typeof value !== 'object' || Object.keys(value).some(key => !['zoom', 'x', 'y'].includes(key)) ||
      !Number.isFinite(value.zoom) || value.zoom < 1 || value.zoom > 3 ||
      !Number.isFinite(value.x) || Math.abs(value.x) > 1 || !Number.isFinite(value.y) || Math.abs(value.y) > 1)
    throw new PortraitPreferenceError('invalid');
  return {zoom: value.zoom, x: value.x, y: value.y};
}
async function validateImage(value) {
  if (!value || !(value.blob instanceof Blob) || !MIME_TYPES.has(value.blob.type) ||
      value.blob.size < 12 || value.blob.size > MAX_PORTRAIT_BYTES ||
      !Number.isInteger(value.width) || !Number.isInteger(value.height) ||
      value.width < 128 || value.height < 128 || Math.max(value.width, value.height) > MAX_PORTRAIT_EDGE)
    throw new PortraitPreferenceError('invalid');
  const bytes = new Uint8Array(await value.blob.slice(0, 16).arrayBuffer());
  const png = bytes[0] === 137 && String.fromCharCode(...bytes.slice(1, 4)) === 'PNG';
  const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  const webp = String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
  if (!(value.blob.type === 'image/png' && png || value.blob.type === 'image/jpeg' && jpeg || value.blob.type === 'image/webp' && webp))
    throw new PortraitPreferenceError('invalid');
  return {blob: value.blob, width: value.width, height: value.height, crop: portraitCrop(value.crop)};
}

export async function preparePortrait(image, doc = document) {
  const width = image.naturalWidth, height = image.naturalHeight;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 128 || height < 128 || width * height > 40_000_000)
    throw new PortraitPreferenceError('invalid');
  const scale = Math.min(1, MAX_PORTRAIT_EDGE / Math.max(width, height));
  const canvas = doc.createElement('canvas');
  canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  try {
    if (Math.min(canvas.width, canvas.height) < 128) throw new PortraitPreferenceError('invalid');
    const context = canvas.getContext('2d', {alpha: false});
    if (!context) throw new PortraitPreferenceError('unavailable');
    context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    for (const [type, quality] of [['image/webp', .86], ['image/jpeg', .8], ['image/jpeg', .6]]) {
      const blob = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new PortraitPreferenceError('unavailable')), 5000);
        try { canvas.toBlob(result => { clearTimeout(timer); resolve(result); }, type, quality); }
        catch { clearTimeout(timer); reject(new PortraitPreferenceError('unavailable')); }
      });
      if (blob && blob.size <= MAX_PORTRAIT_BYTES) {
        const result = await validateImage({blob, width: canvas.width, height: canvas.height, crop: {zoom: 1, x: 0, y: 0}});
        return {...result, image: canvas};
      }
    }
    throw new PortraitPreferenceError('invalid');
  } catch (error) { canvas.width = canvas.height = 0; throw error; }
}

export function createPortraitPreferences({indexedDB = globalThis.indexedDB, timeoutMs = 3000} = {}) {
  let closed = false, writes = Promise.resolve();
  const pending = new Set();
  function transaction(mode, operation) {
    return new Promise((resolve, reject) => {
      if (closed || !indexedDB?.open) { reject(new PortraitPreferenceError('unavailable')); return; }
      let db, tx, value, finished = false;
      const finish = error => {
        if (finished) return;
        finished = true; clearTimeout(timer); pending.delete(cancel);
        if (error && tx) { try { tx.abort(); } catch {} }
        db?.close(); error ? reject(error) : resolve(value);
      };
      const cancel = () => finish(new PortraitPreferenceError('unavailable'));
      const timer = setTimeout(cancel, timeoutMs);
      pending.add(cancel);
      let opening;
      try { opening = indexedDB.open(DATABASE, 1); } catch { cancel(); return; }
      opening.onupgradeneeded = () => {
        if (finished || closed) { try { opening.transaction.abort(); } catch {} return; }
        if (!opening.result.objectStoreNames.contains(STORE)) opening.result.createObjectStore(STORE, {keyPath: 'account'});
      };
      opening.onerror = opening.onblocked = cancel;
      opening.onsuccess = () => {
        db = opening.result;
        if (finished || closed) { db.close(); cancel(); return; }
        db.onversionchange = () => { db.close(); cancel(); };
        try {
          tx = db.transaction(STORE, mode);
          tx.oncomplete = () => finish(); tx.onerror = tx.onabort = cancel;
          operation(tx.objectStore(STORE), result => { value = result; }, finish);
        } catch { cancel(); }
      };
    });
  }
  function ordered(operation) {
    const result = writes.then(() => {
      if (closed) throw new PortraitPreferenceError('unavailable');
      return operation();
    });
    writes = result.catch(() => {}); return result;
  }
  function remove(key) {
    accountKey(key);
    return ordered(() => transaction('readwrite', store => { store.delete(key); }));
  }
  return {
    async load(key) {
      accountKey(key);
      const record = await transaction('readonly', (store, result) => {
        store.get(key).onsuccess = event => result(event.target.result);
      });
      if (record === undefined) return null;
      if (!record || record.account !== key || record.version !== 1 ||
          Object.keys(record).some(field => !['account', 'version', 'blob', 'width', 'height', 'crop'].includes(field)))
        throw new PortraitPreferenceError('invalid');
      const value = await validateImage(record);
      if (closed) throw new PortraitPreferenceError('unavailable');
      // The controller owns any removal after its source/account epoch check.
      // A stale corrupt read must never erase a newer valid save.
      return value;
    },
    save(key, value) {
      accountKey(key);
      return ordered(async () => {
        const safe = await validateImage(value);
        if (closed) throw new PortraitPreferenceError('unavailable');
        return transaction('readwrite', (store, result, fail) => {
          store.get(key).onsuccess = event => {
            const put = () => {
              try { store.put({account: key, version: 1, ...safe}); result(true); }
              catch { fail(new PortraitPreferenceError('unavailable')); }
            };
            if (event.target.result !== undefined) { put(); return; }
            store.count().onsuccess = count => {
              if (count.target.result >= MAX_PORTRAIT_ACCOUNTS) fail(new PortraitPreferenceError('full'));
              else put();
            };
          };
        });
      });
    },
    remove,
    close() { closed = true; for (const cancel of [...pending]) cancel(); },
  };
}
