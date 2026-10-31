// Sky-map geometry: horizon vectors, stereographic projection, Earth-rotation extrapolation.
// Horizon frame: x = North, y = East, z = Up (same as the backend).

export type Vec3 = [number, number, number];
export type Mat3 = number[][];

export const DEG = Math.PI / 180;
export const SIDEREAL_RATE_RAD_S = (2 * Math.PI) / 86164.0905;

export function radecToVec(raDeg: number, decDeg: number): Vec3 {
  const ra = raDeg * DEG;
  const dec = decDeg * DEG;
  return [Math.cos(dec) * Math.cos(ra), Math.cos(dec) * Math.sin(ra), Math.sin(dec)];
}

export function vecToRadec(v: Vec3): [number, number] {
  const n = Math.hypot(v[0], v[1], v[2]);
  const dec = Math.asin(Math.max(-1, Math.min(1, v[2] / n))) / DEG;
  const ra = ((Math.atan2(v[1], v[0]) / DEG) % 360 + 360) % 360;
  return [ra, dec];
}

export function altazToVec(altDeg: number, azDeg: number): Vec3 {
  const a = altDeg * DEG;
  const z = azDeg * DEG;
  return [Math.cos(a) * Math.cos(z), Math.cos(a) * Math.sin(z), Math.sin(a)];
}

export function vecToAltaz(v: Vec3): [number, number] {
  const n = Math.hypot(v[0], v[1], v[2]);
  const alt = Math.asin(Math.max(-1, Math.min(1, v[2] / n))) / DEG;
  const az = ((Math.atan2(v[1], v[0]) / DEG) % 360 + 360) % 360;
  return [alt, az];
}

export function mulMV(m: Mat3, v: Vec3): Vec3 {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ];
}

export function mulMtV(m: Mat3, v: Vec3): Vec3 {
  return [
    m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
    m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
    m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
  ];
}

/** ICRS->horizon matrix advanced by dt seconds of Earth rotation: M(t0+dt) = M(t0)·Rz(-θ). */
export function advanceMatrix(m: Mat3, dt: number): Mat3 {
  const th = SIDEREAL_RATE_RAD_S * dt;
  const c = Math.cos(th);
  const s = Math.sin(th);
  // Rz(-θ) = [[c, s, 0], [-s, c, 0], [0, 0, 1]]
  return m.map((row) => [row[0] * c - row[1] * s, row[0] * s + row[1] * c, row[2]]);
}

export interface View {
  az: number; // view centre azimuth (deg)
  alt: number; // view centre altitude (deg)
  fov: number; // field of view across the smaller canvas dimension (deg)
  width: number;
  height: number;
}

export interface Basis {
  f: Vec3;
  r: Vec3;
  u: Vec3;
  scale: number;
  cx: number;
  cy: number;
}

export function basis(view: View): Basis {
  const f = altazToVec(view.alt, view.az);
  const r: Vec3 = [-Math.sin(view.az * DEG), Math.cos(view.az * DEG), 0];
  const u: Vec3 = [f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]];
  const scale = Math.min(view.width, view.height) / 2 / (2 * Math.tan((view.fov * DEG) / 4));
  return { f, r, u, scale, cx: view.width / 2, cy: view.height / 2 };
}

/** Stereographic projection of a horizon unit vector. Returns [sx, sy, depth] or null if behind. */
export function project(b: Basis, v: Vec3, minDepth = -0.6): [number, number, number] | null {
  const z = v[0] * b.f[0] + v[1] * b.f[1] + v[2] * b.f[2];
  if (z < minDepth) return null;
  const x = v[0] * b.r[0] + v[1] * b.r[1] + v[2] * b.r[2];
  const y = v[0] * b.u[0] + v[1] * b.u[1] + v[2] * b.u[2];
  const k = 2 / (1 + z);
  return [b.cx + k * x * b.scale, b.cy - k * y * b.scale, z];
}

/** Inverse projection: screen point -> horizon unit vector. */
export function unproject(b: Basis, sx: number, sy: number): Vec3 {
  const X = (sx - b.cx) / b.scale;
  const Y = -(sy - b.cy) / b.scale;
  const rho = Math.hypot(X, Y);
  if (rho < 1e-12) return [...b.f] as Vec3;
  const c = 2 * Math.atan(rho / 2);
  const sc = Math.sin(c);
  const x = (X / rho) * sc;
  const y = (Y / rho) * sc;
  const z = Math.cos(c);
  return [
    x * b.r[0] + y * b.u[0] + z * b.f[0],
    x * b.r[1] + y * b.u[1] + z * b.f[1],
    x * b.r[2] + y * b.u[2] + z * b.f[2],
  ];
}

export function separationDeg(a: Vec3, b: Vec3): number {
  // atan2 form: accurate for tiny angles, unlike acos(dot).
  const cx = a[1] * b[2] - a[2] * b[1];
  const cy = a[2] * b[0] - a[0] * b[2];
  const cz = a[0] * b[1] - a[1] * b[0];
  return Math.atan2(Math.hypot(cx, cy, cz), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / DEG;
}

/** Approximate star colour from B-V index. */
export function bvColor(bv: number): string {
  if (bv < 0) return "#a9c6ff";
  if (bv < 0.3) return "#d6e2ff";
  if (bv < 0.6) return "#fbf8ff";
  if (bv < 1.0) return "#fff1d6";
  if (bv < 1.4) return "#ffd9a8";
  return "#ffbf86";
}

export function formatRA(deg: number): string {
  const h = (((deg % 360) + 360) % 360) / 15;
  const hh = Math.floor(h);
  const mm = Math.floor((h - hh) * 60);
  const ss = ((h - hh) * 60 - mm) * 60;
  return `${hh.toString().padStart(2, "0")}h ${mm.toString().padStart(2, "0")}m ${ss.toFixed(1).padStart(4, "0")}s`;
}

export function formatDMS(deg: number, signed = true): string {
  const sign = deg < 0 ? "−" : signed ? "+" : "";
  const a = Math.abs(deg);
  let d = Math.floor(a);
  let m = Math.floor((a - d) * 60);
  let s = Math.round(((a - d) * 60 - m) * 60);
  if (s === 60) {
    s = 0;
    m += 1;
  }
  if (m === 60) {
    m = 0;
    d += 1;
  }
  return `${sign}${d}° ${m.toString().padStart(2, "0")}′ ${s.toString().padStart(2, "0")}″`;
}
