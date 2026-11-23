/*
 * Small, bounded motion-compensated transitions between real portrait frames.
 * This is display interpolation, not another neural renderer. It estimates
 * bidirectional block motion on 64px luminance images and warps the two actual
 * 256px frames. It never extrapolates beyond an available neural endpoint.
 */

const SIZE = 256;
const ANALYSIS = 64;
const SCALE = SIZE / ANALYSIS;
const STEP = 2;
const GRID = ANALYSIS / STEP + 1;
const POINTS = GRID * GRID;
const PIXELS = SIZE * SIZE;
const SEARCH = 3;
const PATCH = 2;

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;

function validFrame(frame) {
  if (frame?.width !== SIZE || frame?.height !== SIZE ||
      !(frame.data instanceof Uint8ClampedArray) || frame.data.length !== PIXELS * 4) {
    throw new Error('Portrait motion requires a 256 by 256 RGBA frame.');
  }
}

function makeScratch() {
  const grid = () => ({x: new Float32Array(POINTS), y: new Float32Array(POINTS),
                       quality: new Float32Array(POINTS)});
  return {a: new Float32Array(ANALYSIS * ANALYSIS), b: new Float32Array(ANALYSIS * ANALYSIS),
          forward: grid(), backward: grid(), smooth: grid(),
          ax: new Float32Array(PIXELS), ay: new Float32Array(PIXELS),
          bx: new Float32Array(PIXELS), by: new Float32Array(PIXELS),
          confidence: new Float32Array(PIXELS)};
}

function luminance(frame, result) {
  const pixels = frame.data;
  for (let y = 0; y < ANALYSIS; y += 1) {
    for (let x = 0; x < ANALYSIS; x += 1) {
      let total = 0;
      for (let dy = 0; dy < SCALE; dy += 1) {
        for (let dx = 0; dx < SCALE; dx += 1) {
          const i = ((y * SCALE + dy) * SIZE + x * SCALE + dx) * 4;
          total += pixels[i] * 0.299 + pixels[i + 1] * 0.587 + pixels[i + 2] * 0.114;
        }
      }
      result[y * ANALYSIS + x] = total / (SCALE * SCALE);
    }
  }
}

function patchCost(a, b, x, y, dx, dy) {
  if (x + dx < 0 || x + dx >= ANALYSIS || y + dy < 0 || y + dy >= ANALYSIS) return Infinity;
  let total = 0, count = 0;
  const left = Math.max(-PATCH, -x, -x - dx);
  const right = Math.min(PATCH, ANALYSIS - 1 - x, ANALYSIS - 1 - x - dx);
  const top = Math.max(-PATCH, -y, -y - dy);
  const bottom = Math.min(PATCH, ANALYSIS - 1 - y, ANALYSIS - 1 - y - dy);
  for (let py = top; py <= bottom; py += 1) {
    const rowA = (y + py) * ANALYSIS + x;
    const rowB = (y + py + dy) * ANALYSIS + x + dx;
    for (let px = left; px <= right; px += 1) {
      const difference = a[rowA + px] - b[rowB + px];
      total += difference * difference;
      count += 1;
    }
  }
  return count ? total / count : Infinity;
}

function sampleLuma(values, x, y) {
  const px = clamp(x, 0, ANALYSIS - 1), py = clamp(y, 0, ANALYSIS - 1);
  const x0 = Math.floor(px), y0 = Math.floor(py);
  const x1 = Math.min(x0 + 1, ANALYSIS - 1), y1 = Math.min(y0 + 1, ANALYSIS - 1);
  const fx = px - x0, fy = py - y0;
  return (values[y0 * ANALYSIS + x0] * (1 - fx) + values[y0 * ANALYSIS + x1] * fx) * (1 - fy) +
         (values[y1 * ANALYSIS + x0] * (1 - fx) + values[y1 * ANALYSIS + x1] * fx) * fy;
}

