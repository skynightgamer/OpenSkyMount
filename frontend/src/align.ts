// Star-alignment wizard helpers (pure): progress through the chosen stars, the simulator's centring offset and the
// point that should be redone. The server picks the stars (GET /api/align_stars) and fits the pointing model.

export interface AlignStar {
  id: string;
  name: string;
  magnitude: number;
  alt: number;
  az: number;
  constellation: string | null;
}

export interface AlignSuggestion {
  stars: AlignStar[];
  message: string | null;
  dark: boolean;
  sun_alt: number;
  min_separation_deg?: number;
  largest_azimuth_gap_deg?: number;
}

export interface ModelPoint {
  label: string;
  residual: number;
}

/** Index of the first star that has no alignment point yet (the star to do next), or -1 when all are done. */
export function nextStarIndex(stars: AlignStar[], points: Pick<ModelPoint, "label">[]): number {
  const done = new Set(points.map((p) => p.label));
  return stars.findIndex((s) => !done.has(s.name));
}

/** Arc minutes between the simulator's true pointing and the target, split into altitude and on-sky azimuth. */
export function centringOffset(truth: { alt: number; az: number } | null, target: { alt: number; az: number } | null): { alt: number; az: number; total: number } | null {
  if (!truth || !target) return null;
  const dAlt = (truth.alt - target.alt) * 60;
  const dAz = (((truth.az - target.az + 540) % 360) - 180) * Math.cos((target.alt * Math.PI) / 180) * 60;
  return { alt: dAlt, az: dAz, total: Math.hypot(dAlt, dAz) };
}

/**
 * The alignment point most likely to be a bad centring: the largest residual when it is clearly out of line with the
 * rest (more than 2.5 times the median and above 3 arc minutes). Needs three points so the fit has redundancy.
 */
export function suspectPoint(points: ModelPoint[]): number {
  if (points.length < 3) return -1;
  const res = points.map((p) => p.residual);
  const sorted = [...res].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const worst = res.indexOf(Math.max(...res));
  const limit = Math.max(2.5 * median, 3 / 60);
  return res[worst] > limit ? worst : -1;
}

/** "12″" below an arc minute, "3.4′" above. */
export function arcText(deg: number | null | undefined): string {
  if (deg == null || !Number.isFinite(deg)) return "—";
  return deg < 1 / 60 ? `${(deg * 3600).toFixed(0)}″` : `${(deg * 60).toFixed(1)}′`;
}
