// Sky-map look: sky background (twilight/day colours, horizon glow, moonlight), Milky Way, glow sprites, Moon phase.
// Everything here is cosmetic. Positions come from the catalog / backend; the Milky Way map is the Milky Way Outline
// Catalog (J. R. Vieira, via d3-celestial) rasterised by scripts/build_milkyway.py.
import mwUrl from "./milkyway.png";
import { DEG, type Basis, type Mat3, type Vec3 } from "./projection";

export type RGB = [number, number, number];

function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

export function smoothstep(a: number, b: number, x: number): number {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
}

export interface SkyLight {
  sun: Vec3; // horizon unit vector (x = North, y = East, z = Up)
  sunAlt: number; // degrees
  moon: Vec3 | null;
  moonAlt: number;
  moonIllum: number; // 0..1
}

/** Milky Way visibility factor 0..1 for the current light (dark night = 1). */
export function milkyWayVisibility(l: SkyLight): number {
  const dark = 1 - smoothstep(-18, -9, l.sunAlt);
  const moonlight = l.moon ? l.moonIllum * smoothstep(-2, 15, l.moonAlt) : 0;
  return dark * (1 - 0.65 * moonlight);
}

export type SkyShader = (vx: number, vy: number, vz: number, alt: number, out: RGB) => RGB;

/**
 * Sky colour shader (0..255 RGB, before the Milky Way) for a direction (vx, vy, vz) at altitude `alt` (deg, >= 0).
 * A simple empirical model: night base with a horizon glow, a twilight glow toward the Sun, a blue day sky with a
 * sun halo, and a moonlit brightening. Per-light constants are computed once; the per-pixel part avoids inverse
 * trig (angular distances via 1 − cos).
 */
export function skyShader(l: SkyLight): SkyShader {
  const dayF = smoothstep(-7, 6, l.sunAlt);
  const twF = smoothstep(-18, -3, l.sunAlt) * (1 - smoothstep(2, 14, l.sunAlt));
  const tb = twF * (1 - dayF);
  const [sx, sy, sz] = l.sun;
  const mF = l.moon && l.moonAlt > -2 ? l.moonIllum * smoothstep(-2, 20, l.moonAlt) * (1 - dayF) : 0;
  const [mx, my, mz] = l.moon ?? [0, 0, 1];
  // exp(-(sep/55°)²) and exp(-sep/9°) with sep² ≈ 2(1 − cos sep)
  const kWarm = 2 / (55 * DEG) ** 2;
  const kHalo = 1 / (9 * DEG);
  return (vx, vy, vz, alt, out) => {
    const a01 = clamp01(alt / 90);
    const h = 1 - a01; // 1 at the horizon, 0 at the zenith
    const h3 = h * h * h;
    const oneMinusCosS = 1 - (vx * sx + vy * sy + vz * sz);
    // Night: almost black zenith, faint grey-blue glow toward the horizon (airglow / distant light).
    let r = 3 + 13 * h3;
    let g = 5 + 15 * h3;
    let b = 11 + 19 * h3;
    if (dayF > 0) {
      // Day: deep blue zenith, pale horizon, halo around the Sun.
      r += (34 + 110 * h3 - r) * dayF;
      g += (84 + 96 * h3 - g) * dayF;
      b += (160 + 60 * h3 - b) * dayF;
      const halo = dayF * Math.exp(-Math.sqrt(2 * Math.max(0, oneMinusCosS)) * kHalo);
      r += 120 * halo;
      g += 110 * halo;
      b += 80 * halo;
    }
    if (twF > 0) {
      // Twilight: blue overhead, warm band toward the Sun's azimuth near the horizon.
      r += 8 * tb;
      g += 20 * tb * (0.6 + 0.4 * a01);
      b += 46 * tb * (0.6 + 0.4 * a01);
      const warm = twF * Math.exp(-oneMinusCosS * kWarm) * (0.25 + h3);
      r += 190 * warm;
      g += 92 * warm;
      b += 38 * warm;
    }
    if (mF > 0) {
      // Moonlight: a dim blue-grey brightening, stronger near the Moon.
      const near = Math.exp(-(1 - (vx * mx + vy * my + vz * mz)) * 18);
      r += mF * (5 + 28 * near);
      g += mF * (8 + 32 * near);
      b += mF * (14 + 38 * near);
    }
    out[0] = r;
    out[1] = g;
    out[2] = b;
    return out;
  };
}

