import { useEffect, useState } from "react";
import {
  CAMERA_PRESETS,
  PAYLOAD_PRESET,
  fieldOfView,
  formatSeconds,
  maxExposureFieldRotation,
  maxExposureUntracked,
  pixelScale,
  type Camera,
} from "../sky/exposure";
import { formatTime } from "../format";
import { command, isController, select, simNow, toast, useStore } from "../store";


// ------------------------------------------------------------------ tonight
interface TonightTarget {
  id: string;
  name: string;
  kind: string;
  type: string;
  magnitude: number | null;
  max_alt: number;
  best_time: number;
  above_from: number;
  above_until: number;
  hours_above: number;
}

interface Tonight {
  window: { start: number; end: number; hours: number } | null;
  moon?: { illuminated: number; up_hours: number };
  targets: TonightTarget[];
  total?: number;
  message?: string;
}

const KINDS: [string, string][] = [
  ["all", "All"],
  ["planet", "Planets"],
  ["dso", "Deep sky"],
  ["star", "Bright stars"],
];

export function TonightList() {
  const site = useStore((s) => s.config?.config.observer);
  const epoch = useStore((s) => s.clock?.epoch);
  const tz = useStore((s) => s.prefs.timeZone);
  const clock = (unix: number) => formatTime(unix, tz);
  const [kind, setKind] = useState("all");
  const [minAlt, setMinAlt] = useState(30);
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<Tonight | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch(`/api/tonight?t=${simNow()}&kind=${kind}&min_alt=${minAlt}&limit=40`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((d) => {
          if (!stop) {
            setData(d);
            setFailed(false);
          }
        })
        .catch(() => !stop && setFailed(true));
    load();
    const id = setInterval(load, 10 * 60 * 1000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [kind, minAlt, epoch, site?.latitude, site?.longitude]);

  const rows = data?.targets ?? [];
  const shown = expanded ? rows : rows.slice(0, 3);
  return (
    <div className="tonight">
      <div className="tonight-head">
        <span className="label">Best tonight</span>
        <span className="tonight-window">
          {failed
            ? "planner unavailable"
            : !data
              ? "planning…"
              : data.window
                ? `dark ${clock(data.window.start)}–${clock(data.window.end)}` +
                  (data.moon ? ` · Moon ${(data.moon.illuminated * 100).toFixed(0)}% lit` : "")
                : "no astronomical darkness"}
        </span>
      </div>
      <div className="tonight-filters" role="radiogroup" aria-label="Object type">
        {KINDS.map(([k, label]) => (
          <button key={k} className={kind === k ? "sel" : ""} aria-pressed={kind === k} onClick={() => setKind(k)}>
            {label}
          </button>
        ))}
        <label title="Only list objects that climb at least this high while it is dark">
          above
          <select id="tonight-minalt" value={minAlt} onChange={(e) => setMinAlt(Number(e.target.value))}>
            {[15, 30, 45, 60].map((a) => (
              <option key={a} value={a}>
                {a}°
              </option>
            ))}
          </select>
        </label>
      </div>
      {data && !data.window && <p className="tonight-empty">{data.message}</p>}
      {data?.window && rows.length === 0 && <p className="tonight-empty">Nothing of this type climbs above {minAlt}° tonight.</p>}
      {shown.length > 0 && (
        <ol className="tonight-list">
          {shown.map((t) => (
            <li key={t.id}>
              <button onClick={() => select(t.id)} title={`Above ${minAlt}° from ${clock(t.above_from)} to ${clock(t.above_until)}`}>
                <span className="tn-name">{t.name}</span>
                <span className="tn-meta">
                  {t.type}
                  {t.magnitude != null ? ` · mag ${t.magnitude.toFixed(1)}` : ""}
                </span>
                <span className="tn-when">
                  best {clock(t.best_time)} · {t.max_alt.toFixed(0)}° · {t.hours_above.toFixed(1)} h
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
      {rows.length > 3 && (
        <button className="tonight-more" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show top 3" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ exposure
const STORE_KEY = "osm-camera";

function loadCamera(payload: string): { preset: string; cam: Camera; blur: number } {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    if (saved && saved.cam && saved.preset) return saved;
  } catch {
    /* ignore */
  }
  const preset = PAYLOAD_PRESET[payload] ?? "phone";
  return { preset, cam: { ...CAMERA_PRESETS[preset].cam }, blur: 1.5 };
}

export function ExposurePlanner({ rate, dec }: { rate: number | null; dec: number }) {
  const payload = useStore((s) => s.config?.config.payload ?? "smartphone");
  const [st, setSt] = useState(() => loadCamera(payload));
  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(st));
    } catch {
      /* per-device preference only */
    }
  }, [st]);
  const cam = st.cam;
  const valid = cam.sensorW > 0 && cam.sensorH > 0 && cam.pixel > 0 && cam.focal > 0 && st.blur > 0;
  const tracked = valid && rate != null ? maxExposureFieldRotation(rate, cam, st.blur) : NaN;
  const fixed = valid ? maxExposureUntracked(dec, cam, st.blur) : NaN;
  const num = (id: string, label: string, value: number, set: (v: number) => void, step: number) => (
    <label htmlFor={id}>
      <span>{label}</span>
      <input id={id} type="number" min={0} step={step} value={value} onChange={(e) => set(Number(e.target.value))} />
    </label>
  );
  return (
    <details className="exposure">
      <summary>Exposure planner</summary>
      <select
        id="exp-preset"
        value={st.preset}
        onChange={(e) => {
          const p = e.target.value;
          setSt({ ...st, preset: p, cam: p === "custom" ? cam : { ...CAMERA_PRESETS[p].cam } });
        }}
        aria-label="Camera and lens"
      >
        {Object.entries(CAMERA_PRESETS).map(([k, v]) => (
          <option key={k} value={k}>
            {v.label}
          </option>
        ))}
        <option value="custom">Custom</option>
      </select>
      <div className="exp-inputs">
        {num("exp-focal", "Focal length mm", cam.focal, (v) => setSt({ ...st, preset: "custom", cam: { ...cam, focal: v } }), 1)}
        {num("exp-pixel", "Pixel µm", cam.pixel, (v) => setSt({ ...st, preset: "custom", cam: { ...cam, pixel: v } }), 0.1)}
        {num("exp-w", "Sensor width mm", cam.sensorW, (v) => setSt({ ...st, preset: "custom", cam: { ...cam, sensorW: v } }), 0.1)}
        {num("exp-h", "Sensor height mm", cam.sensorH, (v) => setSt({ ...st, preset: "custom", cam: { ...cam, sensorH: v } }), 0.1)}
        {num("exp-blur", "Allowed blur px", st.blur, (v) => setSt({ ...st, blur: v }), 0.5)}
      </div>
      {valid ? (
        <dl className="grid2 exp-out">
          <dt>Field of view</dt>
          <dd>
            {fieldOfView(cam.sensorW, cam.focal).toFixed(1)}° × {fieldOfView(cam.sensorH, cam.focal).toFixed(1)}°
          </dd>
          <dt>Image scale</dt>
          <dd>{pixelScale(cam).toFixed(2)}″/px</dd>
          <dt>Max on this mount (tracking)</dt>
          <dd className="exp-key">{rate == null ? "—" : formatSeconds(tracked)}</dd>
          <dt>Max on a fixed tripod</dt>
          <dd>{formatSeconds(fixed)}</dd>
        </dl>
      ) : (
        <p className="tonight-empty">Enter positive numbers for all camera values.</p>
      )}
      <p className="exp-note">
        Tracking removes the drift but not field rotation, which turns the frame around its centre. The limit is set by
        the corner smearing by the allowed blur. Rotation is slowest low in the east or west and fastest near the
        meridian at high altitude. For longer total exposure, stack many short frames.
      </p>
    </details>
  );
}

// ------------------------------------------------------------------ alignment
export function AlignmentPanel() {
  const model = useStore((s) => s.mount?.pointing_model);
  const ctl = useStore((s) => isController(s));
  const solver = useStore((s) => !!s.config?.plate_solver && !!s.config?.capabilities.simulated);
  const hasTarget = useStore((s) => !!s.mount?.target && s.mount.target.kind !== "altaz");
  const [busy, setBusy] = useState(false);
  if (!model) return null;
  const n = model.points.length;
  const arc = (deg: number | null) => (deg == null ? "—" : deg < 1 / 60 ? `${(deg * 3600).toFixed(0)}″` : `${(deg * 60).toFixed(1)}′`);
  return (
    <section className="panel alignment">
      <h3 className="label">Alignment</h3>
      <p className="align-help">
        {n === 0
          ? "Centre a star in the payload, then press Sync on it. One star fixes the zero offsets; three or more spread around the sky also correct a tilted base."
          : n < model.tilt_min_points
            ? `${n} point${n > 1 ? "s" : ""}: zero offsets corrected. Add ${model.tilt_min_points - n} more, spread in azimuth, to correct base tilt.`
            : `${n} points: zero offsets and base tilt corrected.`}
      </p>
      {n > 0 && (
        <>
          <dl className="grid2">
            <dt>Model</dt>
            <dd>{model.terms}</dd>
            <dt>Zero offsets alt / az</dt>
            <dd>
              {model.alt_off >= 0 ? "+" : ""}
              {model.alt_off.toFixed(3)}° / {model.az_off >= 0 ? "+" : ""}
              {model.az_off.toFixed(3)}°
            </dd>
            <dt>Base tilt</dt>
            <dd>{n >= model.tilt_min_points ? `${model.tilt.toFixed(3)}°` : "not fitted"}</dd>
            <dt>Residual RMS</dt>
            <dd>{n > 1 ? arc(model.rms) : "—"}</dd>
          </dl>
          <ol className="align-points">
            {model.points.map((p, i) => (
              <li key={`${p.label}-${p.time}`}>
                <span>
                  {p.label}
                  {p.source === "plate_solve" ? " (solved)" : ""}
                </span>
                <span className="mono">{n > 1 ? arc(p.residual) : ""}</span>
                <button disabled={!ctl} onClick={() => command("align_remove", { index: i })} aria-label={`Remove point ${p.label}`}>
                  Remove
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
      <div className="row">
        {solver && (
          <button
            disabled={!ctl || !hasTarget || busy}
            title="Experimental: render a synthetic camera frame at the simulated true pointing, plate-solve it and add the result as an alignment point"
            onClick={async () => {
              setBusy(true);
              const a = await command("align_simulated", { apply: true });
              setBusy(false);
              if (a.ok) toast(String(a.message));
            }}
          >
            {busy ? "Solving…" : "Plate solve (sim, experimental)"}
          </button>
        )}
        <button disabled={!ctl || n === 0} onClick={() => command("clear_sync")}>
          Clear model
        </button>
      </div>
    </section>
  );
}
