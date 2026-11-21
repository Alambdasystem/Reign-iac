import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../services/ax/portrait-motion.js', import.meta.url), 'utf8');
const {createMotionCompositor} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const size = 256;

function frame(shift = 0) {
  const data = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const sx = Math.max(0, Math.min(size - 1, x - shift));
      const value = 128 + Math.sin(sx * 0.13) * 42 + Math.sin(y * 0.19 + sx * 0.08) * 40;
      const i = (y * size + x) * 4;
      data[i] = value;
      data[i + 1] = value + Math.sin(sx * 0.07 + y * 0.17) * 28;
      data[i + 2] = value + Math.cos(sx * 0.11 - y * 0.09) * 24;
      data[i + 3] = 255;
    }
  }
  return {width: size, height: size, data};
}

function harness() {
  let painted = null, paints = 0, allocations = 0;
  const context = {
    putImageData(image) { painted = new Uint8ClampedArray(image.data); paints += 1; },
    createImageData(width, height) { allocations += 1; return {width, height, data: new Uint8ClampedArray(width * height * 4)}; },
  };
  return {compositor: createMotionCompositor({canvas: {width: size, height: size, getContext: () => context}}),
          get painted() { return painted; }, get paints() { return paints; }, get allocations() { return allocations; }};
}

function mse(left, right) {
  let error = 0, count = 0;
  for (let y = 16; y < size - 16; y += 1) {
    for (let x = 16; x < size - 16; x += 1) {
      for (let c = 0; c < 3; c += 1) {
        const i = (y * size + x) * 4 + c;
        error += (left[i] - right[i]) ** 2; count += 1;
      }
    }
  }
  return error / count;
}

const elapsed = (h, milliseconds) => h.compositor.draw(h.compositor.startedAt + milliseconds);

test('a translated image moves through its middle position instead of dissolving two outlines', () => {
  const h = harness(), a = frame(), b = frame(8), expected = frame(4);
  h.compositor.reset(a);
  assert.equal(h.compositor.append(b, {durationMs: 1000, now: 0}), true);
  const state = elapsed(h, 500);
  assert.equal(state.active, true);
  assert.equal(state.interpolated, true);
  assert.equal(state.painted, true);
  assert.ok(Math.abs(state.progress - 0.5) < 1e-9);
  const dissolve = Uint8ClampedArray.from(a.data, (value, i) => (value + b.data[i]) / 2);
  assert.ok(mse(h.painted, expected.data) < mse(dissolve, expected.data) * 0.5,
            'motion compensation should improve a known geometric intermediate');
  assert.notDeepEqual(h.painted, a.data);
  assert.notDeepEqual(h.painted, b.data);
  assert.notDeepEqual(h.painted, dissolve);
  assert.ok([...h.painted].every(Number.isFinite));
});

test('exact neural endpoints, no extrapolation, and overlapping frames are never retained', () => {
  const h = harness(), a = frame(), b = frame(4), unused = frame(-4);
  h.compositor.reset(a);
  assert.deepEqual(h.painted, a.data);
  assert.equal(h.compositor.append(b, {durationMs: 100, now: 40}), true);
  const bytes = h.compositor.ownedBufferBytes;
  assert.equal(h.compositor.append(unused, {durationMs: 100, now: 50}), false);
  assert.equal(h.compositor.ownedBufferBytes, bytes);
  elapsed(h, 0);
  assert.deepEqual(h.painted, a.data);
  assert.deepEqual(elapsed(h, 100), {active: false, progress: 1, interpolated: false, painted: true});
  assert.deepEqual(h.painted, b.data);
  const paints = h.paints;
  assert.equal(h.compositor.draw(1000000).painted, false);
  assert.equal(h.paints, paints);
  assert.deepEqual(h.painted, b.data);
});

