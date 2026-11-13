// Mosaic and timelapse planner for a phone or camera on the mount. Mosaic: a grid of overlapping frames around the
// selected target, drawn on the sky map, checked against the mount's limits and added to the observing session in one go.
// Timelapse: holding one field for a while: frames, how far the field rotates, where the target goes and when it sets.
import { useEffect, useState } from "react";
import { formatDuration } from "../preview";
import { type MosaicPlan, payloadFov, rotationText, type TimelapsePlan } from "../planning";
import { command, isController, setMosaicPreview, simNow, toast, useStore } from "../store";
import { Section } from "./Panels";

function Num({ label, value, onChange, min, max, step = 1, unit }: { label: string; value: number; onChange: (v: number) => void; min: number; max: number; step?: number; unit?: string }) {
  return (
    <label className="mz-num">
      <span>{label}</span>
      <span className="num-field">
        <input type="number" value={value} min={min} max={max} step={step} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
        {unit && <span className="unit">{unit}</span>}
      </span>
    </label>
  );
}

const clampNum = (v: number, lo: number, hi: number) => (Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : lo);

function MosaicTab({ target }: { target: string | null }) {
  const payload = useStore((s) => s.config?.config.payload);
  const ctl = useStore((s) => isController(s));
  const epoch = useStore((s) => s.clock?.epoch);
  const base = payloadFov(payload);
  const [cols, setCols] = useState(3);
  const [rows, setRows] = useState(2);
  const [overlap, setOverlap] = useState(25);
  const [portrait, setPortrait] = useState(false);
  const [fovW, setFovW] = useState(() => +base.w.toFixed(1));
  const [fovH, setFovH] = useState(() => +base.h.toFixed(1));
  const [dwell, setDwell] = useState(30);
  const [plan, setPlan] = useState<MosaicPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The camera changes with the payload: start from its field of view again.
  useEffect(() => {
    setFovW(+base.w.toFixed(1));
    setFovH(+base.h.toFixed(1));
  }, [payload]);

  useEffect(() => {
    setPlan(null);
    setError(null);
    if (!target) {
      setMosaicPreview(null);
      return;
    }
    const w = portrait ? fovH : fovW;
    const h = portrait ? fovW : fovH;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      const q = new URLSearchParams({ id: target, t: String(simNow()), cols: String(cols), rows: String(rows), overlap: String(overlap / 100), fov_w: String(w), fov_h: String(h), dwell_s: String(dwell) });
      fetch(`/api/mosaic?${q}`, { signal: abort.signal })
        .then(async (r) => {
          if (!r.ok) throw new Error((await r.json()).detail ?? "could not plan the mosaic");
          return r.json() as Promise<MosaicPlan>;
        })
        .then((p) => {
          setPlan(p);
          setMosaicPreview({ fov: p.fov, panels: p.panels.map((x) => ({ index: x.index, ra: x.ra, dec: x.dec, reachable: x.reachable })) });
        })
        .catch((e) => {
          if (e.name === "AbortError") return;
          setError(String(e.message ?? e));
          setMosaicPreview(null);
        });
    }, 350);
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [target, cols, rows, overlap, portrait, fovW, fovH, dwell, epoch]);
  useEffect(() => () => setMosaicPreview(null), []);

  const add = async () => {
    if (!plan || plan.items.length === 0) return;
    setBusy(true);
    const a = await command("queue_add_many", { items: plan.items, group: "mosaic" });
    setBusy(false);
    if (a.ok) toast(String(a.message));
  };

  return (
    <div className="mz">
      {!target && <p className="hint">Select a target on the sky map (or search for one): the mosaic is centred on it.</p>}
      <div className="mz-grid">
        <Num label="Columns" value={cols} onChange={(v) => setCols(clampNum(Math.round(v), 1, 8))} min={1} max={8} />
        <Num label="Rows" value={rows} onChange={(v) => setRows(clampNum(Math.round(v), 1, 8))} min={1} max={8} />
        <Num label="Overlap" value={overlap} onChange={(v) => setOverlap(clampNum(Math.round(v), 0, 60))} min={0} max={60} step={5} unit="%" />
        <Num label="Time per frame" value={dwell} onChange={(v) => setDwell(clampNum(Math.round(v), 0, 3600))} min={0} max={3600} step={5} unit="s" />
        <Num label="Frame width" value={fovW} onChange={(v) => setFovW(clampNum(v, 0.2, 150))} min={0.2} max={150} step={0.5} unit="°" />
        <Num label="Frame height" value={fovH} onChange={(v) => setFovH(clampNum(v, 0.2, 150))} min={0.2} max={150} step={0.5} unit="°" />
      </div>
      <label className="mz-check">
        <input type="checkbox" checked={portrait} onChange={(e) => setPortrait(e.target.checked)} /> Portrait (swap width and height)
      </label>
      {error && <p className="hint wiz-problem">{error}</p>}
      {plan && (
        <div className="mz-result">
          <p>
            <b>{plan.panels.length} frames</b> around {plan.centre.name.split(" (")[0]}, covering {plan.coverage.w.toFixed(0)}° × {plan.coverage.h.toFixed(0)}°, about {formatDuration(plan.total_s)} in all.
          </p>
          {plan.unreachable > 0 && (
            <p className="mz-warn">
              {plan.unreachable} frame{plan.unreachable > 1 ? "s" : ""} (red on the map) cannot be reached right now:{" "}
              {[...new Set(plan.panels.flatMap((p) => p.problems))].join(", ")}. {plan.items.length > 0 ? "They are left out of the session." : ""}
            </p>
          )}
          <button className="primary" disabled={!ctl || busy || plan.items.length === 0} onClick={add} title={ctl ? undefined : "Take control to use the session"}>
            Add {plan.items.length} frame{plan.items.length === 1 ? "" : "s"} to the session
          </button>
          <small className="pv-note">{plan.estimate}</small>
        </div>
      )}
    </div>
  );
}

