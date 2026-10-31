import { useEffect, useRef, useState } from "react";
import { formatDMS, formatRA } from "../sky/projection";
import {
  command,
  getState,
  isController,
  releaseControl,
  requestControl,
  select,
  simNow,
  toast,
  useStore,
} from "../store";

const EMPTY: never[] = [];
const fmt = (v: number | null | undefined, d = 2, unit = "°") => (v == null || Number.isNaN(v) ? "—" : `${v.toFixed(d)}${unit}`);
const arcsec = (deg: number | null | undefined) =>
  deg == null ? "—" : deg < 1 / 60 ? `${(deg * 3600).toFixed(1)}″` : deg < 1 ? `${(deg * 60).toFixed(2)}′` : `${deg.toFixed(2)}°`;
const timeStr = (unix: number | null | undefined) =>
  unix == null ? "—" : new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// ------------------------------------------------------------------ search
interface Hit {
  id: string;
  name: string;
  kind: string;
  magnitude: number | null;
  detail: string;
}

export function SearchPanel() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  useEffect(() => {
    if (!q.trim()) {
      setHits([]);
      return;
    }
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(q)}&limit=8`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : []))
        .then(setHits)
        .catch(() => {});
    }, 150);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [q]);
  const quick = ["Jupiter", "Saturn", "Moon", "Vega", "Sirius", "M31", "M42", "Polaris"];
  return (
    <section className="panel search">
      <input
        type="search"
        placeholder="Search: Jupiter, Vega, M31, alpha Lyr…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && hits[0]) {
            select(hits[0].id);
            setQ("");
          }
        }}
        aria-label="Search sky objects"
      />
      {hits.length > 0 && (
        <ul className="hits" role="listbox">
          {hits.map((h) => (
            <li key={h.id}>
              <button
                onClick={() => {
                  select(h.id);
                  setQ("");
                }}
              >
                <span>{h.name}</span>
                <small>
                  {h.kind}
                  {h.detail ? ` · ${h.detail}` : ""}
                  {h.magnitude != null ? ` · mag ${h.magnitude.toFixed(1)}` : ""}
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!q && (
        <div className="chips">
          {quick.map((n) => (
            <button key={n} className="chip" onClick={() => setQ(n)}>
              {n}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ target
interface Info {
  id: string;
  name: string;
  kind: string;
  type: string;
  ra_j2000: number;
  dec_j2000: number;
  ra_date: number;
  dec_date: number;
  alt: number;
  az: number;
  alt_rate: number;
  az_rate: number;
  above_horizon: boolean;
  magnitude: number | null;
  constellation: string | null;
  distance_au: number | null;
  sun_separation: number;
  field_rotation_rate: number | null;
  events?: { rise: number | null; set: number | null; transit: number | null; circumpolar?: boolean; never_rises?: boolean };
  notes: string[];
  illuminated?: number;
}

export function TargetPanel() {
  const selected = useStore((s) => s.selectedId);
  const epoch = useStore((s) => s.clock?.epoch);
  const site = useStore((s) => s.config?.config.observer);
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setInfo(null);
    setErr(null);
    if (!selected) return;
    let stop = false;
    const load = () =>
      fetch(`/api/object?id=${encodeURIComponent(selected)}&t=${simNow()}`)
        .then(async (r) => {
          if (!r.ok) throw new Error((await r.json()).detail ?? "not found");
          return r.json();
        })
        .then((d) => !stop && setInfo(d))
        .catch((e) => !stop && setErr(String(e.message ?? e)));
    load();
    const id = setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [selected, epoch, site?.latitude, site?.longitude]);

  if (!selected) return <section className="panel target empty">Select an object on the sky map or search for one.</section>;
  if (err) return <section className="panel target empty">{err}</section>;
  if (!info) return <section className="panel target empty">Loading…</section>;
  const ev = info.events;
  return (
    <section className="panel target">
      <header>
        <h2>{info.name}</h2>
        <span className={`badge ${info.above_horizon ? "ok" : "warn"}`}>{info.above_horizon ? "above horizon" : "below horizon"}</span>
      </header>
      <div className="sub">
        {info.type}
        {info.constellation ? ` · ${info.constellation}` : ""}
        {info.magnitude != null ? ` · mag ${info.magnitude.toFixed(1)}` : ""}
        {info.illuminated != null ? ` · ${(info.illuminated * 100).toFixed(0)}% lit` : ""}
      </div>
      <dl className="grid2">
        <dt>RA (J2000)</dt>
        <dd>{formatRA(info.ra_j2000)}</dd>
        <dt>Dec (J2000)</dt>
        <dd>{formatDMS(info.dec_j2000)}</dd>
        <dt>Altitude</dt>
        <dd>{fmt(info.alt, 3)}</dd>
        <dt>Azimuth</dt>
        <dd>{fmt(info.az, 3)}</dd>
        <dt>Alt / Az rate</dt>
        <dd>
          {(info.alt_rate * 3600).toFixed(0)}″/s · {(info.az_rate * 3600).toFixed(0)}″/s
        </dd>
        <dt>Field rotation</dt>
        <dd>{info.field_rotation_rate == null ? "—" : `${(info.field_rotation_rate * 3600).toFixed(1)}″/s`}</dd>
        <dt>Rise · Transit · Set</dt>
        <dd>
          {ev?.circumpolar ? "circumpolar" : ev?.never_rises ? "does not rise" : `${timeStr(ev?.rise)} · ${timeStr(ev?.transit)} · ${timeStr(ev?.set)}`}
        </dd>
        {info.distance_au != null && info.kind !== "star" && info.kind !== "dso" && info.kind !== "radec" && (
          <>
            <dt>Distance</dt>
            <dd>{info.distance_au < 0.01 ? `${(info.distance_au * 149597870.7).toFixed(0)} km` : `${info.distance_au.toFixed(3)} AU`}</dd>
          </>
        )}
        <dt>Sun distance</dt>
        <dd>{fmt(info.sun_separation, 1)}</dd>
      </dl>
      {info.notes.length > 0 && (
        <ul className="notes">
          {info.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <GotoButtons id={info.id} disabled={!info.above_horizon || info.kind === "sun"} />
    </section>
  );
}

function GotoButtons({ id, disabled }: { id: string; disabled: boolean }) {
  const ctl = useStore((s) => isController(s));
  return (
    <div className="row">
      <button className="primary" disabled={!ctl || disabled} onClick={() => command("goto", { target: id, track: true })}>
        GoTo &amp; track
      </button>
      <button disabled={!ctl || disabled} onClick={() => command("goto", { target: id, track: false })}>
        GoTo only
      </button>
      <button disabled={!ctl} onClick={() => command("sync", { target: id }).then((a) => a.ok && toast("Synced: pointing model updated"))} title="Declare that the mount currently points at this object (alignment)">
        Sync
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ control
export function ControlAuthority() {
  const ctl = useStore((s) => isController(s));
  const control = useStore((s) => s.control);
  const conn = useStore((s) => s.connection);
  if (conn !== "open") return <div className="authority warn">Disconnected — reconnecting…</div>;
  return (
    <div className={`authority ${ctl ? "ok" : ""}`}>
      {ctl ? (
        <>
          <span>You have control</span>
          <button onClick={releaseControl}>Release</button>
        </>
      ) : control?.controller ? (
        <>
          <span>Observing · {control.controller_name} has control</span>
          <button
            onClick={() => {
              if (confirm(`Take control from ${control.controller_name}?`)) requestControl(true);
            }}
          >
            Take over
          </button>
        </>
      ) : (
        <>
          <span>Observing · nobody has control</span>
          <button className="primary" onClick={() => requestControl(false)}>
            Take control
          </button>
        </>
      )}
    </div>
  );
}

export function MotionPanel() {
  const ctl = useStore((s) => isController(s));
  const mode = useStore((s) => s.mount?.mode);
  const hasTarget = useStore((s) => !!s.mount?.target && s.mount.target.kind !== "altaz");
  const faults = useStore((s) => s.mount?.driver.faults ?? EMPTY);
  const maxRate = useStore((s) => s.mount?.limits.max_rate ?? 5);
  const [speed, setSpeed] = useState(1);
  const latched = mode === "emergency_stop" || mode === "fault" || mode === "disconnected";
  return (
    <section className="panel motion">
      <div className="row">
        <button className="estop" onClick={() => command("estop")} title="Software emergency stop (always available). Physical mounts need a hardware E-stop.">
          E-STOP
        </button>
        <button className="stop" onClick={() => command("stop")}>
          Stop
        </button>
      </div>
      <div className="row">
        <button disabled={!ctl || !hasTarget || mode === "tracking"} onClick={() => command("track_start")}>
          Start tracking
        </button>
        <button disabled={!ctl || (mode !== "tracking" && mode !== "slewing")} onClick={() => command("track_stop")}>
          Stop tracking
        </button>
      </div>
      <div className="row">
        <button disabled={!ctl || !latched} className={latched ? "warnbtn" : ""} onClick={() => command("reset")}>
          Reset {faults.length ? `(${faults.join(", ")})` : ""}
        </button>
        <button disabled={!ctl} onClick={() => command("home")}>
          Home
        </button>
      </div>
      <AlignButton enabled={ctl && !latched} />
      <ManualPad enabled={ctl && !latched} speed={Math.min(speed, maxRate)} />
      <div className="speed" role="radiogroup" aria-label="Manual speed">
        {[0.05, 0.5, 2, 5].map((s) => (
          <button key={s} className={speed === s ? "sel" : ""} onClick={() => setSpeed(s)} aria-pressed={speed === s}>
            {s}°/s
          </button>
        ))}
      </div>
    </section>
  );
}

function AlignButton({ enabled }: { enabled: boolean }) {
  const available = useStore((s) => !!s.config?.plate_solver && !!s.config?.capabilities.simulated);
  const hasTarget = useStore((s) => !!s.mount?.target);
  const [busy, setBusy] = useState(false);
  if (!available) return null;
  return (
    <div className="row">
      <button
        disabled={!enabled || !hasTarget || busy}
        title="Experimental: render a synthetic camera frame at the simulated true pointing, plate-solve it and correct the pointing model"
        onClick={async () => {
          setBusy(true);
          const a = await command("align_simulated", { apply: true });
          setBusy(false);
          if (a.ok) toast(String(a.message));
        }}
      >
        {busy ? "Solving…" : "Align: plate solve (simulated camera, experimental)"}
      </button>
    </div>
  );
}

function ManualPad({ enabled, speed }: { enabled: boolean; speed: number }) {
  const timer = useRef<number | undefined>(undefined);
  const start = (dAlt: number, dAz: number) => {
    if (!enabled) return;
    const sendRate = () => command("manual", { alt_rate: dAlt * speed, az_rate: dAz * speed }, true);
    sendRate();
    clearInterval(timer.current);
    // Dead-man: the server stops if refreshes stop arriving (e.g. connection lost).
    timer.current = window.setInterval(sendRate, 250);
  };
  const end = () => {
    if (timer.current !== undefined) {
      clearInterval(timer.current);
      timer.current = undefined;
      command("manual", { alt_rate: 0, az_rate: 0 }, true);
    }
  };
  useEffect(() => end, []);
  const btn = (label: string, dAlt: number, dAz: number, cls: string) => (
    <button
      className={`pad ${cls}`}
      disabled={!enabled}
      onPointerDown={(e) => {
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
        start(dAlt, dAz);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onContextMenu={(e) => e.preventDefault()}
      aria-label={cls}
    >
      {label}
    </button>
  );
  return (
    <div className="manual" aria-label="Manual slew (press and hold)">
      {btn("▲", 1, 0, "up")}
      {btn("◀", 0, -1, "left")}
      <div className="pad-center">hold</div>
      {btn("▶", 0, 1, "right")}
      {btn("▼", -1, 0, "down")}
    </div>
  );
}

// ------------------------------------------------------------------ clock
export function ClockPanel() {
  const clock = useStore((s) => s.clock);
  const ctl = useStore((s) => isController(s));
  const warp = useStore((s) => s.config?.capabilities.supports_time_warp ?? false);
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const now = simNow();
  const d = new Date(now * 1000);
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  return (
    <section className="panel clock">
      <div className="clock-read">
        <strong>{d.toLocaleString([], { dateStyle: "medium", timeStyle: "medium" })}</strong>
        <small>
          UTC {d.toISOString().slice(11, 19)} · {clock?.live ? "live" : clock?.rate === 0 ? "paused" : `simulated ×${clock?.rate}`}
        </small>
      </div>
      {warp && (
        <div className="row wrap">
          <button disabled={!ctl || clock?.live} onClick={() => command("set_clock", { live: true })}>
            Live
          </button>
          {[1, 10, 60, 600].map((r) => (
            <button key={r} disabled={!ctl} className={!clock?.live && clock?.rate === r ? "sel" : ""} onClick={() => command("set_clock", { rate: r })}>
              ×{r}
            </button>
          ))}
          <input
            type="datetime-local"
            disabled={!ctl}
            defaultValue={local}
            key={clock?.epoch}
            onChange={(e) => {
              const t = new Date(e.target.value).getTime() / 1000;
              if (Number.isFinite(t)) command("set_clock", { time: t });
            }}
            aria-label="Set simulation date and time (local)"
          />
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ settings
export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const cfg = useStore((s) => s.config);
  const ctl = useStore((s) => isController(s));
  const [obs, setObs] = useState(() => ({ ...getState().config!.config.observer }));
  const [sim, setSim] = useState(() => ({ ...getState().config!.config.sim }));
  const [control, setControl] = useState(() => ({ ...getState().config!.config.control }));
  const [payload, setPayload] = useState(() => getState().config!.config.payload);
  if (!cfg) return null;
  const save = async () => {
    const r = await command("set_config", { observer: obs, sim, control, payload });
    if (r.ok) {
      toast("Settings saved");
      onClose();
    }
  };
  const geo = () => {
    if (!navigator.geolocation) return toast("Geolocation not available", "error");
    navigator.geolocation.getCurrentPosition(
      (p) => setObs({ ...obs, latitude: +p.coords.latitude.toFixed(4), longitude: +p.coords.longitude.toFixed(4), elevation_m: Math.round(p.coords.altitude ?? obs.elevation_m), name: "My location" }),
      (e) => toast(`Location unavailable: ${e.message}`, "error"),
    );
  };
  const num = (label: string, value: number, set: (v: number) => void, step = 0.01, help?: string) => (
    <label title={help}>
      <span>{label}</span>
      <input type="number" step={step} value={value} onChange={(e) => set(Number(e.target.value))} />
    </label>
  );
  const simNum = (key: string, label: string, step = 0.01, help?: string) =>
    num(label, sim[key] as number, (v) => setSim({ ...sim, [key]: v }), step, help);
  return (
    <div className="modal" role="dialog" aria-label="Settings">
      <div className="modal-body">
        <header>
          <h2>Settings</h2>
          <button onClick={onClose} aria-label="Close">✕</button>
        </header>
        {!ctl && <p className="warn-text">Take control to change settings.</p>}
        <fieldset>
          <legend>Observer location</legend>
          <label>
            <span>Name</span>
            <input value={obs.name} onChange={(e) => setObs({ ...obs, name: e.target.value })} />
          </label>
          {num("Latitude (°, N+)", obs.latitude, (v) => setObs({ ...obs, latitude: v }), 0.0001)}
          {num("Longitude (°, E+)", obs.longitude, (v) => setObs({ ...obs, longitude: v }), 0.0001)}
          {num("Elevation (m)", obs.elevation_m, (v) => setObs({ ...obs, elevation_m: v }), 1)}
          <button type="button" onClick={geo}>Use my location (optional)</button>
        </fieldset>
        <fieldset>
          <legend>Payload</legend>
          <select value={payload} onChange={(e) => setPayload(e.target.value)}>
            {Object.values(cfg.payloads).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.mass_kg} kg, max alt {p.max_altitude}°
              </option>
            ))}
          </select>
        </fieldset>
        <fieldset>
          <legend>Pointing &amp; safety</legend>
          {num("GoTo tolerance (°)", control.goto_tolerance_deg as number, (v) => setControl({ ...control, goto_tolerance_deg: v }), 0.005)}
          {num("Min. target altitude (°)", control.min_target_altitude as number, (v) => setControl({ ...control, min_target_altitude: v }), 1)}
          {num("Sun exclusion radius (°)", control.sun_exclusion_deg as number, (v) => setControl({ ...control, sun_exclusion_deg: v }), 1)}
          <label className="check">
            <input type="checkbox" checked={control.track_after_goto as boolean} onChange={(e) => setControl({ ...control, track_after_goto: e.target.checked })} />
            <span>Track automatically after GoTo</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={control.auto_unwind as boolean} onChange={(e) => setControl({ ...control, auto_unwind: e.target.checked })} />
            <span>Unwind cable wrap automatically</span>
          </label>
        </fieldset>
        {cfg.capabilities.simulated && (
          <fieldset>
            <legend>Simulated imperfections</legend>
            {simNum("command_latency_s", "Command latency (s)", 0.01)}
            {simNum("backlash_alt", "Backlash alt (°)", 0.01)}
            {simNum("backlash_az", "Backlash az (°)", 0.01)}
            {simNum("az_zero_offset", "Azimuth zero error (°)", 0.05, "True north misalignment — uncorrected until Sync / plate solve")}
            {simNum("alt_zero_offset", "Altitude zero error (°)", 0.05)}
            {simNum("tilt_magnitude", "Base tilt (°)", 0.05)}
            {simNum("tilt_direction", "Tilt direction (az °)", 1)}
            {simNum("encoder_noise", "Encoder noise (° RMS)", 0.001)}
            <FaultButtons faults={cfg.injectable_faults} />
          </fieldset>
        )}
        <div className="row">
          <button className="primary" disabled={!ctl} onClick={save}>
            Save
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function FaultButtons({ faults }: { faults: string[] }) {
  const active = useStore((s) => s.mount?.driver.motor.injected_faults ?? EMPTY);
  const ctl = useStore((s) => isController(s));
  return (
    <div className="faults">
      <span>Fault injection:</span>
      {faults.map((f) => (
        <button key={f} disabled={!ctl} className={active.includes(f) ? "warnbtn" : ""} onClick={() => command("inject_fault", { fault: f, active: !active.includes(f) })}>
          {f}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ telemetry
const MODE_LABEL: Record<string, string> = {
  idle: "Idle",
  slewing: "Slewing",
  tracking: "Tracking",
  manual: "Manual",
  homing: "Homing",
  emergency_stop: "E-STOP",
  fault: "Fault",
  disconnected: "Mount disconnected",
};

export function Telemetry() {
  const m = useStore((s) => s.mount);
  const latency = useStore((s) => s.latencyMs);
  const sim = useStore((s) => s.config?.capabilities.simulated);
  if (!m) return <footer className="telemetry">Waiting for telemetry…</footer>;
  return (
    <footer className="telemetry">
      <div className={`mode mode-${m.mode}`}>{MODE_LABEL[m.mode] ?? m.mode}</div>
      <div className="tel">
        <label>Mount Alt/Az</label>
        <span>
          {fmt(m.position.alt, 3)} / {fmt(m.position.az, 3)}
        </span>
      </div>
      <div className="tel">
        <label>RA / Dec</label>
        <span>{m.position.ra != null ? `${formatRA(m.position.ra)} ${formatDMS(m.position.dec!)}` : "—"}</span>
      </div>
      <div className="tel">
        <label>Target</label>
        <span>{m.target ? `${m.target.name} · ${fmt(m.target.alt, 3)} / ${fmt(m.target.az, 3)}` : "—"}</span>
      </div>
      <div className="tel">
        <label>Error (mount)</label>
        <span>{arcsec(m.tracking_error)}</span>
      </div>
      {sim && (
        <div className="tel" title="Simulation ground truth: where the payload really points">
          <label>Error (true, sim)</label>
          <span>{arcsec(m.true_pointing_error)}</span>
        </div>
      )}
      <div className="tel">
        <label>Axis rates</label>
        <span>
          {(m.position.alt_rate * 3600).toFixed(0)}″/s · {(m.position.az_rate * 3600).toFixed(0)}″/s
        </span>
      </div>
      <div className="tel">
        <label>Axes (alt / az°)</label>
        <span>
          {m.position.alt_axis.toFixed(2)} / {m.position.az_axis.toFixed(2)}
        </span>
      </div>
      <div className="tel">
        <label>Motor</label>
        <span>
          {m.driver.connected ? "link ok" : "no link"} · {m.driver.homed ? "homed" : "not homed"} · {m.driver.motor.alt_mode}/{m.driver.motor.az_mode}
        </span>
      </div>
      <div className="tel">
        <label>Network</label>
        <span>{latency != null ? `${latency.toFixed(0)} ms` : "—"}</span>
      </div>
      {(m.flags.length > 0 || m.driver.faults.length > 0) && (
        <div className="tel flags">
          <label>Flags</label>
          <span>{[...m.driver.faults, ...m.flags].join(", ")}</span>
        </div>
      )}
    </footer>
  );
}

export function EventLog() {
  const events = useStore((s) => s.mount?.events ?? EMPTY);
  return (
    <section className="panel events">
      <h3>Events</h3>
      <ul>
        {[...events].reverse().slice(0, 8).map((e, i) => (
          <li key={`${e.t}-${i}`} className={e.level}>
            <time>{new Date(e.t * 1000).toLocaleTimeString()}</time> {e.msg}
          </li>
        ))}
      </ul>
    </section>
  );
}
