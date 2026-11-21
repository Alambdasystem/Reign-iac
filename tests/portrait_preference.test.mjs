import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../services/ax/portrait-preference.js', import.meta.url), 'utf8');
const {createPortraitPreferences, portraitAccountKey, portraitCrop, preparePortrait,
  MAX_PORTRAIT_BYTES, MAX_PORTRAIT_ACCOUNTS} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const png = () => new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0])], {type: 'image/png'});
const portrait = (extra = {}) => ({blob: png(), width: 512, height: 512, crop: {zoom: 1.4, x: .2, y: -.4}, ...extra});
const rejected = (operation, code) => assert.rejects(operation, error => error.code === code);

// A small transactional test double: requests are asynchronous, completion
// waits for requests queued by callbacks, and abort never commits the draft.
// Real IndexedDB persistence is also exercised by the browser acceptance gate.
function database({hang = false, quota = false} = {}) {
  const records = new Map(), requests = [];
  let opens = 0, closes = 0;
  const indexedDB = {open(name, version) {
    assert.equal(name, 'workspace-portrait-preferences-v1'); assert.equal(version, 1);
    opens++;
    const opening = {}; requests.push(opening);
    if (hang) return opening;
    queueMicrotask(() => {
      const db = {close() { closes++; }, transaction(storeName, mode) {
        assert.equal(storeName, 'portraits');
        const draft = new Map(records);
        let pending = 0, ended = false;
        const tx = {abort() { if (!ended) { ended = true; queueMicrotask(() => tx.onabort?.()); } }};
        function completion() {
          setImmediate(() => {
            if (pending || ended) return;
            ended = true;
            if (mode === 'readwrite') { records.clear(); for (const [key, value] of draft) records.set(key, value); }
            tx.oncomplete?.();
          });
        }
        function request(operation) {
          if (ended) throw new Error('inactive transaction');
          const value = {}; pending++;
          queueMicrotask(() => {
            if (ended) return;
            try { value.result = operation(); value.onsuccess?.({target: value}); }
            catch { tx.abort(); }
            pending--; completion();
          });
          return value;
        }
        tx.objectStore = () => ({
          get: key => request(() => draft.get(key)), count: () => request(() => draft.size),
          delete: key => request(() => draft.delete(key)),
          put: value => { if (quota) throw new Error('quota'); return request(() => draft.set(value.account, value)); },
        });
        completion(); return tx;
      }};
      opening.result = db; opening.onsuccess?.();
    });
    return opening;
  }};
  return {indexedDB, records, requests, get opens() { return opens; }, get closes() { return closes; }};
}

test('account keys require authenticated canonical identities and keep named roles separate', () => {
  assert.equal(portraitAccountKey({authenticated: true, role: 'owner', username: 'admin'}), 'owner');
  assert.equal(portraitAccountKey({authenticated: true, role: 'owner', username: 'owner'}), 'owner');
  assert.equal(portraitAccountKey({authenticated: true, role: 'avatar', username: 'mayur'}), 'avatar:mayur');
  assert.equal(portraitAccountKey({authenticated: true, role: 'employee', username: 'mayur'}), 'employee:mayur');
  for (const profile of [{}, {authenticated: false, role: 'owner'}, {authenticated: true, role: 'guest'},
    {authenticated: true, role: 'avatar', username: '../admin'}, {authenticated: true, role: 'avatar', username: 'Mayur'}])
    assert.throws(() => portraitAccountKey(profile), error => error.code === 'account');
});

test('saved image and crop survive a new preference instance while another account remains empty', async () => {
  const db = database(), first = createPortraitPreferences(db), value = portrait();
  assert.equal(db.opens, 0, 'construction does not read browser storage');
  await first.save('avatar:mayur', value); first.close();
  const restored = createPortraitPreferences(db);
  assert.equal(await restored.load('owner'), null);
  assert.deepEqual(await restored.load('avatar:mayur'), value);
  assert.deepEqual(Object.keys(db.records.get('avatar:mayur')).sort(), ['account', 'blob', 'crop', 'height', 'version', 'width']);
  assert.equal(db.opens, db.closes);
});