function refine(a, b, x, y, dx, dy, result, index) {
  // Two small Lucas-Kanade corrections resolve subpixel motion. Fitting a
  // parabola to integer patch errors overstates tiny movements near an edge.
  for (let iteration = 0; iteration < 2; iteration += 1) {
    let xx = 0, xy = 0, yy = 0, ex = 0, ey = 0;
    for (let py = Math.max(-PATCH, 1 - y); py <= Math.min(PATCH, ANALYSIS - 2 - y); py += 1) {
      for (let px = Math.max(-PATCH, 1 - x); px <= Math.min(PATCH, ANALYSIS - 2 - x); px += 1) {
        const tx = x + px + dx, ty = y + py + dy;
        const error = a[(y + py) * ANALYSIS + x + px] - sampleLuma(b, tx, ty);
        const gx = (sampleLuma(b, tx + 1, ty) - sampleLuma(b, tx - 1, ty)) * 0.5;
        const gy = (sampleLuma(b, tx, ty + 1) - sampleLuma(b, tx, ty - 1)) * 0.5;
        xx += gx * gx; xy += gx * gy; yy += gy * gy; ex += gx * error; ey += gy * error;
      }
    }
    const ridge = Math.max(0.01, (xx + yy) * 0.01);
    xx += ridge; yy += ridge;
    const determinant = xx * yy - xy * xy;
    if (determinant < 0.01) break;
    dx = clamp(dx + clamp((yy * ex - xy * ey) / determinant, -0.5, 0.5), -SEARCH - 0.5, SEARCH + 0.5);
    dy = clamp(dy + clamp((xx * ey - xy * ex) / determinant, -0.5, 0.5), -SEARCH - 0.5, SEARCH + 0.5);
  }
  result.x[index] = dx; result.y[index] = dy;
}

function match(a, b, field) {
  for (let gy = 0; gy < GRID; gy += 1) {
    for (let gx = 0; gx < GRID; gx += 1) {
      const x = Math.min(ANALYSIS - 1, gx * STEP);
      const y = Math.min(ANALYSIS - 1, gy * STEP);
      const index = gy * GRID + gx;
      let bestX = 0, bestY = 0, best = patchCost(a, b, x, y, 0, 0);
      // A small displacement prior keeps blank backgrounds and repeating
      // texture stationary instead of selecting arbitrary distant matches.
      if (best > 4) {
        for (let dy = -SEARCH; dy <= SEARCH; dy += 1) {
          for (let dx = -SEARCH; dx <= SEARCH; dx += 1) {
            const cost = patchCost(a, b, x, y, dx, dy) + 0.12 * (dx * dx + dy * dy);
            if (cost < best) { best = cost; bestX = dx; bestY = dy; }
          }
        }
      }
      const cost = patchCost(a, b, x, y, bestX, bestY);
      if (cost < 0.01) { field.x[index] = bestX; field.y[index] = bestY; }
      else refine(a, b, x, y, bestX, bestY, field, index);
      field.quality[index] = 1 / (1 + cost / 225);
    }
  }
}

function smooth(field, guide, temporary) {
  for (let gy = 0; gy < GRID; gy += 1) {
    for (let gx = 0; gx < GRID; gx += 1) {
      const i = gy * GRID + gx;
      const center = guide[Math.min(gy * STEP, ANALYSIS - 1) * ANALYSIS + Math.min(gx * STEP, ANALYSIS - 1)];
      let x = field.x[i] * 8, y = field.y[i] * 8, weight = 8;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const nx = gx + dx, ny = gy + dy;
          if ((!dx && !dy) || nx < 0 || nx >= GRID || ny < 0 || ny >= GRID) continue;
          const j = ny * GRID + nx;
          const adjacent = guide[Math.min(ny * STEP, ANALYSIS - 1) * ANALYSIS + Math.min(nx * STEP, ANALYSIS - 1)];
          const w = field.quality[j] / (1 + Math.abs(adjacent - center) / 12);
          x += field.x[j] * w; y += field.y[j] * w; weight += w;
        }
      }
      temporary.x[i] = x / weight; temporary.y[i] = y / weight;
    }
  }
  field.x.set(temporary.x); field.y.set(temporary.y);
}

