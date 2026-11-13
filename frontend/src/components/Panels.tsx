import { type ReactNode, useEffect, useRef, useState } from "react";
import { angleFmt, fixedAngle, fixedNum, formatDateTime, formatTime, fromZonedInput, resolveZone, toZonedInput, zoneLabel } from "../format";
import { Icon, type IconName } from "./Icon";
import { ExposurePlanner, TonightList } from "./Planner";
import { SatellitePasses } from "./Session";
import {
  MANUAL_SPEEDS,
  canControl,
  command,
  isController,
  releaseControl,
  requestControl,
  select,
  setState,
  simNow,
  toast,
  useStore,
} from "../store";

const EMPTY: never[] = [];
const fmt = (v: number | null | undefined, d = 2, unit = "°") => (v == null || Number.isNaN(v) ? "—" : `${v.toFixed(d)}${unit}`);

// ------------------------------------------------------------------ collapsible section
// Every panel of the control column is a section whose header always shows a one-line summary, so a
// collapsed column still tells the user what each part is doing. Open/closed is remembered per device.
const SECTIONS_KEY = "osm-sections";
function sectionOpen(id: string, fallback: boolean): boolean {
  try {
    const v = (JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}") as Record<string, boolean>)[id];
    return typeof v === "boolean" ? v : fallback;
  } catch {
    return fallback;
  }
}
function saveSectionOpen(id: string, open: boolean) {
  try {
    const all = JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}") as Record<string, boolean>;
    localStorage.setItem(SECTIONS_KEY, JSON.stringify({ ...all, [id]: open }));
  } catch {
    /* per-device preference only */
  }
}

