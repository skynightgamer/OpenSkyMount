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

/** Degrees as [±]DD° MM′ SS″ with the degree field padded to `degDigits` (fixed width). */
export function fixedDMS(deg: number | null | undefined, degDigits = 2, signed = true): string {
  const width = degDigits + (signed ? 1 : 0) + 9;
  if (deg == null || !Number.isFinite(deg)) return "—".padStart(width, FIGURE_SPACE);
  const sign = deg < 0 ? MINUS : signed ? "+" : "";
  const totalS = Math.round(Math.abs(deg) * 3600);
  const d = Math.floor(totalS / 3600);
  const m = Math.floor((totalS % 3600) / 60);
  const s = totalS % 60;
  return `${(sign + d).padStart(degDigits + (signed ? 1 : 0), FIGURE_SPACE)}° ${String(m).padStart(2, "0")}′ ${String(s).padStart(2, "0")}″`;
}

// ------------------------------------------------------------------ display preferences (per device)
export type AngleFormat = "dms" | "decimal";
export interface DisplayPrefs {
  angles: AngleFormat;
  /** "device" (browser time zone), "UTC" or an IANA zone such as "Europe/Berlin". */
  timeZone: string;
}
export const DEFAULT_PREFS: DisplayPrefs = { angles: "dms", timeZone: "device" };

export function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function resolveZone(tz: string): string {
  return tz === "device" ? deviceTimeZone() : tz;
}

export function isValidZone(tz: string): boolean {
  if (tz === "device") return true;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** All IANA zones the browser knows (falls back to a short list on older browsers). */
export function timeZones(): string[] {
  const sv = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
  const list = sv ? sv("timeZone") : ["Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "Australia/Sydney"];
  return list.includes("UTC") ? list : ["UTC", ...list];
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function formatter(tz: string, opts: Intl.DateTimeFormatOptions, locale?: string): Intl.DateTimeFormat {
  const key = `${locale ?? ""}|${tz}|${JSON.stringify(opts)}`;
  let f = fmtCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { ...opts, timeZone: resolveZone(tz) });
    fmtCache.set(key, f);
  }
  return f;
}

/** Clock time (HH:MM, or HH:MM:SS) in the chosen zone. */
export function formatTime(unix: number | null | undefined, tz: string, seconds = false): string {
  if (unix == null || !Number.isFinite(unix)) return "—";
  return formatter(tz, { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }).format(unix * 1000);
}

export function formatDateTime(unix: number, tz: string): string {
  return formatter(tz, { dateStyle: "medium", timeStyle: "medium" }).format(unix * 1000);
}

/** Short zone label for display, e.g. "CET", "GMT+1" or "UTC". */
export function zoneLabel(unix: number, tz: string): string {
  const part = formatter(tz, { timeZoneName: "short" }, "en-US")
    .formatToParts(unix * 1000)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? resolveZone(tz);
}

function wallParts(unix: number, tz: string): number[] {
  const parts = formatter(tz, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }, "en-US").formatToParts(unix * 1000);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  return [get("year"), get("month"), get("day"), get("hour"), get("minute"), get("second")];
}

/** Value for <input type="datetime-local"> showing `unix` as wall time in `tz`. */
export function toZonedInput(unix: number, tz: string): string {
  const [y, mo, d, h, mi] = wallParts(unix, tz);
  const p2 = (n: number) => String(n).padStart(2, "0");
  return `${String(y).padStart(4, "0")}-${p2(mo)}-${p2(d)}T${p2(h)}:${p2(mi)}`;
}

/** Inverse of toZonedInput: wall time "YYYY-MM-DDTHH:MM" in `tz` to Unix seconds (NaN if malformed). */
export function fromZonedInput(value: string, tz: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!m) return NaN;
  const guess = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] ?? 0)) / 1000;
  const offset = (t: number) => {
    const [y, mo, d, h, mi, s] = wallParts(t, tz);
    return Date.UTC(y, mo - 1, d, h, mi, s) / 1000 - t;
  };
  let t = guess - offset(guess);
  t = guess - offset(t); // second pass settles times near a DST change
  return t;
}

/** Angle readouts in the chosen format. RA is shown in hours (sexagesimal) or degrees (decimal). */
export function angleFmt(prefs: DisplayPrefs) {
  const dms = prefs.angles === "dms";
  return {
    ra: (deg: number | null | undefined) => (deg == null ? "—" : dms ? formatRAh(deg) : `${fixedNum(((deg % 360) + 360) % 360, 3, 4)}°`),
    dec: (deg: number | null | undefined) => (dms ? fixedDMS(deg) : `${fixedNum(deg, 2, 4, true)}°`),
    alt: (deg: number | null | undefined) => (dms ? fixedDMS(deg) : `${fixedNum(deg, 2, 3, true)}°`),
    az: (deg: number | null | undefined) => (dms ? fixedDMS(deg, 3, false) : `${fixedNum(deg, 3, 3)}°`),
  };
}

function formatRAh(deg: number): string {
  const totalDs = Math.round(((((deg % 360) + 360) % 360) / 15) * 36000); // tenths of a second of time
  const h = Math.floor(totalDs / 36000) % 24;
  const m = Math.floor((totalDs % 36000) / 600);
  const s = (totalDs % 600) / 10;
  return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${s.toFixed(1).padStart(4, "0")}s`;
}
