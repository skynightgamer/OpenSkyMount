// Drawing the constellation art: each picture is a textured mesh warped onto the sky projection (canvas 2D can only
// do affine maps, so the picture is cut into small triangles, each drawn with its own affine transform).
// The pictures are opaque: grey paint on solid black, made to be added to the sky ("lighter"), so black adds nothing.
import { angleDeg, figureNightFactor, MESH, type FigureMesh } from "./figures";
import { mulMV, project, type Basis, type Mat3, type Vec3 } from "./projection";

/** Opacity of the art: most figures sit back, the one the view is centred on comes forward. */
export const FIGURE_ALPHA = { rest: 0.3, focus: 0.62 };

// ---------------------------------------------------------------- picture loading
const images = new Map<string, HTMLImageElement | "loading" | "failed">();
let imageRevision = 0;

/** Counts finished picture loads, so a map that only redraws on change knows a new picture can be drawn. */
export function figureImageRevision(): number {
  return imageRevision;
}

/** The figure's picture once it has loaded (the load starts on first use); null while loading or if it failed. */
export function figureImage(id: string): HTMLImageElement | null {
  const have = images.get(id);
  if (have === "loading" || have === "failed") return null;
  if (have) return have;
  if (typeof Image === "undefined") return null;
  images.set(id, "loading");
  const img = new Image();
  img.onload = () => {
    images.set(id, img);
    imageRevision++;
  };
  img.onerror = () => images.set(id, "failed");
  img.src = `/figures/${id}.png`;
  return null;
}

// ---------------------------------------------------------------- hit testing
const LUMA_SIZE = 96;
const lumaMaps = new Map<string, Uint8Array | null>();

/** Brightest paint within 2 cells of a picture pixel (so thin strokes are easy to hit), 0 while loading. */
export function figureLuma(id: string, px: number, py: number): number {
  let map = lumaMaps.get(id);
  if (map === undefined) {
    const img = figureImage(id);
    if (!img || typeof document === "undefined") return 0; // not loaded yet: try again on the next click
    map = null;
    try {
      const c = document.createElement("canvas");
      c.width = c.height = LUMA_SIZE;
      const g = c.getContext("2d", { willReadFrequently: true });
      if (g) {
        g.drawImage(img, 0, 0, LUMA_SIZE, LUMA_SIZE);
        const rgba = g.getImageData(0, 0, LUMA_SIZE, LUMA_SIZE).data;
        map = new Uint8Array(LUMA_SIZE * LUMA_SIZE);
        for (let i = 0; i < map.length; i++) map[i] = Math.max(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]);
      }
    } catch {
      map = null;
    }
    lumaMaps.set(id, map);
  }
  if (!map) return 0;
  const img = figureImage(id);
  const w = img?.naturalWidth || LUMA_SIZE;
  const h = img?.naturalHeight || LUMA_SIZE;
  const cx = Math.floor((px / w) * LUMA_SIZE);
  const cy = Math.floor((py / h) * LUMA_SIZE);
  let best = 0;
  for (let y = Math.max(0, cy - 2); y <= Math.min(LUMA_SIZE - 1, cy + 2); y++) {
    for (let x = Math.max(0, cx - 2); x <= Math.min(LUMA_SIZE - 1, cx + 2); x++) best = Math.max(best, map[y * LUMA_SIZE + x]);
  }
  return best;
}

// ---------------------------------------------------------------- focus animation
const shown = new Map<string, number>(); // current opacity per figure, eased toward its target
let lastT = 0;
let settling = false;

/** True while some figure's opacity is still easing toward its target, so another frame will change the picture. */
export function figureLayerSettling(): boolean {
  return settling;
}

/** Index of the figure the view is centred on (nearest centre within 90 % of its radius), or -1. */
export function focusedFigure(figs: FigureMesh[], horizonCentres: Vec3[], forward: Vec3, fov: number): number {
  if (fov > 110) return -1;
  let best = -1;
  let bestRatio = 0.9;
  for (let i = 0; i < figs.length; i++) {
    const ratio = angleDeg(horizonCentres[i], forward) / figs[i].radiusDeg;
    if (ratio < bestRatio) {
      bestRatio = ratio;
      best = i;
    }
  }
  return best;
}

// ---------------------------------------------------------------- mesh drawing
let scratch: HTMLCanvasElement | null = null;

function scratchFor(wPx: number, hPx: number): CanvasRenderingContext2D | null {
  if (typeof document === "undefined") return null;
  scratch ??= document.createElement("canvas");
  if (scratch.width !== wPx || scratch.height !== hPx) {
    scratch.width = wPx;
    scratch.height = hPx;
  }
  return scratch.getContext("2d");
}

