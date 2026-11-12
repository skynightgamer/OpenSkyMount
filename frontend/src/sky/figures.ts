// Constellation figures: the painted mythological characters and creatures that atlases and planetarium programs
// draw over the stick patterns. The pictures come from the Stellarium project (Free Art License, see
// public/figures/NOTICE.md and D50); each one carries three anchor stars (a pixel position and a Hipparcos number),
// (baked with the star's position by scripts/build_figures.py), which is all that is needed to pin it to the sky: an affine map from picture pixels to the flat "tangent plane"
// around the figure, then the usual sky projection per mesh vertex, so the art follows the real star positions.
import data from "./figures.json";
import { DEG, type Vec3, radecToVec } from "./projection";

export interface FigureInfo {
  w: number;
  h: number;
  /** [pixel x, pixel y, Hipparcos number, RA, Dec (J2000, degrees)] ×3 */
  anchors: [number, number, number, number, number][];
  english: string;
}

export const FIGURE_INFO = data as unknown as Record<string, FigureInfo>;

/** Mesh resolution of the stored lattice; render strides must divide it. */
export const MESH = 12;

// -------------------------------------------------------------- tangent plane <-> sky
/** Gnomonic projection of (ra, dec) about (ra0, dec0): east/north offsets in degrees. */
export function skyToTangent(ra0: number, dec0: number, ra: number, dec: number): [number, number] {
  const a = (ra - ra0) * DEG;
  const d = dec * DEG;
  const d0 = dec0 * DEG;
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a);
  const x = (Math.cos(d) * Math.sin(a)) / cosc;
  const y = (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a)) / cosc;
  return [x / DEG, y / DEG];
}

/** Inverse of {@link skyToTangent}: tangent-plane degrees to a J2000 unit vector. */
export function tangentToVec(ra0: number, dec0: number, x: number, y: number): Vec3 {
  const X = x * DEG;
  const Y = y * DEG;
  const rho = Math.hypot(X, Y);
  if (rho < 1e-15) return radecToVec(ra0, dec0);
  const c = Math.atan(rho);
  const d0 = dec0 * DEG;
  const dec = Math.asin(Math.cos(c) * Math.sin(d0) + (Y * Math.sin(c) * Math.cos(d0)) / rho);
  const ra = ra0 * DEG + Math.atan2(X * Math.sin(c), rho * Math.cos(d0) * Math.cos(c) - Y * Math.sin(d0) * Math.sin(c));
  return radecToVec(ra / DEG, dec / DEG);
}

// -------------------------------------------------------------- anchoring
/** Solve [px py 1]·[a b c]ᵀ = v for the three anchors (Cramer's rule); null if they are collinear. */
function solve3(p: [number, number][], v: number[]): [number, number, number] | null {
  const det = (m: number[][]) =>
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const A = p.map(([x, y]) => [x, y, 1]);
  const d = det(A);
  if (Math.abs(d) < 1e-9) return null;
  const col = (k: number) => det(A.map((row, i) => row.map((val, j) => (j === k ? v[i] : val))));
  return [col(0) / d, col(1) / d, col(2) / d];
}

/** A figure's picture-to-sky fit: tangent plane about (ra0, dec0), tangent = [a b c; d e f]·[px py 1]. */
export interface FigureFit {
  ra0: number;
  dec0: number;
  ax: [number, number, number];
  ay: [number, number, number];
}

/** Fit the picture to the sky from its three anchor stars; null when they are collinear. */
export function fitFigure(info: FigureInfo): FigureFit | null {
  const sky: [number, number][] = info.anchors.map((a) => [a[3], a[4]]);
  // Tangent plane about the mean direction of the anchors.
  const mean: Vec3 = [0, 0, 0];
  for (const [ra, dec] of sky) {
    const v = radecToVec(ra, dec);
    mean[0] += v[0];
    mean[1] += v[1];
    mean[2] += v[2];
  }
  const n = Math.hypot(...mean);
  const ra0 = ((Math.atan2(mean[1], mean[0]) / DEG) % 360 + 360) % 360;
  const dec0 = Math.asin(mean[2] / n) / DEG;
  const tan = sky.map(([ra, dec]) => skyToTangent(ra0, dec0, ra, dec));
  const pix = info.anchors.map((a) => [a[0], a[1]] as [number, number]);
  const ax = solve3(pix, tan.map((t) => t[0]));
  const ay = solve3(pix, tan.map((t) => t[1]));
  return ax && ay ? { ra0, dec0, ax, ay } : null;
}

/** Sky position (J2000 unit vector) of a picture pixel. */
export function pixelToVec(fit: FigureFit, px: number, py: number): Vec3 {
  return tangentToVec(fit.ra0, fit.dec0, fit.ax[0] * px + fit.ax[1] * py + fit.ax[2], fit.ay[0] * px + fit.ay[1] * py + fit.ay[2]);
}

export interface FigureMesh {
  id: string;
  /** Unit vector (J2000) at the middle of the picture. */
  centre: Vec3;
  /** Largest angular distance from the centre to a lattice vertex, degrees. */
  radiusDeg: number;
  /** (MESH+1)² sky vectors, row by row (y outer, x inner), picture pixel (i·w/MESH, j·h/MESH). */
  grid: Vec3[];
  /** +1 when the picture's pixel axes map to (east, north) with positive determinant, -1 when mirrored. */
  handedness: 1 | -1;
}

/** Pin a figure's picture to the sky. Returns null for an unknown id or collinear anchors. */
export function buildFigureMesh(id: string, info: FigureInfo = FIGURE_INFO[id]): FigureMesh | null {
  if (!info) return null;
  const fit = fitFigure(info);
  if (!fit) return null;
  const grid: Vec3[] = [];
  for (let j = 0; j <= MESH; j++) for (let i = 0; i <= MESH; i++) grid.push(pixelToVec(fit, (i * info.w) / MESH, (j * info.h) / MESH));
  const centre = grid[(MESH / 2) * (MESH + 1) + MESH / 2];
  let radius = 0;
  for (const v of grid) radius = Math.max(radius, angleDeg(centre, v));
  const det = fit.ax[0] * fit.ay[1] - fit.ax[1] * fit.ay[0];
  return { id, centre, radiusDeg: radius, grid, handedness: det > 0 ? 1 : -1 };
}

// -------------------------------------------------------------- focus and visibility
/**
 * How visible the artwork is for a sky of the given Sun altitude (degrees): hidden in daylight, fading in through
 * civil and nautical twilight, full at astronomical night.
 */
export function figureNightFactor(sunAlt: number): number {
  const t = (-sunAlt - 4) / 10;
  return Math.max(0, Math.min(1, t));
}

/** Angular distance (deg) between two unit vectors. */
export function angleDeg(a: Vec3, b: Vec3): number {
  return Math.atan2(Math.hypot(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / DEG;
}
