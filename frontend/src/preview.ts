// Slew preview (GET /api/slew_preview): the response shape and the pure helpers behind the Target panel's preview.

export interface Cable {
  az_min: number;
  az_max: number;
  span: number;
  now: number;
  after: number;
  margin_now: number;
  margin_after: number;
  long_way: boolean;
  tracking_limit_s: number | null;
}

export interface SlewPlan {
  target: { id: string; name: string };
  refused: string | null;
  warnings: string[];
  duration_s?: number;
  separation_deg?: number;
  delta?: { alt: number; az: number };
  turn?: { alt: "up" | "down" | "none"; az: string };
  /** Sky (alt, az) points of the pointing direction while both axes move. */
  path?: [number, number][];
  cable?: Cable;
  estimate?: string;
}

/** Cable-wrap margin (deg) below which the bar paints the end zones: the same value the server warns about. */
export const NEAR_LIMIT_DEG = 40;

/** "38 s", "2 min 10 s", "1 h 05 min". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

export interface CableBar {
  /** Percent of the bar width (0 = azimuth minimum limit, 100 = maximum limit). */
  now: number;
  after: number;
  neutral: number;
  zone: number;
}

export function cableBar(c: Pick<Cable, "az_min" | "az_max" | "now" | "after">): CableBar {
  const span = c.az_max - c.az_min;
  const pct = (v: number) => Math.max(0, Math.min(100, ((v - c.az_min) / span) * 100));
  return { now: pct(c.now), after: pct(c.after), neutral: pct(0), zone: (NEAR_LIMIT_DEG / span) * 100 };
}

/** Signed degrees with a real minus sign, e.g. "+120°" / "−12°". */
export function signedDeg(v: number): string {
  const r = Math.round(v);
  return `${r < 0 ? "−" : r > 0 ? "+" : ""}${Math.abs(r)}°`;
}