test('reset and disposal drop old portrait pixels and cancel any transition', () => {
  const h = harness(), a = frame(), b = frame(4), replacement = frame(-8);
  h.compositor.reset(a);
  h.compositor.append(b, {now: 0});
  elapsed(h, 80);
  h.compositor.reset(replacement);
  assert.equal(h.compositor.active, false);
  assert.deepEqual(h.painted, replacement.data);
  assert.equal(h.compositor.ownedBufferBytes, size * size * 4);
  assert.equal(h.compositor.draw(1000).painted, false);
  h.compositor.reset();
  assert.equal(h.compositor.ownedBufferBytes, 0);
  h.compositor.append(a);
  h.compositor.dispose();
  h.compositor.dispose();
  assert.equal(h.compositor.ownedBufferBytes, 0);
  assert.equal(h.compositor.append(b), false);
  assert.equal(h.compositor.draw(1000).painted, false);
});

test('display frames reuse bounded storage and accept only fixed-size inputs', () => {
  const h = harness();
  assert.throws(() => h.compositor.reset({width: 512, height: 512, data: new Uint8ClampedArray(512 * 512 * 4)}), /256/);
  h.compositor.reset(frame());
  h.compositor.append(frame(2), {durationMs: 1000, now: 0});
  const bytes = h.compositor.ownedBufferBytes;
  assert.ok(bytes < 2300000);
  for (let time = 0; time < 1000; time += 20) {
    elapsed(h, time);
    assert.equal(h.compositor.ownedBufferBytes, bytes);
  }
  elapsed(h, 1000);
  h.compositor.append(frame(4), {durationMs: 1000, now: 1000});
  assert.equal(h.allocations, 1, 'reuse the output image across display frames and keyframe pairs');
  assert.equal(h.compositor.ownedBufferBytes, bytes);
});

test('a finite duration ceiling settles even a stalled slow-device estimate', () => {
  const h = harness();
  h.compositor.reset(frame());
  h.compositor.append(frame(2), {durationMs: 999999, now: 0});
  assert.equal(elapsed(h, 14999).active, true);
  assert.equal(elapsed(h, 15000).active, false);
  h.compositor.append(frame(3), {durationMs: NaN, now: 20000});
  assert.equal(h.compositor.draw(NaN).progress, 0);
  assert.equal(elapsed(h, 180).active, false);
});

test('unmatched changing detail stays continuous through the middle and uses one RGB weight', () => {
  const h = harness(), a = frame(), b = frame();
  for (let i = 0; i < a.data.length; i += 4) {
    a.data.set([0, 30, 240, 255], i); b.data.set([250, 220, 10, 255], i);
  }
  h.compositor.reset(a); h.compositor.append(b, {durationMs: 1000, now: 0});
  elapsed(h, 499);
  const before = h.painted;
  elapsed(h, 501);
  for (let i = 0; i < before.length; i += 4) {
    assert.ok(Math.abs(before[i] - h.painted[i]) < 4, 'no hard switch at t=0.5');
  }
  elapsed(h, 500);
  assert.ok(Math.abs(h.painted[0] - 125) <= 1);
  assert.ok(Math.abs(h.painted[1] - 125) <= 1);
  assert.ok(Math.abs(h.painted[2] - 125) <= 1);
});

test('estimation time does not skip the start of a transition and stationary images stay exact', () => {
  const h = harness(), a = frame();
  h.compositor.reset(a); h.compositor.append(a, {durationMs: 80, now: 0});
  assert.equal(h.compositor.startedAt, h.compositor.matchMs);
  assert.equal(h.compositor.draw(h.compositor.matchMs / 2).progress, 0);
  elapsed(h, 40);
  assert.deepEqual(h.painted, a.data);
});

test('automatic linear intervals maintain motion while manual transitions ease into the pose', () => {
  const automatic = harness(), manual = harness(), a = frame(), b = frame(8);
  for (const h of [automatic, manual]) h.compositor.reset(a);
  automatic.compositor.append(b, {durationMs: 1000, now: 0, easing: 'linear'});
  manual.compositor.append(b, {durationMs: 1000, now: 0});
  elapsed(automatic, 250); elapsed(manual, 250);
  const linearPosition = frame(2).data, easedPosition = frame(1.25).data;
  assert.ok(mse(automatic.painted, linearPosition) < mse(automatic.painted, easedPosition));
  assert.ok(mse(manual.painted, easedPosition) < mse(manual.painted, linearPosition));
  for (const h of [automatic, manual]) {
    elapsed(h, 1000);
    assert.deepEqual(h.painted, b.data);
  }
});