export function Section({
  id,
  title,
  summary,
  defaultOpen = false,
  onOpenChange,
  children,
}: {
  id: string;
  title: string;
  summary?: ReactNode;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => sectionOpen(id, defaultOpen));
  useEffect(() => onOpenChange?.(open), [open]);
  return (
    <details
      className={`panel section ${id}`}
      open={open}
      onToggle={(e) => {
        const o = e.currentTarget.open;
        if (o === open) return;
        setOpen(o);
        saveSectionOpen(id, o);
      }}
    >
      <summary>
        <span className="sec-title">{title}</span>
        {summary != null && <span className="sec-sum">{summary}</span>}
      </summary>
      {children}
    </details>
  );
}

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
  // "Best tonight" is a popover over the sky map, not a permanent block, so the map keeps the height.
  const [tonight, setTonight] = useState(false);
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!tonight) return;
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setTonight(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setTonight(false);
    };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      document.removeEventListener("keydown", esc);
    };
  }, [tonight]);
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
  return (
    <section className="panel search" ref={root}>
      <div className="search-row">
        <input
          type="search"
          placeholder="Search: Jupiter, Vega, M31, alpha Lyrae"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setTonight(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits[0]) {
              select(hits[0].id, true);
              setQ("");
            }
          }}
          aria-label="Search sky objects"
        />
        <button
          className={`tonight-toggle${tonight ? " sel" : ""}`}
          onClick={() => {
            setQ("");
            setTonight(!tonight);
          }}
          aria-expanded={tonight}
          aria-controls="tonight-pop"
          title="Best targets for tonight's dark hours"
        >
          <Icon name="sparkle" />
          <span>Best tonight</span>
        </button>
      </div>
      {hits.length > 0 && (
        <ul className="hits" role="listbox">
          {hits.map((h) => (
            <li key={h.id}>
              <button
                onClick={() => {
                  select(h.id, true);
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
      {!q && tonight && (
        <div id="tonight-pop" className="tonight-pop">
          <TonightList onPick={() => setTonight(false)} />
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
  sunlit?: boolean;
  elements_age_days?: number | null;
  /** Constellations: the English name and a short story. */
  lore?: { id: string; english: string; story: string | null };
}

export function TargetPanel() {
  const selected = useStore((s) => s.selectedId);
  const epoch = useStore((s) => s.clock?.epoch);
  const orbitsRev = useStore((s) => s.orbitsRevision);
  const site = useStore((s) => s.config?.config.observer);
  const prefs = useStore((s) => s.prefs);
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
    // Satellites move about 1° per second: refresh their numbers more often.
    const id = setInterval(load, selected.startsWith("sat:") ? 1000 : 4000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [selected, epoch, orbitsRev, site?.latitude, site?.longitude]);

  const empty = (text: string) => (
    <Section id="target" title="Target" defaultOpen summary="none">
      <p className="empty-text">{text}</p>
    </Section>
  );
  if (!selected) return empty("Nothing selected. Click an object on the sky map, pick one from “Best tonight”, or search for it.");
  if (err) return empty(err);
  if (!info) return empty("Computing position…");
  const ev = info.events;
  const A = angleFmt(prefs);
  const timeStr = (unix: number | null | undefined) => formatTime(unix, prefs.timeZone);
  // Rise for objects below the horizon, so a disabled GoTo says when it will work.
  const rises = !info.above_horizon && ev?.rise != null && !ev.never_rises ? `Rises at ${timeStr(ev.rise)}.` : null;
  return (
    <Section id="target" title="Target" defaultOpen summary={info.name}>
      <header>
        <h2>{info.name}</h2>
        <span className={`lamp ${info.above_horizon ? "on" : "off"}`}>{info.above_horizon ? "above horizon" : "below horizon"}</span>
      </header>
      <div className="sub">
        {info.type}
        {info.constellation ? ` · ${info.constellation}` : ""}
        {info.magnitude != null ? ` · mag ${info.magnitude.toFixed(1)}` : ""}
        {info.illuminated != null ? ` · ${(info.illuminated * 100).toFixed(0)}% lit` : ""}
        {info.sunlit != null ? ` · ${info.sunlit ? "sunlit" : "in Earth's shadow"}` : ""}
        {info.elements_age_days != null ? ` · TLE ${info.elements_age_days.toFixed(1)} d old` : ""}
      </div>
      {info.lore?.story && (
        <p className="lore">
          <strong>{info.lore.english}.</strong> {info.lore.story}
        </p>
      )}
      {(info.notes.length > 0 || rises) && (
        <ul className="notes">
          {info.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
          {rises && <li>{rises}</li>}
        </ul>
      )}
      <GotoButtons id={info.id} disabled={!info.above_horizon || info.kind === "sun"} canQueue={info.kind !== "sun"} />
      <dl className="grid2">
        <dt>Altitude</dt>
        <dd>{A.alt(info.alt)}</dd>
        <dt>Azimuth</dt>
        <dd>{A.az(info.az)}</dd>
        <dt>Rise · Transit · Set ({zoneLabel(info.events?.transit ?? simNow(), prefs.timeZone)})</dt>
        <dd>
          {ev?.circumpolar ? "circumpolar" : ev?.never_rises ? "does not rise" : `${timeStr(ev?.rise)} · ${timeStr(ev?.transit)} · ${timeStr(ev?.set)}`}
        </dd>
        <dt>RA (J2000)</dt>
        <dd>{A.ra(info.ra_j2000)}</dd>
        <dt>Dec (J2000)</dt>
        <dd>{A.dec(info.dec_j2000)}</dd>
        <dt>Alt / Az rate</dt>
        <dd>
          {(info.alt_rate * 3600).toFixed(0)}″/s · {(info.az_rate * 3600).toFixed(0)}″/s
        </dd>
        <dt>Field rotation</dt>
        <dd>{info.field_rotation_rate == null ? "—" : `${(info.field_rotation_rate * 3600).toFixed(1)}″/s`}</dd>
        {info.distance_au != null && info.kind !== "star" && info.kind !== "dso" && info.kind !== "radec" && (
          <>
            <dt>Distance</dt>
            <dd>{info.distance_au < 0.01 ? `${(info.distance_au * 149597870.7).toFixed(0)} km` : `${info.distance_au.toFixed(3)} AU`}</dd>
          </>
        )}
        <dt>Sun distance</dt>
        <dd>{fmt(info.sun_separation, 1)}</dd>
      </dl>
      {info.kind === "satellite" && <SatellitePasses id={info.id} />}
      {info.kind !== "altaz" && info.kind !== "satellite" && <ExposurePlanner rate={info.above_horizon ? info.field_rotation_rate : null} dec={info.dec_date} />}
    </Section>
  );
}

function GotoButtons({ id, disabled, canQueue }: { id: string; disabled: boolean; canQueue: boolean }) {
  const ctl = useStore((s) => isController(s));
  return (
    <div className="goto">
      <div className="row">
        <button className="primary goto-main" disabled={!ctl || disabled} onClick={() => command("goto", { target: id, track: true })} title="Slew to the object, then follow it across the sky">
          GoTo &amp; track
        </button>
        <button disabled={!ctl || disabled} onClick={() => command("goto", { target: id, track: false })} title="Slew to the object and stop there">
          GoTo only
        </button>
      </div>
      <div className="row">
        <button
          disabled={!ctl}
          onClick={() => command("sync", { target: id }).then((a) => a.ok && toast(String(a.message)))}
          title="Use when this object is centred in the payload: corrects the pointing and adds an alignment point"
        >
          Sync
        </button>
        <button
          disabled={!ctl || !canQueue}
          onClick={() => command("queue_add", { target: id, duration_s: 600 }).then((a) => a.ok && toast(String(a.message)))}
          title="Add to the observing session (tracks 10 minutes; change it in the Observing session section)"
        >
          Add to session
        </button>
      </div>
      {!ctl && <p className="hint">Take control (top of this column) to point the mount.</p>}
    </div>
  );
}

// ------------------------------------------------------------------ control
const LATCHED = ["emergency_stop", "fault", "disconnected"];

function Authority() {
  const ctl = useStore((s) => isController(s));
  const control = useStore((s) => s.control);
  const conn = useStore((s) => s.connection);
  const allowed = useStore((s) => canControl(s));
  if (conn !== "open") return <div className="authority warn">Disconnected, reconnecting…</div>;
  return (
    <div className={`authority ${ctl ? "ok" : control?.controller ? "other" : "free"}`}>
      {!allowed && !ctl ? (
        <>
          <span title="Observer accounts can watch and stop the mount, but not take control">
            View only{control?.controller ? `: ${control.controller_name} has control` : ""}
          </span>
          <span className="badge">observer</span>
        </>
      ) : ctl ? (
        <>
          <span>You have control</span>
          <button onClick={releaseControl} title="Let another device take control">
            Release
          </button>
        </>
      ) : control?.controller ? (
        <>
          <span>View only: {control.controller_name} has control</span>
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
          <span>View only</span>
          <button className="primary" onClick={() => requestControl(false)}>
            Take control
          </button>
        </>
      )}
    </div>
  );
}

const LATCH_TEXT: Record<string, string> = {
  emergency_stop: "Emergency stop is active: all motion is blocked.",
  fault: "The mount reported a fault: all motion is blocked.",
  disconnected: "The mount is disconnected: all motion is blocked.",
};

/** Always-visible top of the control column: who controls the mount, what it is doing, and the stop buttons. */
export function ControlHeader() {
  const ctl = useStore((s) => isController(s));
  const m = useStore((s) => s.mount);
  const faults = useStore((s) => s.mount?.driver.faults ?? EMPTY);
  const mode = m?.mode ?? "disconnected";
  const latched = LATCHED.includes(mode);
  return (
    <div className="control-head">
      <Authority />
      <div className={`status mode-${mode}`} aria-live="polite">
        <span className="status-mode">{MODE_LABEL[mode] ?? mode}</span>
        <span className="status-target" title={m?.target?.name}>
          {m?.target ? m.target.name : "no target"}
        </span>
        {(mode === "tracking" || mode === "slewing") && (
          <span className="status-err" title="Mount-reported pointing error">
            {fixedAngle(m?.tracking_error ?? null).trim()}
          </span>
        )}
      </div>
      {latched && (
        <div className="latched" role="alert">
          <span>
            {LATCH_TEXT[mode]} {ctl ? "Check the mount, then reset." : "The controlling device can reset it."}
          </span>
          <button className="warnbtn" disabled={!ctl} onClick={() => command("reset")}>
            Reset{faults.length ? ` (${faults.join(", ")})` : ""}
          </button>
        </div>
      )}
      <div className="row safety">
        <button className="estop" onClick={() => command("estop")} title="Software emergency stop: halts both axes at once and blocks motion until Reset. Works from every device, with or without control. Physical mounts need a hardware E-stop.">
          E-STOP
        </button>
        <button className="stop" onClick={() => command("stop")} title="Brake to a stop and end GoTo, tracking, manual motion and the session">
          Stop
        </button>
      </div>
    </div>
  );
}

export function MotionPanel() {
  const ctl = useStore((s) => isController(s));
  const mode = useStore((s) => s.mount?.mode);
  const target = useStore((s) => (s.mount?.target && s.mount.target.kind !== "altaz" ? s.mount.target.name : null));
  const maxRate = useStore((s) => s.mount?.limits.max_rate ?? 5);
  const speed = useStore((s) => s.manualSpeed);
  const pad = useStore((s) => s.gamepad);
  const latched = LATCHED.includes(mode ?? "disconnected");
  const following = mode === "tracking" || mode === "slewing";
  return (
    <Section id="motion" title="Move" defaultOpen summary={`${Math.min(speed, maxRate)}°/s${pad.id ? " · gamepad" : ""}`}>
      <div className="row">
        {following ? (
          <button disabled={!ctl} onClick={() => command("track_stop")} title="Stop following the target; the mount holds still">
            Stop tracking
          </button>
        ) : (
          <button
            disabled={!ctl || !target || latched}
            onClick={() => command("track_start")}
            title={target ? `Follow ${target} again from where the mount is now` : "GoTo a target first"}
          >
            {target ? `Track ${target}` : "Start tracking"}
          </button>
        )}
        <button disabled={!ctl || latched} onClick={() => command("home")} title="Drive both axes to the home (park) position">
          Home
        </button>
      </div>
      <ManualPad enabled={ctl && !latched} speed={Math.min(speed, maxRate)} />
      <div className="speed">
        <span>Speed</span>
        <div className="seg" role="radiogroup" aria-label="Manual speed">
          {MANUAL_SPEEDS.map((s) => (
            <button key={s} className={speed === s ? "sel" : ""} onClick={() => setState({ manualSpeed: s })} aria-pressed={speed === s}>
              {s}°/s
            </button>
          ))}
        </div>
      </div>
      <p className="hint gamepad-hint" title="Gamepad: hold LB or RB and use the left stick or D-pad; B = Stop, Back/View = E-STOP, Y/X = speed">
        {pad.id
          ? `Gamepad: ${pad.id.slice(0, 40)} · ${pad.moving ? "moving" : pad.held ? "armed" : "hold LB/RB + stick to slew"}`
          : "Gamepad: none (press any button on a connected controller)"}
      </p>
    </Section>
  );
}

export function ManualPad({ enabled, speed, big = false }: { enabled: boolean; speed: number; big?: boolean }) {
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
  // Arrow = direction on the sky as seen from behind the mount: up/down in altitude, azimuth + turns right (east of north).
  const btn = (arrow: IconName, axis: string, dAlt: number, dAz: number, cls: string, name: string) => (
    <button
      className={`pad ${cls}`}
      disabled={!enabled}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start(dAlt, dAz);
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onContextMenu={(e) => e.preventDefault()}
      aria-label={`${name} (${axis})`}
      title={`Hold to move ${name} (${axis})`}
    >
      <span className="arrow">
        <Icon name={arrow} size={big ? 26 : 18} />
      </span>
      <small>{axis}</small>
    </button>
  );
  return (
    <div className={`manual${big ? " big" : ""}`} aria-label="Manual slew (press and hold)">
      {btn("up", "Alt +", 1, 0, "up", "up")}
      {btn("left", "Az −", 0, -1, "left", "left")}
      <div className="pad-center">
        <span>{speed}°/s</span>
        <small>hold to move</small>
      </div>
      {btn("right", "Az +", 0, 1, "right", "right")}
      {btn("down", "Alt −", -1, 0, "down", "down")}
    </div>
  );
}

// ------------------------------------------------------------------ clock
export function ClockPanel() {
  const clock = useStore((s) => s.clock);
  const ctl = useStore((s) => isController(s));
  const warp = useStore((s) => s.config?.capabilities.supports_time_warp ?? false);
  const tz = useStore((s) => s.prefs.timeZone);
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const now = simNow();
  const d = new Date(now * 1000);
  const local = toZonedInput(now, tz);
  const how = clock?.live ? "live" : clock?.rate === 0 ? "paused" : `simulated ×${clock?.rate}`;
  return (
    <Section id="clock" title="Time" summary={`${formatTime(now, tz, true)} · ${how}`}>
      <div className="clock-read">
        <strong>
          {formatDateTime(now, tz)} <span className="zone">{zoneLabel(now, tz)}</span>
        </strong>
        <small>
          UTC {d.toISOString().slice(11, 19)} · {how}
        </small>
      </div>
      {warp && (
        <>
          <div className="seg clock-rates" role="group" aria-label="Clock speed">
            <button disabled={!ctl || clock?.live} className={clock?.live ? "sel" : ""} onClick={() => command("set_clock", { live: true })} title="Back to the real current time">
              Live
            </button>
            {[1, 10, 60, 600].map((r) => (
              <button key={r} disabled={!ctl} className={!clock?.live && clock?.rate === r ? "sel" : ""} onClick={() => command("set_clock", { rate: r })} title={`Run the clock ${r}× faster`}>
                ×{r}
              </button>
            ))}
          </div>
          <label className="clock-jump">
            <span>Jump to</span>
            <input
              type="datetime-local"
              disabled={!ctl}
              defaultValue={local}
              key={`${clock?.epoch}-${tz}`}
              onChange={(e) => {
                const t = fromZonedInput(e.target.value, tz);
                if (Number.isFinite(t)) command("set_clock", { time: t });
              }}
              aria-label={`Set simulation date and time (${resolveZone(tz)})`}
            />
          </label>
          <p className="hint">Simulated mount only: rehearse a night by speeding up the clock or jumping to a date.</p>
        </>
      )}
    </Section>
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
  disconnected: "Disconnected",
};

function Cell({ label, value, title, className = "" }: { label: string; value: string; title?: string; className?: string }) {
  return (
    <div className={`tel ${className}`} title={title}>
      <label>{label}</label>
      <span>{value}</span>
    </div>
  );
}

// Every cell is always rendered and every value has a fixed character width (see format.ts and the
// .telemetry grid in styles.css), so the bar layout never changes while numbers update.
export function Telemetry() {
  const m = useStore((s) => s.mount);
  const sim = useStore((s) => s.config?.capabilities.simulated);
  const prefs = useStore((s) => s.prefs);
  if (!m) return <footer className="telemetry waiting">Waiting for telemetry…</footer>;
  const p = m.position;
  const flags = [...m.driver.faults, ...m.flags];
  const rate = (r: number) => `${fixedNum(r * 3600, 5, 0, true)}″/s`;
  const A = angleFmt(prefs);
  return (
    <footer className={`telemetry angles-${prefs.angles}`}>
      <div className={`mode mode-${m.mode}`}>{MODE_LABEL[m.mode] ?? m.mode}</div>
      <Cell label="Mount alt / az" value={`${A.alt(p.alt)} / ${A.az(p.az)}`} />
      <Cell label="RA / Dec" className="radec" value={p.ra != null ? `${A.ra(p.ra)}  ${A.dec(p.dec)}` : "—"} />
      <div className="tel target-cell" title={m.target?.name}>
        <label>Target</label>
        <span>{m.target ? m.target.name : "none"}</span>
      </div>
      <Cell label="Error" value={fixedAngle(m.tracking_error)} title="Mount-reported pointing error" />
      {sim ? (
        <Cell label="True error (sim)" value={fixedAngle(m.true_pointing_error)} title="Simulation ground truth: where the payload really points" />
      ) : (
        <Cell label="True error" value="n/a" title="Only known in the simulator" />
      )}
      <Cell label="Rate alt / az" value={`${rate(p.alt_rate)} ${rate(p.az_rate)}`} />
      <Cell label="Axes alt / az" value={`${fixedNum(p.alt_axis, 3, 2, true)}° ${fixedNum(p.az_axis, 3, 2, true)}°`} title="Raw axis angles (azimuth is multi-turn)" />
      <Cell label="Motor" value={`${m.driver.connected ? "link ok" : "no link"}, ${m.driver.homed ? "homed" : "not homed"}`} />
      <div className="tel flags">
        <label>Flags</label>
        <span className={flags.length ? "" : "none"} title={flags.join(", ")}>
          {flags.length ? flags.join(", ") : "none"}
        </span>
      </div>
    </footer>
  );
}

export function EventLog() {
  const events = useStore((s) => s.mount?.events ?? EMPTY);
  const tz = useStore((s) => s.prefs.timeZone);
  const last = events[events.length - 1];
  return (
    <Section id="events" title="Log" summary={last ? <span className={`ev-${last.level}`}>{last.msg}</span> : "empty"}>
      <ul>
        {[...events].reverse().slice(0, 8).map((e, i) => (
          <li key={`${e.t}-${i}`} className={e.level}>
            <time>{formatTime(e.t, tz, true)}</time> {e.msg}
          </li>
        ))}
      </ul>
    </Section>
  );
}