/** Convenience wrapper around `skyShader` for a single direction. */
export function skyColor(v: Vec3, alt: number, l: SkyLight, out: RGB): RGB {
  return skyShader(l)(v[0], v[1], v[2], alt, out);
}

// ------------------------------------------------------------------ Milky Way map
export interface MwMap {
  w: number;
  h: number;
  data: Uint8Array; // one byte per cell, 0..255
}

let mwMap: MwMap | null = null;
let mwRequested = false;
export function getMilkyWay(): MwMap | null {
  if (!mwRequested && typeof Image !== "undefined") {
    mwRequested = true;
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const g = c.getContext("2d", { willReadFrequently: true })!;
      g.drawImage(img, 0, 0);
      const px = g.getImageData(0, 0, c.width, c.height).data;
      const data = new Uint8Array(c.width * c.height);
      for (let i = 0; i < data.length; i++) data[i] = px[i * 4];
      mwMap = { w: c.width, h: c.height, data };
    };
    img.onerror = () => (mwRequested = false);
    img.src = mwUrl;
  }
  return mwMap;
}

/** Bilinear sample (0..1) of an equirectangular RA/Dec map at J2000 RA/Dec in degrees; RA wraps. */
export function sampleMap(m: MwMap, raDeg: number, decDeg: number): number {
  const fx = (((raDeg % 360) + 360) % 360) * (m.w / 360) - 0.5;
  const fy = (90 - decDeg) * (m.h / 180) - 0.5;
  const x0 = Math.floor(fx);
  const y0 = Math.max(0, Math.min(m.h - 1, Math.floor(fy)));
  const y1 = Math.min(m.h - 1, y0 + 1);
  const tx = fx - x0;
  const ty = Math.max(0, Math.min(1, fy - y0));
  const xa = ((x0 % m.w) + m.w) % m.w;
  const xb = (xa + 1) % m.w;
  const d = m.data;
  const top = d[y0 * m.w + xa] * (1 - tx) + d[y0 * m.w + xb] * tx;
  const bot = d[y1 * m.w + xa] * (1 - tx) + d[y1 * m.w + xb] * tx;
  return (top * (1 - ty) + bot * ty) / 255;
}

// ------------------------------------------------------------------ background raster
/** CSS pixels per background sample: 4, coarser on large canvases (about 10k samples at most, ~3 ms). */
function cellSize(w: number, h: number): number {
  return Math.max(4, Math.ceil(Math.sqrt((w * h) / 10000)));
}
let bgCanvas: HTMLCanvasElement | null = null;
let bgKey = "";

/**
 * Draw the sky background (sky colour + Milky Way) as a low-resolution raster scaled up with smoothing.
 * `m` maps J2000 vectors to the horizon frame; the raster is rebuilt only when the view, light or time changes.
 */