/** Smallest of the lattice strides (divisors of MESH) giving cells of roughly `target` pixels or less. */
function cellsFor(sizePx: number, target = 70): number {
  const want = Math.max(2, Math.ceil(sizePx / target));
  for (const n of [2, 3, 4, 6, MESH]) if (n >= want) return n;
  return MESH;
}

type Pt = [number, number];

/** Affine transform taking source triangle s to destination triangle d: [a, b, c, d, e, f] for setTransform. */
export function triangleTransform(s: [Pt, Pt, Pt], d: [Pt, Pt, Pt]): [number, number, number, number, number, number] | null {
  const [s0, s1, s2] = s;
  const [d0, d1, d2] = d;
  const den = (s1[0] - s0[0]) * (s2[1] - s0[1]) - (s2[0] - s0[0]) * (s1[1] - s0[1]);
  if (Math.abs(den) < 1e-9) return null;
  const a = ((d1[0] - d0[0]) * (s2[1] - s0[1]) - (d2[0] - d0[0]) * (s1[1] - s0[1])) / den;
  const c = ((d2[0] - d0[0]) * (s1[0] - s0[0]) - (d1[0] - d0[0]) * (s2[0] - s0[0])) / den;
  const b = ((d1[1] - d0[1]) * (s2[1] - s0[1]) - (d2[1] - d0[1]) * (s1[1] - s0[1])) / den;
  const dd = ((d2[1] - d0[1]) * (s1[0] - s0[0]) - (d1[1] - d0[1]) * (s2[0] - s0[0])) / den;
  return [a, b, c, dd, d0[0] - a * s0[0] - c * s0[1], d0[1] - b * s0[0] - dd * s0[1]];
}

/**
 * Draw one figure. `m` takes J2000 vectors to the horizon frame; `alpha` is the final opacity. Returns false when
 * nothing was drawn (off screen, behind the camera, picture not loaded yet).
 */
export function drawFigure(
  ctx: CanvasRenderingContext2D,
  b: Basis,
  m: Mat3,
  fig: FigureMesh,
  img: HTMLImageElement,
  alpha: number,
  w: number,
  h: number,
  dpr: number,
  maxJump: number,
  tint: string | null,
): boolean {
  // Project the coarse lattice first to size the mesh and the scratch area.
  const stride0 = MESH / 3;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let j = 0; j <= MESH; j += stride0) {
    for (let i = 0; i <= MESH; i += stride0) {
      const p = project(b, mulMV(m, fig.grid[j * (MESH + 1) + i]), -0.3);
      if (!p) return false;
      minX = Math.min(minX, p[0]);
      minY = Math.min(minY, p[1]);
      maxX = Math.max(maxX, p[0]);
      maxY = Math.max(maxY, p[1]);
    }
  }
  if (maxX < 0 || minX > w || maxY < 0 || minY > h) return false;
  const n = cellsFor(Math.max(maxX - minX, maxY - minY));
  const stride = MESH / n;
  const V = n + 1;
  const dest: (Pt | null)[] = [];
  minX = minY = Infinity;
  maxX = maxY = -Infinity;
  for (let j = 0; j <= n; j++) {
    for (let i = 0; i <= n; i++) {
      const p = project(b, mulMV(m, fig.grid[j * stride * (MESH + 1) + i * stride]), -0.3);
      dest.push(p ? [p[0], p[1]] : null);
      if (p) {
        minX = Math.min(minX, p[0]);
        minY = Math.min(minY, p[1]);
        maxX = Math.max(maxX, p[0]);
        maxY = Math.max(maxY, p[1]);
      }
    }
  }
  // Scratch rectangle: the picture's bounds clipped to the canvas.
  const x0 = Math.max(0, Math.floor(minX) - 2);
  const y0 = Math.max(0, Math.floor(minY) - 2);
  const x1 = Math.min(w, Math.ceil(maxX) + 2);
  const y1 = Math.min(h, Math.ceil(maxY) + 2);
  if (x1 - x0 < 2 || y1 - y0 < 2) return false;
  const g = scratchFor(Math.ceil((x1 - x0) * dpr), Math.ceil((y1 - y0) * dpr));
  if (!g) return false;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalCompositeOperation = "source-over";
  g.globalAlpha = 1;
  g.fillStyle = "#000";
  g.fillRect(0, 0, g.canvas.width, g.canvas.height);
  const sw = img.naturalWidth / MESH;
  const sh = img.naturalHeight / MESH;
  let drawn = 0;
  const tri = (a: number, bI: number, c: number, sa: Pt, sb: Pt, sc: Pt) => {
    const da = dest[a];
    const db = dest[bI];
    const dc = dest[c];
    if (!da || !db || !dc) return;
    if (Math.max(Math.hypot(da[0] - db[0], da[1] - db[1]), Math.hypot(db[0] - dc[0], db[1] - dc[1]), Math.hypot(dc[0] - da[0], dc[1] - da[1])) > maxJump) return;
    // Destination in scratch pixels.
    const D = [da, db, dc].map((p) => [(p[0] - x0) * dpr, (p[1] - y0) * dpr] as Pt) as [Pt, Pt, Pt];
    const t = triangleTransform([sa, sb, sc], D);
    if (!t) return;
    // Clip to the triangle grown by ~0.7 px so neighbouring triangles overlap instead of leaving hairline seams.
    const cx = (D[0][0] + D[1][0] + D[2][0]) / 3;
    const cy = (D[0][1] + D[1][1] + D[2][1]) / 3;
    g.save();
    g.beginPath();
    D.forEach(([px, py], k) => {
      const dx = px - cx;
      const dy = py - cy;
      const l = Math.hypot(dx, dy) || 1;
      const q: Pt = [px + (dx / l) * 0.7 * dpr, py + (dy / l) * 0.7 * dpr];
      if (k === 0) g.moveTo(q[0], q[1]);
      else g.lineTo(q[0], q[1]);
    });
    g.closePath();
    g.clip();
    g.setTransform(t[0], t[1], t[2], t[3], t[4], t[5]);
    g.drawImage(img, 0, 0);
    g.restore();
    drawn++;
  };
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const i00 = j * V + i;
      const i10 = i00 + 1;
      const i01 = i00 + V;
      const i11 = i01 + 1;
      const s00: Pt = [i * stride * sw, j * stride * sh];
      const s10: Pt = [(i + 1) * stride * sw, j * stride * sh];
      const s01: Pt = [i * stride * sw, (j + 1) * stride * sh];
      const s11: Pt = [(i + 1) * stride * sw, (j + 1) * stride * sh];
      tri(i00, i10, i01, s00, s10, s01);
      tri(i10, i11, i01, s10, s11, s01);
    }
  }
  if (!drawn) return false;
  if (tint) {
    // Multiply keeps the black background black and gives the grey paint the tint.
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = "multiply";
    g.fillStyle = tint;
    g.fillRect(0, 0, g.canvas.width, g.canvas.height);
    g.globalCompositeOperation = "source-over";
  }
  const before = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = "lighter";
  ctx.globalAlpha = alpha;
  ctx.drawImage(g.canvas, 0, 0, g.canvas.width, g.canvas.height, x0, y0, x1 - x0, y1 - y0);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = before;
  return true;
}

