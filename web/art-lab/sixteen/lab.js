// Art lab renderer for the 16-bit direction. Everything is drawn into an indexed framebuffer (one byte per
// pixel, palette index), the way a 16-bit console composes tiles and sprites; colour is applied only at the
// end. Cast shadows and the guide's glow are palette look-ups (one ramp step darker / lighter), not alpha.
// Integer positions only; scaling is integer and nearest-neighbour.

// resolved at run time (a literal new URL(..., import.meta.url) would be rewritten by the bundler)
const here = import.meta.url.replace(/[^/]*$/, '');
const base = here + 'assets/';
const load = (n) => fetch(base + n).then((r) => r.json());

const [PAL, TILES, SPR, SCENE] = await Promise.all([
  load('palette.json'), load('tiles.json'), load('sprites.json'), load('scene.json'),
]);

const ALPH = PAL.alphabet;
const DEC = new Map([...ALPH].map((c, i) => [c, i]));
const RGB = PAL.colours.map((h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]);

function decodeRows(rows) {
  const h = rows.length, w = rows[0].length, px = new Uint8Array(w * h);
  rows.forEach((r, y) => { for (let x = 0; x < w; x++) px[y * w + x] = DEC.get(r[x]); });
  return px;
}
const tiles = TILES.tiles.map((s) => { const px = new Uint8Array(256); for (let i = 0; i < 256; i++) px[i] = DEC.get(s[i]); return px; });
const sprites = {};
for (const [name, s] of Object.entries(SPR)) sprites[name] = { ...s, px: s.frames.map(decodeRows) };

class Frame {
  constructor(w, h, fill = 0) { this.w = w; this.h = h; this.px = new Uint8Array(w * h).fill(fill); }
  tile(id, tx, ty) {
    const t = tiles[id], T = TILES.tile;
    for (let y = 0; y < T; y++) this.px.set(t.subarray(y * T, y * T + T), (ty * T + y) * this.w + tx * T);
  }
  // draw a sprite frame with its anchor at (x, y)
  sprite(name, frame, x, y, mirror = false, ax = null, ay = null) {
    const s = sprites[name];
    const f = s.px[((frame % s.px.length) + s.px.length) % s.px.length];
    const ox = x - (ax ?? s.ox), oy = y - (ay ?? s.oy);
    for (let j = 0; j < s.h; j++) {
      const yy = oy + j; if (yy < 0 || yy >= this.h) continue;
      for (let i = 0; i < s.w; i++) {
        const xx = ox + i; if (xx < 0 || xx >= this.w) continue;
        const v = f[j * s.w + (mirror ? s.w - 1 - i : i)];
        if (v) this.px[yy * this.w + xx] = v;
      }
    }
  }
  // remap pixels inside an ellipse through a palette table; the outer ring is dithered 50 %
  ellipse(cx, cy, rx, ry, table) {
    for (let y = Math.floor(cy - ry); y <= Math.ceil(cy + ry); y++) {
      if (y < 0 || y >= this.h) continue;
      for (let x = Math.floor(cx - rx); x <= Math.ceil(cx + rx); x++) {
        if (x < 0 || x >= this.w) continue;
        const d = ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2;
        if (d > 1) continue;
        if (d > 0.62 && ((x + y) & 1)) continue;
        const i = y * this.w + x; this.px[i] = table[this.px[i]];
      }
    }
  }
  // remap every pixel inside a polygon (even-odd rule, pixel centres) through a palette table
  polygon(pts, table) {
    const ys = pts.map((p) => p[1]), y0 = Math.max(0, Math.min(...ys)), y1 = Math.min(this.h - 1, Math.max(...ys));
    for (let y = y0; y <= y1; y++) {
      const cy = y + 0.5, xs = [];
      for (let i = 0; i < pts.length; i++) {
        const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % pts.length];
        if ((ay <= cy && by > cy) || (by <= cy && ay > cy)) xs.push(ax + ((cy - ay) / (by - ay)) * (bx - ax));
      }
      xs.sort((a, b) => a - b);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        for (let x = Math.max(0, Math.ceil(xs[k] - 0.5)); x <= Math.min(this.w - 1, Math.floor(xs[k + 1] - 0.5)); x++) {
          const i = y * this.w + x; this.px[i] = table[this.px[i]];
        }
      }
    }
  }
  toImageData() {
    const out = new ImageData(this.w, this.h);
    for (let i = 0; i < this.px.length; i++) {
      const v = this.px[i], c = RGB[v];
      out.data[i * 4] = c[0]; out.data[i * 4 + 1] = c[1]; out.data[i * 4 + 2] = c[2]; out.data[i * 4 + 3] = 255;
    }
    return out;
  }
}

const animFrame = (a, t) => (a ? Math.floor((t * a.fps) / 1000) + (a.phase ?? 0) : 0);

// the player walks a loop on the path: integer pixel steps, direction from the segment
function walker(t) {
  const pts = SCENE.walk.path, speed = SCENE.walk.speed;
  const segs = pts.map((p, i) => [p, pts[(i + 1) % pts.length]]);
  const lens = segs.map(([a, b]) => Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]));
  const total = lens.reduce((a, b) => a + b, 0);
  let d = Math.floor((t * speed) / 1000) % total;
  for (let i = 0; i < segs.length; i++) {
    if (d <= lens[i]) {
      const [a, b] = segs[i];
      const dx = Math.sign(b[0] - a[0]), dy = Math.sign(b[1] - a[1]);
      const x = a[0] + dx * Math.min(d, Math.abs(b[0] - a[0]));
      const y = a[1] + dy * Math.min(d, Math.abs(b[1] - a[1]));
      const dir = dx > 0 ? 'right' : dx < 0 ? 'left' : dy < 0 ? 'up' : 'down';
      return { x, y, dir, frame: Math.floor(((t * speed) / 1000) / 6) % 4 };
    }
    d -= lens[i];
  }
  return { x: pts[0][0], y: pts[0][1], dir: 'down', frame: 0 };
}