function TimelapseTab({ target }: { target: string | null }) {
  const ctl = useStore((s) => isController(s));
  const epoch = useStore((s) => s.clock?.epoch);
  const [minutes, setMinutes] = useState(60);
  const [interval, setIntervalS] = useState(10);
  const [plan, setPlan] = useState<TimelapsePlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPlan(null);
    setError(null);
    if (!target) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`/api/timelapse?id=${encodeURIComponent(target)}&t=${simNow()}&minutes=${minutes}&interval_s=${interval}`, { signal: abort.signal })
        .then(async (r) => {
          if (!r.ok) throw new Error((await r.json()).detail ?? "could not plan the timelapse");
          return r.json() as Promise<TimelapsePlan>;
        })
        .then(setPlan)
        .catch((e) => e.name !== "AbortError" && setError(String(e.message ?? e)));
    }, 350);
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [target, minutes, interval, epoch]);

  return (
    <div className="mz">
      {!target && <p className="hint">Select a target on the sky map: the mount holds it for the run.</p>}
      <div className="mz-grid">
        <Num label="Duration" value={minutes} onChange={(v) => setMinutes(clampNum(Math.round(v), 1, 720))} min={1} max={720} step={5} unit="min" />
        <Num label="Interval" value={interval} onChange={(v) => setIntervalS(clampNum(v, 0.5, 3600))} min={0.5} max={3600} step={1} unit="s" />
      </div>
      {error && <p className="hint wiz-problem">{error}</p>}
      {plan && (
        <div className="mz-result">
          <p>
            <b>{plan.frames} frames</b> from {plan.target.name.split(" (")[0]}: a {formatDuration(plan.playback_s)} clip at 25 frames/s.
          </p>
          <dl className="grid2">
            <dt>Altitude</dt>
            <dd>
              {plan.alt.start.toFixed(0)}° → {plan.alt.end.toFixed(0)}°
            </dd>
            <dt>Azimuth</dt>
            <dd>
              {plan.az.start.toFixed(0)}° → {plan.az.end.toFixed(0)}° ({plan.az.swept.toFixed(0)}° swept)
            </dd>
            <dt>Field rotation</dt>
            <dd title="An alt-az mount cannot avoid it: the frame turns about its centre as the sky turns">
              {rotationText(plan.field_rotation_deg)} in all, {rotationText(plan.field_rotation_per_frame_deg)} per frame
            </dd>
            <dt>Star motion</dt>
            <dd>{plan.star_motion_per_frame_arcsec.toFixed(0)}″ per frame if the mount did not track</dd>
            <dt>Sky</dt>
            <dd>
              {plan.dark ? "dark all along" : "not fully dark (Sun above −12° at some point)"}
              {plan.moon.separation_deg != null ? ` · Moon ${(plan.moon.illuminated * 100).toFixed(0)} % lit, ${plan.moon.separation_deg.toFixed(0)}° away` : ""}
            </dd>
          </dl>
          {plan.sets_in_s != null && <p className="mz-warn">The target sets after {formatDuration(plan.sets_in_s)}: the run would stop there.</p>}
          {plan.cable_limit_in_s != null && <p className="mz-warn">Tracking would reach the cable-wrap limit after {formatDuration(plan.cable_limit_in_s)}.</p>}
          <button
            className="primary"
            disabled={!ctl}
            onClick={() => command("queue_add", { target: target!, duration_s: plan.held_for_s ?? plan.minutes * 60, label: `Timelapse: ${plan.target.name.split(" (")[0]}` }).then((a) => a.ok && toast(String(a.message)))}
            title={ctl ? undefined : "Take control to use the session"}
          >
            Hold it for {formatDuration(plan.held_for_s ?? plan.minutes * 60)} in the session
          </button>
          <small className="pv-note">{plan.estimate} Set your camera or phone app to the interval above.</small>
        </div>
      )}
    </div>
  );
}

export function MosaicPanel() {
  const selected = useStore((s) => s.selectedId);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"mosaic" | "timelapse">("mosaic");
  const target = selected && !selected.startsWith("altaz:") ? selected : null;
  return (
    <Section id="mosaic" title="Mosaic & timelapse" summary={tab === "mosaic" ? "frames" : "hold a field"} onOpenChange={setOpen}>
      <div className="mz-tabs" role="tablist" aria-label="Planner">
        {(["mosaic", "timelapse"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "sel" : ""} onClick={() => setTab(t)}>
            {t === "mosaic" ? "Mosaic" : "Timelapse"}
          </button>
        ))}
      </div>
      {open && (tab === "mosaic" ? <MosaicTab target={target} /> : <TimelapseTab target={target} />)}
    </Section>
  );
}
