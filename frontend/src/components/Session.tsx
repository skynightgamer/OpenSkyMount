// Observing session queue, satellite passes, user-supplied orbital elements, telemetry recordings / replay,
// and the phone "hand controller" layout.
import { useEffect, useMemo, useRef, useState } from "react";
import { fixedAngle, formatDateTime, formatTime, fromZonedInput, toZonedInput } from "../format";
import { type Recording, parseRecording, sampleIndexAt, toCsv, trackingStats } from "../replay";
import { MANUAL_SPEEDS, type QueueItem, command, getState, isController, requestControl, setState, simNow, toast, useStore } from "../store";
import { ManualPad } from "./Panels";

const EMPTY: never[] = [];

function duration(s: number | null): string {
  if (s == null) return "until next";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

const STATUS_LABEL: Record<QueueItem["status"], string> = {
  pending: "pending",
  waiting: "waiting",
  slewing: "slewing",
  tracking: "tracking",
  done: "done",
  skipped: "skipped",
  interrupted: "interrupted",
};

// ------------------------------------------------------------------ session queue
export function SessionPanel() {
  const session = useStore((s) => s.session);
  const ctl = useStore((s) => isController(s));
  const tz = useStore((s) => s.prefs.timeZone);
  const selected = useStore((s) => s.selectedId);
  const recording = useStore((s) => s.recording?.active ?? false);
  const [minutes, setMinutes] = useState(10);
  const [untilNext, setUntilNext] = useState(false);
  const [start, setStart] = useState("");
  const [record, setRecord] = useState(true);
  const items = session?.items ?? EMPTY;
  const open = items.filter((i) => !["done", "skipped"].includes(i.status)).length;
  const add = () => {
    if (!selected) return;
    const startAt = start ? fromZonedInput(start, tz) : null;
    command("queue_add", { target: selected, duration_s: untilNext ? null : minutes * 60, start_at: startAt != null && Number.isFinite(startAt) ? startAt : null }).then(
      (a) => a.ok && toast(String(a.message)),
    );
  };
  return (
    <section className="panel session">
      <h3 className="label">
        Observing session{" "}
        <span className={`badge ${session?.running ? "on" : ""}`}>{session?.running ? "running" : items.length ? "stopped" : "empty"}</span>
      </h3>
      {items.length === 0 ? (
        <p className="hint">Add targets with “+ Session” in the target panel, or below. The mount then works through the list: GoTo, track for the set time, next.</p>
      ) : (
        <ol className="queue">
          {items.map((it, i) => (
            <li key={it.id} className={`q-${it.status}${session?.current === it.id ? " current" : ""}`}>
              <div className="q-main">
                <button className="link" onClick={() => setState({ selectedId: it.target })} title="Select on the sky map">
                  {it.name}
                </button>
                <small>
                  {it.start_at != null ? `from ${formatTime(it.start_at, tz)} · ` : ""}
                  {duration(it.duration_s)}
                  {session?.current === it.id && session.remaining_s != null ? ` · ${duration(session.remaining_s)} left` : ""}
                  {it.message ? ` · ${it.message}` : ""}
                </small>
              </div>
              <span className={`q-status`}>{STATUS_LABEL[it.status]}</span>
              <span className="q-tools">
                <button disabled={!ctl || i === 0} onClick={() => command("queue_move", { id: it.id, delta: -1 })} aria-label={`Move ${it.name} up`}>
                  ↑
                </button>
                <button disabled={!ctl || session?.current === it.id} onClick={() => command("queue_remove", { id: it.id })} aria-label={`Remove ${it.name}`}>
                  ×
                </button>
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="row wrap">
        {session?.running ? (
          <>
            <button disabled={!ctl} onClick={() => command("queue_pause")} title="Stop working through the list (the mount keeps tracking)">
              Pause
            </button>
            <button disabled={!ctl || !session.current} onClick={() => command("queue_skip")}>
              Skip
            </button>
          </>
        ) : (
          <button className="primary" disabled={!ctl || open === 0} onClick={() => command("queue_start", { record: record && !recording })}>
            Start session
          </button>
        )}
        <button disabled={!ctl || session?.running || items.length === 0} onClick={() => command("queue_reset")} title="Mark all items pending again">
          Run again
        </button>
        <button disabled={!ctl || items.every((i) => i.status !== "done" && i.status !== "skipped")} onClick={() => command("queue_clear", { finished_only: true })}>
          Clear finished
        </button>
        <button disabled={!ctl || session?.running || items.length === 0} onClick={() => confirm("Remove all targets from the session?") && command("queue_clear")}>
          Clear all
        </button>
      </div>
      {!session?.running && (
        <label className="inline-check">
          <input type="checkbox" checked={record} onChange={(e) => setRecord(e.target.checked)} /> Record telemetry while the session runs
        </label>
      )}
      <details className="sub-details">
        <summary>Add the selected target with options</summary>
        <div className="q-add">
          <label>
            <span>Track for (min)</span>
            <input type="number" min={0} step={1} value={minutes} disabled={untilNext} onChange={(e) => setMinutes(Math.max(0, Number(e.target.value)))} />
          </label>
          <label className="inline-check">
            <input type="checkbox" checked={untilNext} onChange={(e) => setUntilNext(e.target.checked)} /> until the next item's start time
          </label>
          <label>
            <span>Start at (optional)</span>
            <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Start time (optional)" />
          </label>
          <div className="row">
            <button onClick={() => setStart(toZonedInput(simNow(), tz))}>Now</button>
            <button onClick={() => setStart("")}>No start time</button>
            <button className="primary" disabled={!ctl || !selected} onClick={add}>
              Add {selected ? "selected" : "(select a target)"}
            </button>
          </div>
        </div>
      </details>
      <p className="hint">Any other motion command (GoTo, Stop, E-STOP, manual slew) pauses the session; it never restarts by itself.</p>
    </section>
  );
}

// ------------------------------------------------------------------ satellite passes
interface Pass {
  rise: number;
  culminate: number;
  set: number;
  max_alt: number;
  culminate_az: number;
  sunlit: boolean;
  sun_alt: number;
}

export function SatellitePasses({ id }: { id: string }) {
  const tz = useStore((s) => s.prefs.timeZone);
  const epoch = useStore((s) => s.clock?.epoch);
  const ctl = useStore((s) => isController(s));
  const minAlt = useStore((s) => Number(s.config?.config.control.min_target_altitude ?? 0));
  const [passes, setPasses] = useState<Pass[] | null>(null);
  useEffect(() => {
    setPasses(null);
    const ctl = new AbortController();
    fetch(`/api/passes?id=${encodeURIComponent(id)}&t=${simNow()}&hours=48&min_alt=${Math.max(10, minAlt)}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : { passes: [] }))
      .then((d) => setPasses(d.passes))
      .catch(() => {});
    return () => ctl.abort();
  }, [id, epoch, minAlt]);
  return (
    <div className="passes">
      <h4>Passes in the next 48 h (above {Math.max(10, minAlt)}°)</h4>
      {passes == null ? (
        <p className="hint">Computing…</p>
      ) : passes.length === 0 ? (
        <p className="hint">No passes.</p>
      ) : (
        <ul>
          {passes.slice(0, 8).map((p) => (
            <li key={p.rise}>
              <span className="num">
                {toZonedInput(p.rise, tz).slice(5).replace("T", " ")} – {formatTime(p.set, tz)}
              </span>
              <span className="num">max {p.max_alt.toFixed(0)}°</span>
              <span className={p.sunlit && p.sun_alt < -6 ? "vis" : "dim"} title="Visible to the eye only when sunlit while the sky is dark">
                {p.sunlit ? (p.sun_alt < -6 ? "visible" : "daylight") : "shadow"}
              </span>
              <button
                disabled={!ctl}
                onClick={() =>
                  command("queue_add", { target: id, start_at: p.rise, duration_s: p.set - p.rise + 30 }).then((a) => a.ok && toast("Pass added to the session"))
                }
              >
                + Session
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ orbital elements
interface OrbitObj {
  id: string;
  name: string;
  kind: string;
  age_days?: number;
}

export function OrbitsPanel() {
  const rev = useStore((s) => s.orbitsRevision);
  const [objs, setObjs] = useState<OrbitObj[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    fetch("/api/orbits")
      .then((r) => (r.ok ? r.json() : { objects: [] }))
      .then((d) => setObjs(d.objects))
      .catch(() => {});
  }, [rev]);
  const upload = async (body: string) => {
    setBusy(true);
    try {
      const r = await fetch("/api/orbits", { method: "POST", headers: { "Content-Type": "text/plain" }, body });
      const d = await r.json();
      if (!r.ok) toast(d.detail ?? "could not read the elements", "error");
      else {
        toast(`${d.added.length} object(s) loaded`);
        setText("");
        setState({ orbitsRevision: getState().orbitsRevision + 1 });
      }
    } catch {
      toast("server not reachable", "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id?: string) => {
    const r = await fetch(`/api/orbits${id ? `?id=${encodeURIComponent(id)}` : ""}`, { method: "DELETE" });
    if (!r.ok) toast((await r.json()).detail ?? "failed", "error");
    else setState({ orbitsRevision: getState().orbitsRevision + 1 });
  };
  return (
    <details className="panel orbits">
      <summary className="label">Satellites, comets &amp; asteroids ({objs.length})</summary>
      <p className="hint">
        Paste element sets: TLEs for satellites (e.g. CelesTrak “stations”), or MPC one-line orbits (CometEls.txt, MPCORB.DAT lines). Nothing is downloaded by
        OpenSkyMount; elements are stored on the server. Comets/asteroids are two-body orbits; TLEs go stale within days to weeks.
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder={"ISS (ZARYA)\n1 25544U 98067A   …\n2 25544  51.64…"} aria-label="Orbital elements" spellCheck={false} />
      <div className="row">
        <button className="primary" disabled={busy || !text.trim()} onClick={() => upload(text)}>
          Load
        </button>
        <label className="filebtn">
          File…
          <input type="file" accept=".txt,.tle,.dat,text/plain" onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) await upload(await f.text());
            e.target.value = "";
          }} />
        </label>
        <button disabled={!objs.length} onClick={() => confirm("Remove all loaded objects?") && remove()}>
          Remove all
        </button>
      </div>
      {objs.length > 0 && (
        <ul className="orbit-list">
          {objs.slice(0, 200).map((o) => (
            <li key={o.id}>
              <button className="link" onClick={() => setState({ selectedId: o.id })}>
                {o.name}
              </button>
              <small>
                {o.kind}
                {o.age_days != null ? ` · ${o.age_days.toFixed(1)} d` : ""}
              </small>
              <button onClick={() => remove(o.id)} aria-label={`Remove ${o.name}`}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

// ------------------------------------------------------------------ telemetry logs
interface LogFile {
  name: string;
  bytes: number;
  modified: number;
  recording: boolean;
}

export function LogsPanel() {
  const rec = useStore((s) => s.recording);
  const ctl = useStore((s) => isController(s));
  const [logs, setLogs] = useState<LogFile[]>([]);
  const [replay, setReplay] = useState<{ name: string; rec: Recording } | null>(null);
  const [openState, setOpen] = useState(false);
  const refresh = () =>
    fetch("/api/logs")
      .then((r) => (r.ok ? r.json() : { logs: [] }))
      .then((d) => setLogs(d.logs))
      .catch(() => {});
  useEffect(() => {
    if (openState) refresh();
  }, [openState, rec?.active, rec?.name]);
  const load = async (name: string) => {
    const r = await fetch(`/api/logs/${encodeURIComponent(name)}`);
    if (!r.ok) return toast("could not load the log", "error");
    setReplay({ name, rec: parseRecording(await r.text()) });
  };
  return (
    <details className="panel logs" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="label">Telemetry recordings{rec?.active ? " · recording" : ""}</summary>
      <div className="row">
        {rec?.active ? (
          <button className="warnbtn" disabled={!ctl} onClick={() => command("record_stop")}>
            Stop recording ({rec.samples} samples)
          </button>
        ) : (
          <button disabled={!ctl} onClick={() => command("record_start", { label: "" })}>
            Start recording
          </button>
        )}
        <button onClick={refresh}>Refresh</button>
      </div>
      <p className="hint">5 samples per second of pointing, target, axis rates and errors, plus the event log. Files are kept in the server's state/logs folder.</p>
      <ul className="log-list">
        {logs.map((l) => (
          <li key={l.name}>
            <span title={l.name}>{l.name.replace(/\.jsonl$/, "")}</span>
            <small>{(l.bytes / 1024).toFixed(0)} kB</small>
            <button onClick={() => load(l.name)}>Replay</button>
            <a href={`/api/logs/${encodeURIComponent(l.name)}`} download>
              Download
            </a>
            <button
              disabled={l.recording}
              onClick={async () => {
                if (!confirm(`Delete ${l.name}?`)) return;
                await fetch(`/api/logs/${encodeURIComponent(l.name)}`, { method: "DELETE" });
                refresh();
              }}
              aria-label={`Delete ${l.name}`}
            >
              ×
            </button>
          </li>
        ))}
        {logs.length === 0 && <li className="hint">No recordings yet.</li>}
      </ul>
      {replay && <ReplayViewer name={replay.name} rec={replay.rec} onClose={() => setReplay(null)} />}
    </details>
  );
}

const MODE_COLORS: Record<string, string> = {
  tracking: "#7cc17f",
  slewing: "#6ea8dc",
  manual: "#6ea8dc",
  homing: "#6ea8dc",
  idle: "#5f666e",
  emergency_stop: "#e0645c",
  fault: "#e0645c",
  disconnected: "#e0645c",
};

export function ReplayViewer({ name, rec, onClose }: { name: string; rec: Recording; onClose: () => void }) {
  const tz = useStore((s) => s.prefs.timeZone);
  const samples = rec.samples;
  const stats = useMemo(() => trackingStats(samples), [samples]);
  const t0 = samples[0]?.t ?? 0;
  const t1 = samples[samples.length - 1]?.t ?? 1;
  const [cursor, setCursor] = useState(t0);
  const [playing, setPlaying] = useState(false);
  const [useTrue, setUseTrue] = useState(samples.some((s) => s.te != null));
  const canvas = useRef<HTMLCanvasElement>(null);
  // Plot scale: errors while tracking only (slews are off the chart by design), clipped at the 99th percentile.
  const yMax = useMemo(() => {
    const xs = samples.filter((s) => s.m === "tracking").map((s) => ((useTrue ? s.te : s.e) ?? 0) * 3600).sort((a, b) => a - b);
    return Math.max(5, xs.length ? xs[Math.floor(xs.length * 0.99)] * 1.2 : 60);
  }, [samples, useTrue]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setCursor((c) => {
        const n = c + (t1 - t0) / 300; // whole recording in about 30 s
        if (n >= t1) setPlaying(false);
        return Math.min(n, t1);
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, t0, t1]);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth;
    const h = cv.clientHeight;
    cv.width = w * dpr;
    cv.height = h * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    g.fillStyle = "#121417";
    g.fillRect(0, 0, w, h);
    const pad = { l: 44, r: 8, t: 8, b: 22 };
    const x = (t: number) => pad.l + ((t - t0) / Math.max(1e-9, t1 - t0)) * (w - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - Math.min(v, yMax) / yMax) * (h - pad.t - pad.b - 6);
    // Mode band along the bottom.
    for (let i = 0; i < samples.length; i++) {
      const s = samples[i];
      const xe = i + 1 < samples.length ? x(samples[i + 1].t) : x(s.t) + 1;
      g.fillStyle = MODE_COLORS[s.m] ?? "#5f666e";
      g.fillRect(x(s.t), h - pad.b - 4, Math.max(1, xe - x(s.t)), 4);
    }
    // Grid + y labels (arcsec).
    g.strokeStyle = "#2b2f34";
    g.fillStyle = "#8d949c";
    g.font = "11px system-ui, sans-serif";
    for (let k = 0; k <= 4; k++) {
      const v = (yMax * k) / 4;
      g.beginPath();
      g.moveTo(pad.l, y(v));
      g.lineTo(w - pad.r, y(v));
      g.stroke();
      g.fillText(`${v.toFixed(v < 10 ? 1 : 0)}″`, 4, y(v) + 4);
    }
    // Events.
    g.strokeStyle = "rgba(216,166,70,0.5)";
    for (const e of rec.events) {
      if (e.t == null || e.t < t0 || e.t > t1) continue;
      g.beginPath();
      g.moveTo(x(e.t), pad.t);
      g.lineTo(x(e.t), h - pad.b);
      g.stroke();
    }
    // Error trace (tracking samples only; gaps where the mount was doing something else).
    g.strokeStyle = useTrue ? "#ffd479" : "#6ea8dc";
    g.lineWidth = 1.2;
    g.beginPath();
    let pen = false;
    for (const s of samples) {
      const v = useTrue ? s.te : s.e;
      if (s.m !== "tracking" || v == null) {
        pen = false;
        continue;
      }
      const px = x(s.t);
      const py = y(v * 3600);
      if (pen) g.lineTo(px, py);
      else g.moveTo(px, py);
      pen = true;
    }
    g.stroke();
    // Cursor.
    g.strokeStyle = "#dde0e4";
    g.beginPath();
    g.moveTo(x(cursor), pad.t);
    g.lineTo(x(cursor), h - pad.b);
    g.stroke();
    g.fillStyle = "#8d949c";
    g.fillText(formatTime(t0, tz, true), pad.l, h - 6);
    const endLabel = formatTime(t1, tz, true);
    g.fillText(endLabel, w - pad.r - g.measureText(endLabel).width, h - 6);
  }, [samples, rec.events, cursor, yMax, useTrue, t0, t1, tz]);

  const i = sampleIndexAt(samples, cursor);
  const s = i >= 0 ? samples[i] : null;
  const recent = rec.events.filter((e) => e.t != null && e.t <= cursor).slice(-4);
  const download = () => {
    const blob = new Blob([toCsv(samples)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name.replace(/\.jsonl$/, ".csv");
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  return (
    <div className="modal" role="dialog" aria-label="Telemetry replay">
      <div className="modal-body replay">
        <header>
          <h2>Replay: {name.replace(/\.jsonl$/, "")}</h2>
          <button onClick={onClose}>Close</button>
        </header>
        {samples.length === 0 ? (
          <p>This recording has no samples.</p>
        ) : (
          <>
            <canvas
              ref={canvas}
              className="replay-chart"
              onPointerDown={(e) => {
                const r = (e.target as HTMLCanvasElement).getBoundingClientRect();
                const f = (e.clientX - r.left - 44) / Math.max(1, r.width - 52);
                setCursor(t0 + Math.max(0, Math.min(1, f)) * (t1 - t0));
              }}
              aria-label="Tracking error over time"
            />
            <div className="row">
              <button onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
              <input type="range" min={t0} max={t1} step={(t1 - t0) / 1000 || 1} value={cursor} onChange={(e) => setCursor(Number(e.target.value))} aria-label="Replay position" />
            </div>
            <label className="inline-check">
              <input type="checkbox" checked={useTrue} onChange={(e) => setUseTrue(e.target.checked)} disabled={!samples.some((x) => x.te != null)} /> Plot the simulator's true error
              (yellow) instead of the mount-reported error (blue)
            </label>
            {s && (
              <dl className="grid2 replay-read">
                <dt>Time</dt>
                <dd>{formatDateTime(s.t, tz)}</dd>
                <dt>Mode · target</dt>
                <dd>
                  {s.m} · {s.id ?? "none"}
                </dd>
                <dt>Pointing alt / az</dt>
                <dd className="num">
                  {s.a.toFixed(4)}° / {s.z.toFixed(4)}°
                </dd>
                <dt>Target alt / az</dt>
                <dd className="num">{s.ta != null && s.tz != null ? `${s.ta.toFixed(4)}° / ${s.tz.toFixed(4)}°` : "—"}</dd>
                <dt>Axis rates</dt>
                <dd className="num">
                  {(s.ra * 3600).toFixed(1)}″/s · {(s.rz * 3600).toFixed(1)}″/s
                </dd>
                <dt>Error</dt>
                <dd className="num">
                  {fixedAngle(s.e).trim()} (true {fixedAngle(s.te).trim()})
                </dd>
                <dt>Flags</dt>
                <dd>{s.f.length ? s.f.join(", ") : "none"}</dd>
              </dl>
            )}
            {recent.length > 0 && (
              <ul className="replay-events">
                {recent.map((e, k) => (
                  <li key={k} className={e.level}>
                    <time>{formatTime(e.t, tz, true)}</time> {e.msg}
                  </li>
                ))}
              </ul>
            )}
            <h4>Tracking error while tracking</h4>
            <table className="stats">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Time</th>
                  <th>RMS</th>
                  <th>95 %</th>
                  <th>Max</th>
                  <th>True RMS</th>
                  <th>Max rate</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((x) => (
                  <tr key={x.id}>
                    <td>{x.id}</td>
                    <td>{duration(x.seconds)}</td>
                    <td>{x.rms.toFixed(1)}″</td>
                    <td>{x.p95.toFixed(1)}″</td>
                    <td>{x.max.toFixed(1)}″</td>
                    <td>{x.trueRms == null ? "—" : `${x.trueRms.toFixed(1)}″`}</td>
                    <td>{x.maxAxisRate.toFixed(3)}°/s</td>
                  </tr>
                ))}
                {stats.length === 0 && (
                  <tr>
                    <td colSpan={7}>No tracking in this recording.</td>
                  </tr>
                )}
              </tbody>
            </table>
            <div className="row">
              <button onClick={download}>Export CSV</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ hand controller (phone layout)
export function HandController() {
  const ctl = useStore((s) => isController(s));
  const m = useStore((s) => s.mount);
  const control = useStore((s) => s.control);
  const session = useStore((s) => s.session);
  const speed = useStore((s) => s.manualSpeed);
  const maxRate = useStore((s) => s.mount?.limits.max_rate ?? 5);
  const mode = m?.mode ?? "disconnected";
  const latched = mode === "emergency_stop" || mode === "fault" || mode === "disconnected";
  const cur = session?.items.find((i) => i.id === session.current);
  const next = session?.items.find((i) => i.status === "pending");
  return (
    <div className="hand">
      <div className={`hand-status mode-${mode}`}>
        <strong>{mode.replace("_", " ")}</strong>
        <span>{m?.target ? m.target.name : "no target"}</span>
        <span className="num">error {fixedAngle(m?.tracking_error ?? null)}</span>
      </div>
      <button className="estop hand-estop" onClick={() => command("estop")}>
        E-STOP
      </button>
      <div className="row">
        <button className="stop hand-big" onClick={() => command("stop")}>
          Stop
        </button>
        {latched ? (
          <button className="hand-big warnbtn" disabled={!ctl} onClick={() => command("reset")}>
            Reset
          </button>
        ) : (
          <button className="hand-big" disabled={!ctl || !m?.target || mode === "tracking"} onClick={() => command("track_start")}>
            Track
          </button>
        )}
      </div>
      {!ctl && (
        <button
          className="primary hand-big"
          onClick={() => {
            if (!control?.controller || confirm(`Take control from ${control.controller_name}?`)) requestControl(!!control?.controller);
          }}
        >
          {control?.controller ? `Take over from ${control.controller_name}` : "Take control"}
        </button>
      )}
      <ManualPad enabled={ctl && !latched} speed={Math.min(speed, maxRate)} big />
      <div className="speed" role="radiogroup" aria-label="Manual speed">
        {MANUAL_SPEEDS.map((s) => (
          <button key={s} className={speed === s ? "sel" : ""} onClick={() => setState({ manualSpeed: s })} aria-pressed={speed === s}>
            {s}°/s
          </button>
        ))}
      </div>
      {session && session.items.length > 0 && (
        <div className="hand-session">
          <div>
            <small>Session {session.running ? "running" : "stopped"}</small>
            <span>{cur ? `${cur.name} · ${cur.status}${session.remaining_s != null ? ` · ${duration(session.remaining_s)} left` : ""}` : next ? `next: ${next.name}` : "all done"}</span>
          </div>
          {session.running ? (
            <div className="row">
              <button disabled={!ctl} onClick={() => command("queue_pause")}>
                Pause
              </button>
              <button disabled={!ctl || !session.current} onClick={() => command("queue_skip")}>
                Next
              </button>
            </div>
          ) : (
            <button disabled={!ctl || !next} onClick={() => command("queue_start", {})}>
              Start session
            </button>
          )}
        </div>
      )}
    </div>
  );
}
