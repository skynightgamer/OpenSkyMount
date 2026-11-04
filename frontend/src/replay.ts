// Telemetry recordings (state/logs/*.jsonl, see backend/openskymount/server/recorder.py): parsing and statistics.

export interface Sample {
  w: number; // wall time (s)
  t: number; // simulation time (s)
  m: string; // mode
  id: string | null; // target id
  ta: number | null; // target alt / az (deg)
  tz: number | null;
  a: number; // estimated pointing alt / az
  z: number;
  xa: number; // raw axes
  xz: number;
  ra: number; // axis rates (deg/s)
  rz: number;
  e: number | null; // mount-reported pointing error (deg)
  te: number | null; // simulator ground-truth error (deg)
  f: string[];
}

export interface LogEvent {
  t: number;
  level: string;
  msg: string;
}

export interface Recording {
  header: Record<string, unknown>;
  samples: Sample[];
  events: LogEvent[];
  skipped: number; // unreadable lines
}

export function parseRecording(text: string): Recording {
  const rec: Recording = { header: {}, samples: [], events: [], skipped: 0 };
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let o: Record<string, unknown>;
    try {
      o = JSON.parse(line);
    } catch {
      rec.skipped++; // e.g. a truncated last line of a file that is still being written
      continue;
    }
    if (o.k === "h") rec.header = o;
    else if (o.k === "s" && typeof o.t === "number") rec.samples.push(o as unknown as Sample);
    else if (o.k === "e" && typeof o.msg === "string") rec.events.push(o as unknown as LogEvent);
    else rec.skipped++;
  }
  rec.samples.sort((a, b) => a.t - b.t);
  return rec;
}

/** Index of the last sample at or before simulation time ``t`` (0 if before the first). */
export function sampleIndexAt(samples: Sample[], t: number): number {
  let lo = 0;
  let hi = samples.length - 1;
  if (hi < 0) return -1;
  if (t <= samples[0].t) return 0;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (samples[mid].t <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export interface TrackStats {
  id: string;
  samples: number;
  seconds: number; // simulation time spent tracking
  rms: number; // mount-reported error, arcsec
  p95: number;
  max: number;
  trueRms: number | null; // simulator ground truth, arcsec
  trueMax: number | null;
  maxAxisRate: number; // deg/s
}

function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return NaN;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[i];
}

/** Tracking-error statistics per target, from samples in "tracking" mode only (slews would dominate). */
export function trackingStats(samples: Sample[]): TrackStats[] {
  const groups = new Map<string, Sample[]>();
  for (const s of samples) {
    if (s.m !== "tracking" || !s.id || s.e == null) continue;
    const g = groups.get(s.id) ?? [];
    g.push(s);
    groups.set(s.id, g);
  }
  const out: TrackStats[] = [];
  for (const [id, g] of groups) {
    const err = g.map((s) => (s.e as number) * 3600).sort((a, b) => a - b);
    const tru = g.filter((s) => s.te != null).map((s) => (s.te as number) * 3600);
    let seconds = 0;
    for (let i = 1; i < g.length; i++) {
      const dt = g[i].t - g[i - 1].t;
      if (dt > 0 && dt < 60) seconds += dt; // gaps (other targets, paused) are not counted
    }
    const rms = (xs: number[]) => Math.sqrt(xs.reduce((a, x) => a + x * x, 0) / xs.length);
    out.push({
      id,
      samples: g.length,
      seconds,
      rms: rms(err),
      p95: percentile(err, 95),
      max: err[err.length - 1],
      trueRms: tru.length ? rms(tru) : null,
      trueMax: tru.length ? Math.max(...tru) : null,
      maxAxisRate: Math.max(...g.map((s) => Math.max(Math.abs(s.ra), Math.abs(s.rz)))),
    });
  }
  return out.sort((a, b) => b.seconds - a.seconds);
}

/** Recording as CSV (one row per sample) for spreadsheets / plotting tools. */
export function toCsv(samples: Sample[]): string {
  const cols: (keyof Sample)[] = ["w", "t", "m", "id", "ta", "tz", "a", "z", "xa", "xz", "ra", "rz", "e", "te", "f"];
  const head = "wall,sim_time,mode,target,target_alt,target_az,alt,az,axis_alt,axis_az,rate_alt,rate_az,error_deg,true_error_deg,flags";
  const cell = (v: unknown) => (v == null ? "" : Array.isArray(v) ? `"${v.join(" ")}"` : typeof v === "string" ? `"${v.replace(/"/g, '""')}"` : String(v));
  return [head, ...samples.map((s) => cols.map((c) => cell(s[c])).join(","))].join("\n") + "\n";
}