function sampleGrid(values, x, y) {
  const px = clamp(x / STEP, 0, GRID - 1), py = clamp(y / STEP, 0, GRID - 1);
  const x0 = Math.floor(px), y0 = Math.floor(py), x1 = Math.min(x0 + 1, GRID - 1), y1 = Math.min(y0 + 1, GRID - 1);
  const fx = px - x0, fy = py - y0;
  return (values[y0 * GRID + x0] * (1 - fx) + values[y0 * GRID + x1] * fx) * (1 - fy) +
         (values[y1 * GRID + x0] * (1 - fx) + values[y1 * GRID + x1] * fx) * fy;
}

function estimate(a, b, scratch) {
  luminance(a, scratch.a); luminance(b, scratch.b);
  const forward = scratch.forward, backward = scratch.backward;
  match(scratch.a, scratch.b, forward); match(scratch.b, scratch.a, backward);
  smooth(forward, scratch.a, scratch.smooth); smooth(backward, scratch.b, scratch.smooth);
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const i = y * SIZE + x, px = x / SCALE, py = y / SCALE;
      const ax = sampleGrid(forward.x, px, py), ay = sampleGrid(forward.y, px, py);
      const bx = sampleGrid(backward.x, px, py), by = sampleGrid(backward.y, px, py);
      const returnX = sampleGrid(backward.x, px + ax, py + ay);
      const returnY = sampleGrid(backward.y, px + ax, py + ay);
      const otherX = sampleGrid(forward.x, px + bx, py + by);
      const otherY = sampleGrid(forward.y, px + bx, py + by);
      const disagreement = Math.max((ax + returnX) ** 2 + (ay + returnY) ** 2,
                                    (bx + otherX) ** 2 + (by + otherY) ** 2);
      const quality = Math.min(sampleGrid(forward.quality, px, py), sampleGrid(backward.quality, px, py));
      scratch.ax[i] = ax * SCALE; scratch.ay[i] = ay * SCALE;
      scratch.bx[i] = bx * SCALE; scratch.by[i] = by * SCALE;
      // Forward/backward disagreement flags occlusion or an unreliable match.
      scratch.confidence[i] = quality * Math.max(0, 1 - disagreement / 2.25);
    }
  }
}

function sampleChannel(pixels, i, j, k, l, x, y, channel) {
  return (pixels[i + channel] * (1 - x) + pixels[j + channel] * x) * (1 - y) +
         (pixels[k + channel] * (1 - x) + pixels[l + channel] * x) * y;
}

function warp(a, b, scratch, output, amount) {
  const left = a.data, right = b.data, result = output.data;
  const local = clamp((amount - 0.15) / 0.7, 0, 1);
  const conservative = local * local * (3 - 2 * local);
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const i = y * SIZE + x;
      const ax = clamp(x - amount * scratch.ax[i], 0, SIZE - 1);
      const ay = clamp(y - amount * scratch.ay[i], 0, SIZE - 1);
      const bx = clamp(x - (1 - amount) * scratch.bx[i], 0, SIZE - 1);
      const by = clamp(y - (1 - amount) * scratch.by[i], 0, SIZE - 1);
      const x0 = Math.floor(ax), y0 = Math.floor(ay), x1 = Math.min(x0 + 1, SIZE - 1), y1 = Math.min(y0 + 1, SIZE - 1);
      const u0 = Math.floor(bx), v0 = Math.floor(by), u1 = Math.min(u0 + 1, SIZE - 1), v1 = Math.min(v0 + 1, SIZE - 1);
      const fx = ax - x0, fy = ay - y0, fu = bx - u0, fv = by - v0;
      const ai = (y0 * SIZE + x0) * 4, aj = (y0 * SIZE + x1) * 4;
      const ak = (y1 * SIZE + x0) * 4, al = (y1 * SIZE + x1) * 4;
      const bi = (v0 * SIZE + u0) * 4, bj = (v0 * SIZE + u1) * 4;
      const bk = (v1 * SIZE + u0) * 4, bl = (v1 * SIZE + u1) * 4;
      const ar = sampleChannel(left, ai, aj, ak, al, fx, fy, 0);
      const ag = sampleChannel(left, ai, aj, ak, al, fx, fy, 1);
      const ab = sampleChannel(left, ai, aj, ak, al, fx, fy, 2);
      const br = sampleChannel(right, bi, bj, bk, bl, fu, fv, 0);
      const bg = sampleChannel(right, bi, bj, bk, bl, fu, fv, 1);
      const bb = sampleChannel(right, bi, bj, bk, bl, fu, fv, 2);
      const difference = Math.max(Math.abs(ar - br), Math.abs(ag - bg), Math.abs(ab - bb));
      const matched = clamp((scratch.confidence[i] - 0.12) / 0.38, 0, 1) *
                      clamp((56 - difference) / 24, 0, 1);
      // New/disappearing detail has no reliable correspondence. A continuous
      // local dissolve avoids a midpoint cut or per-channel color fringe.
      // It can soften unmatched detail briefly; it does not invent geometry.
      const weight = amount * matched + conservative * (1 - matched);
      result[i * 4] = ar * (1 - weight) + br * weight;
      result[i * 4 + 1] = ag * (1 - weight) + bg * weight;
      result[i * 4 + 2] = ab * (1 - weight) + bb * weight;
      result[i * 4 + 3] = 255;
    }
  }
}

