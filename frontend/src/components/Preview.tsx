// Slew preview inside the Target panel: how long a GoTo would take, which way the axes turn and where the cable wrap
// ends up, from GET /api/slew_preview. Read-only: nothing is sent to the mount. The path is also drawn on the sky map.
import { useEffect, useState } from "react";
import { cableBar, formatDuration, signedDeg, type SlewPlan } from "../preview";
import { setSlewPath, useStore } from "../store";

function CableWrap({ plan }: { plan: SlewPlan }) {
  const c = plan.cable!;
  const bar = cableBar(c);
  const label = `Cable wrap: ${signedDeg(c.now)} now, ${signedDeg(c.after)} after the GoTo, limits ${signedDeg(c.az_min)} to ${signedDeg(c.az_max)}`;
  return (
    <div className="cable" role="img" aria-label={label} title={label}>
      <div className="cable-track">
        <span className="cable-zone" style={{ left: 0, width: `${bar.zone}%` }} />
        <span className="cable-zone" style={{ right: 0, width: `${bar.zone}%` }} />
        <span className="cable-neutral" style={{ left: `${bar.neutral}%` }} />
        <span className="cable-mark now" style={{ left: `${bar.now}%` }} />
        <span className={`cable-mark after${c.long_way ? " long" : ""}`} style={{ left: `${bar.after}%` }} />
      </div>
      <div className="cable-scale">
        <span>{signedDeg(c.az_min)}</span>
        <span>neutral</span>
        <span>{signedDeg(c.az_max)}</span>
      </div>
      <div className="cable-key">
        <span><i className="cable-dot now" /> now {signedDeg(c.now)}</span>
        <span><i className="cable-dot after" /> after {signedDeg(c.after)}</span>
      </div>
    </div>
  );
}

export function SlewPreview({ id }: { id: string }) {
  const mode = useStore((s) => s.mount?.mode);
  const followed = useStore((s) => s.mount?.target?.id);
  const epoch = useStore((s) => s.clock?.epoch);
  const [plan, setPlan] = useState<SlewPlan | null>(null);
  const busy = followed === id && (mode === "slewing" || mode === "tracking"); // the mount is already doing it

  useEffect(() => {
    setPlan(null);
    if (busy) {
      setSlewPath(null);
      return;
    }
    const abort = new AbortController();
    const load = () =>
      fetch(`/api/slew_preview?id=${encodeURIComponent(id)}`, { signal: abort.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((p: SlewPlan | null) => {
          setPlan(p);
          setSlewPath(p?.path && !p.refused ? { id, path: p.path } : null);
        })
        .catch(() => {});
    load();
    // The plan depends on where the mount is and where the target is: refresh while it is idle.
    const timer = window.setInterval(load, 5000);
    return () => {
      abort.abort();
      window.clearInterval(timer);
      setSlewPath(null);
    };
  }, [id, busy, mode, epoch]);

  if (busy || !plan) return null;
  if (plan.refused) {
    return (
      <div className="preview refused">
        <strong>GoTo refused</strong>
        <small>{plan.refused}</small>
      </div>
    );
  }
  const d = plan.delta!;
  return (
    <div className="preview">
      <div className="pv-head">
        <strong>Slew preview</strong>
        <span className="num" title={plan.estimate}>≈ {formatDuration(plan.duration_s ?? 0)}</span>
      </div>
      <p className="pv-line">
        {plan.separation_deg!.toFixed(0)}° away · altitude {plan.turn!.alt === "none" ? "unchanged" : `${Math.abs(d.alt).toFixed(0)}° ${plan.turn!.alt}`} · azimuth {plan.turn!.az}
      </p>
      <CableWrap plan={plan} />
      {plan.warnings.length > 0 && (
        <ul className="pv-warn">
          {plan.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <small className="pv-note">Estimate from the configured axis limits; not measured on hardware.</small>
    </div>
  );
}