export function drawSkyBackground(
  ctx: CanvasRenderingContext2D,
  b: Basis,
  w: number,
  h: number,
  m: Mat3 | null,
  light: SkyLight,
  milkyWay: boolean,
  timeKey: number,
) {
  const CELL = cellSize(w, h);
  const gw = Math.ceil(w / CELL) + 1;
  const gh = Math.ceil(h / CELL) + 1;
  const mw = milkyWay && m ? getMilkyWay() : null;
  const mwVis = mw ? milkyWayVisibility(light) : 0;
  const key = [CELL, gw, gh, b.f.join(","), b.scale.toFixed(4), light.sunAlt.toFixed(1), light.moonAlt.toFixed(0), mwVis > 0 ? 1 : 0, !!mw, timeKey].join("|");
  if (!bgCanvas) bgCanvas = document.createElement("canvas");
  if (key !== bgKey) {
    bgKey = key;
    if (bgCanvas.width !== gw || bgCanvas.height !== gh) {
      bgCanvas.width = gw;
      bgCanvas.height = gh;
    }
    const g = bgCanvas.getContext("2d")!;
    const img = g.createImageData(gw, gh);
    const px = img.data;
    const col: RGB = [0, 0, 0];
    const shade = skyShader(light);
    for (let j = 0; j < gh; j++) {
      const Y = -(j * CELL - b.cy) / b.scale;
      for (let i = 0; i < gw; i++) {
        // Inverse stereographic projection (same as projection.unproject, inlined for speed).
        const X = (i * CELL - b.cx) / b.scale;
        const rho = Math.hypot(X, Y);
        let x = 0;
        let y = 0;
        let z = 1;
        if (rho > 1e-12) {
          const c = 2 * Math.atan(rho / 2);
          const sc = Math.sin(c) / rho;
          x = X * sc;
          y = Y * sc;
          z = Math.cos(c);
        }
        const vx = x * b.r[0] + y * b.u[0] + z * b.f[0];
        const vy = x * b.r[1] + y * b.u[1] + z * b.f[1];
        const vz = x * b.r[2] + y * b.u[2] + z * b.f[2];
        const alt = Math.asin(Math.max(-1, Math.min(1, vz))) / DEG;
        shade(vx, vy, vz, Math.max(0, alt), col);
        if (mw && mwVis > 0 && alt > -1 && m) {
          // horizon -> J2000: transpose of m
          const ex = m[0][0] * vx + m[1][0] * vy + m[2][0] * vz;
          const ey = m[0][1] * vx + m[1][1] * vy + m[2][1] * vz;
          const ez = m[0][2] * vx + m[1][2] * vy + m[2][2] * vz;
          const ra = Math.atan2(ey, ex) / DEG;
          const dec = Math.asin(Math.max(-1, Math.min(1, ez))) / DEG;
          const s = sampleMap(mw, ra, dec);
          if (s > 0.004) {
            // Extinction toward the horizon; a soft curve so faint outer levels still show.
            const ext = smoothstep(-1, 25, alt);
            const k = Math.pow(s, 0.6) * mwVis * ext;
            col[0] += 72 * k;
            col[1] += 70 * k;
            col[2] += 78 * k;
          }
        }
        const o = (j * gw + i) * 4;
        px[o] = col[0];
        px[o + 1] = col[1];
        px[o + 2] = col[2];
        px[o + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bgCanvas, 0, 0, gw * CELL, gh * CELL);
}

// ------------------------------------------------------------------ glow sprites
const sprites = new Map<string, HTMLCanvasElement>();

/** Radial glow sprite: white core, coloured halo. Drawn at half-size `r / CORE` so the core radius is about `r`. */
export const SPRITE_CORE = 0.2;
export function glowSprite(color: string): HTMLCanvasElement {
  let s = sprites.get(color);
  if (s) return s;
  s = document.createElement("canvas");
  s.width = s.height = 64;
  const g = s.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  const [r, gg, bb] = hexRgb(color);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(SPRITE_CORE * 0.55, `rgba(${r},${gg},${bb},1)`);
  grad.addColorStop(SPRITE_CORE, `rgba(${r},${gg},${bb},0.75)`);
  grad.addColorStop(0.36, `rgba(${r},${gg},${bb},0.18)`);
  grad.addColorStop(0.6, `rgba(${r},${gg},${bb},0.05)`);
  grad.addColorStop(1, `rgba(${r},${gg},${bb},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  sprites.set(color, s);
  return s;
}

export function hexRgb(hex: string): RGB {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// ------------------------------------------------------------------ Moon
/**
 * Path of the sunlit part of a disc of radius `r` at the origin whose bright limb faces +x, for illuminated fraction
 * `k` (0 new .. 1 full). The terminator is a half-ellipse with semi-axis r·|1 − 2k| along x.
 */
export function moonLitPath(ctx: CanvasRenderingContext2D, r: number, k: number) {
  const e = r * Math.abs(1 - 2 * k);
  ctx.beginPath();
  ctx.arc(0, 0, r, -Math.PI / 2, Math.PI / 2, false); // bright limb through +x
  // back from the bottom to the top along the terminator: bulging to -x when gibbous, to +x when crescent
  ctx.ellipse(0, 0, Math.max(1e-6, e), r, 0, Math.PI / 2, -Math.PI / 2, k < 0.5);
  ctx.closePath();
}

/** Screen-space angle (radians, canvas convention) from the Moon toward the Sun along the great circle. */
export function limbAngle(
  project: (v: Vec3) => [number, number, number] | null,
  moon: Vec3,
  sun: Vec3,
): number | null {
  const d = moon[0] * sun[0] + moon[1] * sun[1] + moon[2] * sun[2];
  const t: Vec3 = [sun[0] - d * moon[0], sun[1] - d * moon[1], sun[2] - d * moon[2]];
  const n = Math.hypot(t[0], t[1], t[2]);
  if (n < 1e-9) return null;
  const eps = 0.002;
  const q: Vec3 = [moon[0] + (eps * t[0]) / n, moon[1] + (eps * t[1]) / n, moon[2] + (eps * t[2]) / n];
  const a = project(moon);
  const c = project(q);
  if (!a || !c) return null;
  return Math.atan2(c[1] - a[1], c[0] - a[0]);
}
