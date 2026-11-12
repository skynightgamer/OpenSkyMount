// Sky-map scenery and camera motion. Everything here is cosmetic except the ecliptic, which is geometry:
// the decorative skyline never replaces the true horizon (altitude 0), which the map keeps drawing as a line.
import { DEG, type Vec3 } from "./projection";

// ------------------------------------------------------------------ ecliptic
/** Mean obliquity of the ecliptic at J2000.0 (IAU 2006: 84381.406″). */
export const OBLIQUITY_J2000 = 84381.406 / 3600;

/** J2000 equatorial unit vector of the point at ecliptic longitude `lonDeg`, latitude 0. */
export function eclipticVec(lonDeg: number): Vec3 {
  const l = lonDeg * DEG;
  const e = OBLIQUITY_J2000 * DEG;
  return [Math.cos(l), Math.cos(e) * Math.sin(l), Math.sin(e) * Math.sin(l)];
}

// ------------------------------------------------------------------ deterministic noise
/** Hash of an integer to 0..1 (stable across frames and sessions). */
export function hash01(n: number): number {
  let x = Math.imul((n | 0) ^ 0x9e3779b9, 0x85ebca6b);
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35);
  x ^= x >>> 16;
  return (x >>> 0) / 4294967296;
}

function smooth(a: number, b: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

// ------------------------------------------------------------------ decorative skyline
/** Number of tree crowns around the horizon (an integer, so the skyline is seamless at azimuth 0/360). */
const TREES = 520;

/** Distant ridge height (degrees above the true horizon) at azimuth `azDeg`. Periodic in 360°. */
export function ridgeAlt(azDeg: number): number {
  const a = azDeg * DEG;
  const h = 0.75 + 0.55 * Math.sin(2 * a + 1.3) + 0.32 * Math.sin(5 * a + 0.4) + 0.16 * Math.sin(11 * a + 2.2) + 0.07 * Math.sin(23 * a + 0.9);
  return Math.max(0.15, 1.25 * h);
}

/** Near skyline (low hills and patches of forest) in degrees above the true horizon. Periodic in 360°. */
export function landscapeAlt(azDeg: number): number {
  const az = ((azDeg % 360) + 360) % 360;
  const a = az * DEG;
  const hills = 0.35 + 0.22 * Math.sin(3 * a + 2.1) + 0.12 * Math.sin(7 * a + 0.3) + 0.05 * Math.sin(17 * a + 1.1);
  // Forest where a slow wave is high; single trees elsewhere are rare.
  const forest = smooth(0.35, 0.65, 0.5 + 0.5 * Math.sin(4 * a + 0.8) * Math.cos(a - 0.6));
  const f = (az / 360) * TREES;
  const k = Math.floor(f) % TREES;
  const x = f - Math.floor(f);
  const pick = hash01(k);
  const present = forest > 0.02 ? forest : pick > 0.985 ? 0.6 : 0;
  let tree = 0;
  if (present > 0) {
    const height = (0.45 + 0.75 * hash01(k + 7919)) * present;
    // Conifers are pointed, broadleaf crowns round.
    const crown = pick < 0.45 ? 1 - Math.abs(2 * x - 1) : Math.sqrt(Math.max(0, 1 - (2 * x - 1) ** 2));
    tree = height * crown;
  }
  return Math.max(0.1, 1.35 * (hills + tree));
}

// ------------------------------------------------------------------ twinkling
/**
 * Brightness factor for scintillation (about 0.7..1.3). Stronger near the horizon, where starlight crosses more air.
 * `i` identifies the star, `t` is wall-clock seconds.
 */
export function twinkle(i: number, t: number, alt: number): number {
  const amp = 0.05 + 0.22 * (1 - smooth(4, 55, alt));
  const ph = hash01(i) * 6.2832;
  const f1 = 5 + 6 * hash01(i + 101);
  const f2 = 9 + 8 * hash01(i + 202);
  return 1 + amp * (0.62 * Math.sin(t * f1 + ph) + 0.38 * Math.sin(t * f2 + 2.3 * ph));
}

// ------------------------------------------------------------------ camera flights
export interface ViewPose {
  az: number;
  alt: number;
  fov: number;
}

export interface Flight {
  from: ViewPose;
  to: ViewPose;
  t0: number; // ms (performance.now)
  dur: number; // ms
  bump: number; // extra field of view at mid-flight (deg): zoom out a little on long flights
}

export function wrap360(a: number): number {
  return ((a % 360) + 360) % 360;
}

/** Signed azimuth change from `from` to `to` the short way round, in (-180, 180]. */
export function shortestDelta(from: number, to: number): number {
  const d = wrap360(to - from);
  return d > 180 ? d - 360 : d;
}

export function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

/** Angle between two alt/az directions (deg). */
export function separation(a: { alt: number; az: number }, b: { alt: number; az: number }): number {
  const c =
    Math.sin(a.alt * DEG) * Math.sin(b.alt * DEG) + Math.cos(a.alt * DEG) * Math.cos(b.alt * DEG) * Math.cos((a.az - b.az) * DEG);
  return Math.acos(Math.max(-1, Math.min(1, c))) / DEG;
}

/** A smooth camera move; duration grows with distance. `reduced`: jump at once (prefers-reduced-motion). */
export function makeFlight(from: ViewPose, to: ViewPose, now: number, reduced = false): Flight {
  const sep = separation(from, to);
  const dur = reduced ? 0 : Math.min(1500, 380 + 7 * sep + 160 * Math.abs(Math.log2(to.fov / from.fov)));
  const bump = Math.max(0, Math.min(150, sep * 0.9) - Math.max(from.fov, to.fov)) * 0.6;
  return { from, to, t0: now, dur, bump };
}

/** Camera pose at time `now` (ms); `done` once the flight is over. */
export function flightAt(f: Flight, now: number): ViewPose & { done: boolean } {
  const t = f.dur <= 0 ? 1 : Math.max(0, Math.min(1, (now - f.t0) / f.dur));
  const e = easeInOutCubic(t);
  return {
    az: wrap360(f.from.az + shortestDelta(f.from.az, f.to.az) * e),
    alt: f.from.alt + (f.to.alt - f.from.alt) * e,
    // Field of view interpolated in log space (zooming feels even), plus the mid-flight bump.
    fov: f.from.fov * (f.to.fov / f.from.fov) ** e + f.bump * Math.sin(Math.PI * e),
    done: t >= 1,
  };
}

// ------------------------------------------------------------------ labels
const POINTS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
/** Compass point (16-wind) for an azimuth. */
export function compassPoint(az: number): string {
  return POINTS16[Math.round(wrap360(az) / 22.5) % 16];
}

/** Mix two #rrggbb colours (t = 0: a, 1: b) into a #rrggbb colour. */
export function mixHex(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const ch = (s: number) => Math.round(((pa >> s) & 255) * (1 - t) + ((pb >> s) & 255) * t);
  return "#" + ((ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).padStart(6, "0");
}

/** Skyline colours (far ridge, near ground) for the Sun's altitude: hazy blue by day, black at night. */
export function sceneryColors(sunAlt: number): { far: string; near: string } {
  const day = smooth(-6, 8, sunAlt);
  const tw = smooth(-16, -4, sunAlt);
  return {
    far: mixHex(mixHex("#080c12", "#151d29", tw), "#6f86a3", day),
    near: mixHex(mixHex("#020305", "#07090d", tw), "#1b271d", day),
  };
}
