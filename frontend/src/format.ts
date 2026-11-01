// Fixed-width number formatting for live readouts. With tabular figures every digit and U+2007 FIGURE SPACE
// have the same advance, so a value padded to a constant number of characters keeps a constant width and
// the readout does not jitter or push neighbouring elements around while it updates.
export const FIGURE_SPACE = " ";
const MINUS = "−";

/** `v` with `decimals` places, left-padded to `intDigits` integer digits (plus a sign column when `signed`). */
export function fixedNum(v: number | null | undefined, intDigits: number, decimals: number, signed = false): string {
  const width = intDigits + (decimals > 0 ? decimals + 1 : 0) + (signed ? 1 : 0);
  if (v == null || !Number.isFinite(v)) return "—".padStart(width, FIGURE_SPACE);
  let body = Math.abs(v).toFixed(decimals);
  if (Number(body) === 0) v = 0; // no "−0.000"
  const sign = v < 0 ? MINUS : signed ? "+" : "";
  body = sign + body;
  return body.padStart(width, FIGURE_SPACE);
}

/** Angle in degrees shown as ″, ′ or ° depending on size; always 7 characters wide. */
export function fixedAngle(deg: number | null | undefined): string {
  let s: string;
  if (deg == null || !Number.isFinite(deg)) s = "—";
  else if (deg < 1 / 60) s = `${(deg * 3600).toFixed(1)}″`;
  else if (deg < 1) s = `${(deg * 60).toFixed(2)}′`;
  else s = `${deg.toFixed(2)}°`;
  return s.padStart(7, FIGURE_SPACE);
}

/** Signed degrees as ±DD° MM′ SS″ with the degree field padded to two digits (fixed width). */
export function fixedDMS(deg: number | null | undefined): string {
  if (deg == null || !Number.isFinite(deg)) return "—".padStart(12, FIGURE_SPACE);
  const sign = deg < 0 ? MINUS : "+";
  const totalS = Math.round(Math.abs(deg) * 3600);
  const d = Math.floor(totalS / 3600);
  const m = Math.floor((totalS % 3600) / 60);
  const s = totalS % 60;
  return `${(sign + d).padStart(3, FIGURE_SPACE)}° ${String(m).padStart(2, "0")}′ ${String(s).padStart(2, "0")}″`;
}