/**
 * The caller owns its display clock and at most one pending neural frame.
 * append() rejects overlap without retaining the new frame. Input ImageData
 * must remain unchanged while retained. reset() releases all pixel/flow data;
 * reset(frame) also paints that exact endpoint. No timer or worker is created.
 */
export function createMotionCompositor({canvas} = {}) {
  const context = canvas?.getContext('2d');
  if (!context || canvas.width !== SIZE || canvas.height !== SIZE) {
    throw new Error('Portrait motion requires a 256 by 256 canvas.');
  }
  let previous = null, next = null, scratch = null, output = null;
  let start = 0, duration = 0, active = false, disposed = false, matchMs = 0, linear = false;
  function release() { previous = null; next = null; scratch = null; output = null; active = false; matchMs = 0; }
  return {
    get active() { return active; },
    get matchMs() { return matchMs; },
    get startedAt() { return start; },
    get ownedBufferBytes() {
      const frameBytes = (previous ? PIXELS * 4 : 0) + (next ? PIXELS * 4 : 0) + (output ? PIXELS * 4 : 0);
      return frameBytes + (scratch ? (2 * ANALYSIS * ANALYSIS + 9 * POINTS + 5 * PIXELS) * 4 : 0);
    },
    reset(frame) {
      release();
      if (disposed) return;
      if (frame) { validFrame(frame); previous = frame; context.putImageData(frame, 0, 0); }
    },
    append(frame, {durationMs = 180, now = performance.now(), easing = 'smoothstep'} = {}) {
      if (disposed || active) return false;
      validFrame(frame);
      if (!previous) { previous = frame; context.putImageData(frame, 0, 0); return true; }
      next = frame;
      scratch ||= makeScratch();
      output ||= context.createImageData(SIZE, SIZE);
      const before = performance.now();
      estimate(previous, next, scratch);
      matchMs = performance.now() - before;
      // Estimation is synchronous and must not consume the display interval,
      // especially on a slow device or on the first JIT-cold transition.
      start = finite(now, before) + matchMs;
      duration = clamp(finite(durationMs, 180), 80, 15000);
      // Automatic poses already follow an eased sinusoidal trajectory. A
      // linear interval avoids bringing their velocity to zero at every pose.
      linear = easing === 'linear';
      active = true;
      return true;
    },
    draw(now = performance.now()) {
      if (disposed || !active) return {active: false, progress: 1, interpolated: false, painted: false};
      const elapsed = finite(now, start) - start;
      const progress = elapsed >= duration - 1e-6 ? 1 : clamp(elapsed / duration, 0, 1);
      if (progress >= 1) {
        context.putImageData(next, 0, 0);
        previous = next; next = null; active = false;
        return {active: false, progress: 1, interpolated: false, painted: true};
      }
      if (progress <= 0) context.putImageData(previous, 0, 0);
      else {
        const amount = linear ? progress : progress * progress * (3 - 2 * progress);
        warp(previous, next, scratch, output, amount);
        context.putImageData(output, 0, 0);
      }
      return {active: true, progress, interpolated: progress > 0, painted: true};
    },
    dispose() { release(); disposed = true; },
  };
}
