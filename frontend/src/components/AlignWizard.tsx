// Guided star alignment, inside the Alignment section: the server suggests bright stars that are far apart and spread
// around the sky (GET /api/align_stars); for each one the person sends the mount there, centres the star and presses
// Sync. The wizard follows the pointing model, so progress, the final quality and the point to redo come from the same
// numbers the rest of the app shows.
import { useEffect, useRef, useState } from "react";
import { type AlignSuggestion, arcText, centringOffset, nextStarIndex, suspectPoint } from "../align";
import { command, isController, select, toast, useStore } from "../store";

export function AlignWizard() {
  const mount = useStore((s) => s.mount);
  const ctl = useStore((s) => isController(s));
  const simulated = useStore((s) => !!s.config?.capabilities.simulated);
  const [phase, setPhase] = useState<"idle" | "loading" | "running" | "done">("idle");
  const [count, setCount] = useState(3);
  const [sug, setSug] = useState<AlignSuggestion | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const model = mount?.pointing_model;
  const points = model?.points ?? [];
  const idx = sug ? nextStarIndex(sug.stars, points) : -1;
  const cur = sug && idx >= 0 ? sug.stars[idx] : null;

  // Finished when every star has its point; back to running when one of them is removed to be redone.
  useEffect(() => {
    if (phase === "running" && sug && sug.stars.length > 0 && idx === -1) setPhase("done");
    if (phase === "done" && idx !== -1) setPhase("running");
  }, [phase, sug, idx]);

  // Show each star on the sky map when its turn comes.
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (phase === "running" && cur && shown.current !== cur.id) {
      shown.current = cur.id;
      select(cur.id, true);
    }
    if (phase !== "running") shown.current = null;
  }, [phase, cur]);

  const start = async () => {
    setPhase("loading");
    setProblem(null);
    try {
      const r = await fetch(`/api/align_stars?count=${count}`);
      const d: AlignSuggestion = await r.json();
      if (!r.ok || d.stars.length === 0) {
        setProblem(d.message ?? "No alignment stars could be chosen.");
        setPhase("idle");
        return;
      }
      setSug(d);
      setPhase("running");
    } catch {
      setProblem("The server could not be reached.");
      setPhase("idle");
    }
  };
  const stop = () => {
    setPhase("idle");
    setSug(null);
  };

  if (phase === "idle" || phase === "loading") {
    return (
      <div className="wiz">
        <div className="wiz-start">
          <button className="primary" disabled={!ctl || phase === "loading"} onClick={start} title={ctl ? undefined : "Take control to align the mount"}>
            {phase === "loading" ? "Choosing stars…" : "Align with stars (guided)"}
          </button>
          <label>
            Stars{" "}
            <select value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="Number of alignment stars">
              {[2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                  {n === 3 ? " (recommended)" : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
        {problem && <p className="hint wiz-problem">{problem}</p>}
        <p className="hint">Picks bright stars far apart and spread around the sky, so the base tilt can be measured as well as the zero offsets.</p>
      </div>
    );
  }

  const stars = sug!.stars;
  if (phase === "done") {
    const bad = suspectPoint(points);
    const badStar = bad >= 0 && stars.some((s) => s.name === points[bad].label) ? points[bad] : null;
    return (
      <div className="wiz">
        <h4>Alignment finished</h4>
        <dl className="grid2">
          <dt>Points</dt>
          <dd>{points.length}</dd>
          <dt>Residual RMS</dt>
          <dd>{arcText(model?.rms)}</dd>
          <dt>Zero offsets alt / az</dt>
          <dd>
            {model!.alt_off >= 0 ? "+" : ""}
            {model!.alt_off.toFixed(3)}° / {model!.az_off >= 0 ? "+" : ""}
            {model!.az_off.toFixed(3)}°
          </dd>
          <dt>Base tilt</dt>
          <dd>{points.length >= model!.tilt_min_points ? `${model!.tilt.toFixed(3)}°` : "needs 3 stars"}</dd>
        </dl>
        {badStar ? (
          <p className="wiz-bad">
            {badStar.label} is {arcText(badStar.residual)} off while the others agree to a few arc minutes: it was probably not centred well.
            <button disabled={!ctl} onClick={() => command("align_remove", { index: bad })}>
              Redo {badStar.label}
            </button>
          </p>
        ) : (
          <p className="hint">The points agree with each other{points.length < 3 ? "; use three or more stars to check the fit and measure the tilt" : ""}.</p>
        )}
        <div className="row">
          <button onClick={stop}>Finish</button>
        </div>
      </div>
    );
  }

  // running
  const target = mount?.target;
  const here = !!cur && target?.id === cur.id;
  const mode = mount?.mode;
  const slewing = here && mode === "slewing";
  const canSync = ctl && here && !slewing && (mode === "tracking" || mode === "manual" || mode === "idle");
  const offset = simulated && here ? centringOffset(mount?.driver.truth ?? null, target ?? null) : null;
  return (
    <div className="wiz">
      <div className="wiz-head">
        <h4>
          Star {idx + 1} of {stars.length}
        </h4>
        <button className="ghost" onClick={stop}>
          Cancel
        </button>
      </div>
      {sug && !sug.dark && (
        <p className="hint wiz-problem">
          The Sun is {sug.sun_alt.toFixed(0)}° high: real stars are not visible yet. In the simulator, jump to a dark time in the Time section.
        </p>
      )}
      <ol className="wiz-stars">
        {stars.map((s, i) => {
          const done = points.some((p) => p.label === s.name);
          return (
            <li key={s.id} className={done ? "done" : i === idx ? "now" : ""}>
              <span className="wiz-mark">{done ? "✓" : i === idx ? "→" : ""}</span>
              <span>
                {s.name} <small>mag {s.magnitude.toFixed(1)} · alt {s.alt.toFixed(0)}° · az {s.az.toFixed(0)}°</small>
              </span>
            </li>
          );
        })}
      </ol>
      {cur && (
        <div className="wiz-step">
          <p>
            <b>1.</b> Send the mount to {cur.name}.
          </p>
          <button className="primary" disabled={!ctl || slewing} onClick={() => command("goto", { target: cur.id, track: true })}>
            {slewing ? "Slewing…" : here ? "Go there again" : `Go to ${cur.name.split(" (")[0]}`}
          </button>
          <p>
            <b>2.</b> Centre the star in the payload with the Move pad (slowest speed, 0.05°/s, is best).
          </p>
          {offset && (
            <p className="hint" title="Only the simulator knows where it really points; a camera image would show you this.">
              Simulator: the star is {offset.total.toFixed(1)}′ from the centre (altitude {offset.alt >= 0 ? "+" : ""}
              {offset.alt.toFixed(1)}′, azimuth {offset.az >= 0 ? "+" : ""}
              {offset.az.toFixed(1)}′ on the sky).
            </p>
          )}
          <p>
            <b>3.</b> When it is centred, press Sync.
          </p>
          <button
            disabled={!canSync}
            onClick={() => command("sync", { target: cur.id }).then((a) => a.ok && toast(String(a.message)))}
            title={here ? "Declare that the payload points at this star" : "Send the mount to the star first"}
          >
            Sync: {cur.name.split(" (")[0]} is centred
          </button>
          {!ctl && <p className="hint">Take control to move the mount.</p>}
        </div>
      )}
    </div>
  );
}