test('oversize, disguised, corrupt, and out-of-bounds records fail closed', async () => {
  const db = database(), preferences = createPortraitPreferences(db);
  const cases = [portrait({blob: new Blob([new Uint8Array(MAX_PORTRAIT_BYTES + 1)], {type: 'image/png'})}),
    portrait({blob: new Blob(['not-an-image-content'], {type: 'image/png'})}),
    portrait({width: 2048}), portrait({height: 0}), portrait({crop: {zoom: 9, x: 0, y: 0}}),
    portrait({crop: {zoom: 1, x: NaN, y: 0}}), portrait({crop: {zoom: 1, x: 0, y: 0, token: 'never-store'}})];
  for (const value of cases) await rejected(preferences.save('owner', value), 'invalid');
  assert.equal(db.records.size, 0);
  db.records.set('owner', {account: 'avatar:mayur', version: 1, ...portrait()});
  await rejected(preferences.load('owner'), 'invalid');
  db.records.set('owner', {account: 'owner', version: 1, ...portrait(), token: 'unexpected-field'});
  await rejected(preferences.load('owner'), 'invalid');
  assert.equal(db.records.size, 1, 'a stale invalid read cannot delete a newer write; controller owns guarded removal');
});

test('saved account count is bounded without evicting another person’s portrait', async () => {
  const db = database(), preferences = createPortraitPreferences(db);
  for (let i = 0; i < MAX_PORTRAIT_ACCOUNTS; i++) await preferences.save('avatar:user' + i, portrait());
  await rejected(preferences.save('owner', portrait()), 'full');
  assert.equal(db.records.size, MAX_PORTRAIT_ACCOUNTS);
  await preferences.save('avatar:user0', portrait({crop: {zoom: 2, x: 0, y: 0}}));
  assert.equal((await preferences.load('avatar:user0')).crop.zoom, 2);
  assert.equal(db.records.has('owner'), false);
});

test('remove is ordered after pending saves and never deletes another account', async () => {
  const db = database(), preferences = createPortraitPreferences(db);
  await preferences.save('avatar:mayur', portrait());
  const saving = preferences.save('owner', portrait());
  const removing = preferences.remove('owner');
  await Promise.all([saving, removing]);
  assert.equal(await preferences.load('owner'), null);
  assert.ok(await preferences.load('avatar:mayur'));
});

test('quota and unavailable IndexedDB reject safely without committing partial records', async () => {
  const db = database({quota: true}), preferences = createPortraitPreferences(db);
  await rejected(preferences.save('owner', portrait()), 'unavailable');
  assert.equal(db.records.size, 0);
  await rejected(createPortraitPreferences({indexedDB: undefined}).load('owner'), 'unavailable');
});

test('a blocked storage operation times out and close cancels both an open and queued writes', async () => {
  const stalled = database({hang: true});
  await rejected(createPortraitPreferences({...stalled, timeoutMs: 15}).load('owner'), 'unavailable');
  const db = database({hang: true}), preferences = createPortraitPreferences({...db, timeoutMs: 1000});
  const loading = preferences.load('owner'), saving = preferences.save('owner', portrait());
  preferences.close();
  await Promise.all([rejected(loading, 'unavailable'), rejected(saving, 'unavailable')]);
  assert.equal(db.records.size, 0);
  assert.equal(db.opens, 1);
});

test('normalization bounds decoded pixels, strips the original payload, and keeps a valid crop', async () => {
  const calls = [];
  const canvas = {width: 0, height: 0, getContext: () => ({fillRect(...args) { calls.push(args); }, drawImage(...args) { calls.push(args); }}),
    toBlob(callback) { callback(png()); }};
  const result = await preparePortrait({naturalWidth: 4000, naturalHeight: 3000}, {createElement: () => canvas});
  assert.equal(result.width, 1024); assert.equal(result.height, 768);
  assert.equal(result.blob.size, 12); assert.equal(result.image, canvas);
  assert.deepEqual(result.crop, {zoom: 1, x: 0, y: 0});
  assert.equal(calls[1][3], 1024); assert.equal(calls[1][4], 768);
  assert.deepEqual(portraitCrop({zoom: 3, x: -1, y: 1}), {zoom: 3, x: -1, y: 1});
  await rejected(preparePortrait({naturalWidth: 10_000, naturalHeight: 10_000}, {createElement() { throw new Error('must reject before allocation'); }}), 'invalid');
});
