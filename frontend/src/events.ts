// Sky events (GET /api/events): the record shape and the small pure helpers the Events panel uses.

export interface SkyEvent {
  id: string;
  kind: string;
  time: number;
  title: string;
  detail: string;
  target: string | null;
  importance: number;
  alt: number | null;
  sun_alt: number | null;
  visible: boolean | null;
  safety?: string;
}

export const DAY_CHOICES = [30, 60, 120] as const;

/** "in 3 d", "in 5 h", "now" for an event at `unix` seen from `now` (both Unix seconds). */
export function untilText(unix: number, now: number): string {
  const s = unix - now;
  if (s < -3600) return "past";
  if (s < 3600) return s < 60 ? "now" : `in ${Math.round(s / 60)} min`;
  if (s < 48 * 3600) return `in ${Math.round(s / 3600)} h`;
  return `in ${Math.round(s / 86400)} d`;
}

/** Start and length (s) used when an event is added to the observing session. */
export function sessionWindow(e: SkyEvent): { start_at: number; duration_s: number } {
  return { start_at: e.time - 300, duration_s: e.kind === "meteor_shower" ? 3600 : 1800 };
}