export function renderScene(t, opts = {}) {
  const f = new Frame(SCENE.width, SCENE.height);
  const map = TILES.maps[Math.floor(t / TILES.frameMs) % TILES.maps.length];
  for (let ty = 0; ty < TILES.rows; ty++) for (let tx = 0; tx < TILES.cols; tx++) f.tile(map[ty * TILES.cols + tx], tx, ty);
  const objs = SCENE.objects.map((o) => ({ ...o }));
  const p = objs.find((o) => o.walker);
  if (p) {
    const w = opts.still ? { x: p.x, y: p.y, dir: 'down', frame: 0 } : walker(t);
    Object.assign(p, { x: w.x, y: w.y, sprite: 'player_' + w.dir, frameNo: w.frame });
  }
  for (const o of objs) if (o.flat) f.sprite(o.sprite, 0, o.x, o.y, false, o.ax, o.ay);
  for (const o of objs) if (o.shade) f.polygon(o.shade, PAL.darker);
  for (const o of objs) if (o.shadow) f.ellipse(o.x + o.shadow[0], o.y + o.shadow[1], o.shadow[2], o.shadow[3], PAL.darker);
  for (const o of objs) if (o.glow) f.ellipse(o.x + o.glow[0], o.y + o.glow[1], o.glow[2], o.glow[3], PAL.glow);
  const sorted = objs.filter((o) => !o.flat && o.layer !== 'top').sort((a, b) => a.y - b.y || a.x - b.x);
  for (const o of sorted) f.sprite(o.sprite, o.frameNo ?? animFrame(o.anim, t), o.x, o.y, false, o.ax, o.ay);
  for (const o of objs) if (o.layer === 'top') f.sprite(o.sprite, animFrame(o.anim, t), o.x, o.y);
  return f;
}

// background for the small sheets: a strip of the scene's own grass tile
function grassFrame(w, h) {
  const f = new Frame(w, h);
  const g = sprites.grass_variant_0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) f.px[y * w + x] = g.px[0][(y % 16) * 16 + (x % 16)];
  return f;
}

export function renderPortrait() {
  const s = sprites.portrait, f = new Frame(s.w, s.h);
  f.sprite('portrait', 0, 0, 0);
  return f;
}

export function renderOwls(t) {
  const f = grassFrame(120, 40);
  const fly = Math.floor(t / 160) % 2;
  f.ellipse(14, 31, 6, 2, PAL.darker); f.sprite('byte_perch', 0, 14, 31);
  f.ellipse(38, 33, 5, 1.5, PAL.darker); f.sprite('byte_fly', fly, 38, 20);
  f.ellipse(82, 31, 6, 2, PAL.darker); f.sprite('nocti_perch', 0, 82, 31);
  f.ellipse(106, 33, 5, 1.5, PAL.darker); f.sprite('nocti_fly', fly, 106, 20);
  return f;
}

export function renderWalk(t, sheet = false) {
  const dirs = ['down', 'left', 'right', 'up'];
  if (sheet) {
    const f = grassFrame(4 * 20, 4 * 28);
    dirs.forEach((d, r) => { for (let k = 0; k < 4; k++) { f.ellipse(10 + k * 20, 26 + r * 28, 6, 2, PAL.darker); f.sprite('player_' + d, k, 10 + k * 20, 26 + r * 28); } });
    return f;
  }
  const f = grassFrame(4 * 24, 30);
  const k = Math.floor(t / 150) % 4;
  dirs.forEach((d, i) => { f.ellipse(12 + i * 24, 27, 6, 2, PAL.darker); f.sprite('player_' + d, k, 12 + i * 24, 27); });
  return f;
}

export function renderSheet(t) {
  const names = Object.keys(sprites);
  const f = new Frame(400, 520, 3);
  let x = 2, y = 2, rowH = 0;
  for (const n of names) {
    const s = sprites[n];
    for (let k = 0; k < s.px.length; k++) {
      if (x + s.w > 398) { x = 2; y += rowH + 3; rowH = 0; }
      f.sprite(n, k, x, y, false, 0, 0);
      x += s.w + 3; rowH = Math.max(rowH, s.h);
    }
  }
  return f;
}

export const VIEWS = {
  scene: (t) => renderScene(t),
  still: (t) => renderScene(t, { still: true }),
  portrait: () => renderPortrait(),
  owls: (t) => renderOwls(t),
  walk: (t) => renderWalk(t),
  walksheet: (t) => renderWalk(t, true),
  sheet: (t) => renderSheet(t),
};

export function toCanvas(frame, scale, canvas = document.createElement('canvas')) {
  const src = document.createElement('canvas');
  src.width = frame.w; src.height = frame.h;
  src.getContext('2d').putImageData(frame.toImageData(), 0, 0);
  canvas.width = frame.w * scale; canvas.height = frame.h * scale;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
  return canvas;
}

export const info = { colours: PAL.count, tiles: TILES.tiles.length, sprites: Object.keys(sprites).length };

window.lab = {
  ready: true, info,
  capture: (view, t = 0, scale = 1) => toCanvas(VIEWS[view](t), scale).toDataURL('image/png'),
};