// ---------------------------------------------------------------- the layer
export interface FigureLayerArgs {
  ctx: CanvasRenderingContext2D;
  b: Basis;
  m: Mat3;
  figs: FigureMesh[];
  w: number;
  h: number;
  dpr: number;
  fov: number;
  sunAlt: number;
  maxJump: number;
  tMs: number;
  reducedMotion: boolean;
  /** A figure drawn as if the view were centred on it (the selected constellation). */
  highlight?: string | null;
}

/** Draw every visible figure, easing each one's opacity toward its target (focused figure brighter). */
export function drawFigureLayer(a: FigureLayerArgs): void {
  const night = figureNightFactor(a.sunAlt);
  const dt = lastT ? Math.min(0.25, (a.tMs - lastT) / 1000) : 0;
  lastT = a.tMs;
  const k = a.reducedMotion || !dt ? 1 : 1 - Math.exp(-dt / 0.25);
  const reach = a.fov * 0.5 * Math.max(1, Math.max(a.w, a.h) / Math.min(a.w, a.h)) * 1.15;
  const horizon = a.figs.map((f) => mulMV(a.m, f.centre));
  const focus = focusedFigure(a.figs, horizon, a.b.f, a.fov);
  settling = false;
  for (let i = 0; i < a.figs.length; i++) {
    const f = a.figs[i];
    const target = night * (i === focus || f.id === a.highlight ? FIGURE_ALPHA.focus : FIGURE_ALPHA.rest);
    const cur = shown.get(f.id) ?? target;
    const next = cur + (target - cur) * k;
    shown.set(f.id, next);
    if (Math.abs(target - next) > 0.003) settling = true;
    if (next < 0.01) continue;
    if (angleDeg(horizon[i], a.b.f) > reach + f.radiusDeg) continue;
    const img = figureImage(f.id);
    if (!img) continue;
    drawFigure(a.ctx, a.b, a.m, f, img, next, a.w, a.h, a.dpr, a.maxJump, "rgb(178,200,246)");
  }
}
