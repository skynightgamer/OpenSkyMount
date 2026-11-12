// Simulator setup guide: a docked, step-by-step walkthrough (control, site, payload, night, first GoTo, stopping).
// Each step checks the live state where it can (e.g. "tracking a target"), so the guide reacts to what the user does
// anywhere in the app, not only to its own buttons.
import { type ReactNode, useEffect, useState } from "react";
import { formatTime } from "../format";
import { canControl, command, getState, isController, requestControl, select, simNow, toast, useStore } from "../store";
import { Icon, type IconName } from "./Icon";

const GUIDE_KEY = "osm-guide";

interface GuideState {
  step: number;
  done: Record<string, boolean>;
  finished: boolean;
}

function loadGuide(): GuideState {
  try {
    const g = JSON.parse(localStorage.getItem(GUIDE_KEY) ?? "{}") as Partial<GuideState>;
    return { step: typeof g.step === "number" ? g.step : 0, done: g.done ?? {}, finished: !!g.finished };
  } catch {
    return { step: 0, done: {}, finished: false };
  }
}

function saveGuide(g: GuideState) {
  try {
    localStorage.setItem(GUIDE_KEY, JSON.stringify(g));
  } catch {
    /* per-device progress only */
  }
}

/** Restart the guide from the first step (used by "Start the setup guide" on the welcome screen). */
export function resetGuide(): void {
  saveGuide({ step: 0, done: {}, finished: false });
}

interface StepProps {
  done: boolean;
  markDone: () => void;
  onOpenSettings: () => void;
}

interface StepDef {
  id: string;
  title: string;
  icon: IconName;
  /** Live completion check (in addition to `markDone`). */
  live?: () => boolean;
  body: (p: StepProps) => ReactNode;
}

function NeedControl() {
  const ctl = useStore((s) => isController(s));
  const allowed = useStore((s) => canControl(s));
  if (ctl) return null;
  return <p className="guide-warn">{allowed ? "Take control first (step 1) to use these buttons." : "Your account is view-only: read along, an operator can do these steps."}</p>;
}

// ------------------------------------------------------------------ steps
function StepControl({ done }: StepProps) {
  const control = useStore((s) => s.control);
  const allowed = useStore((s) => canControl(s));
  const other = control?.controller && !done ? control.controller_name : null;
  return (
    <>
      <p>
        Several phones and computers can watch the mount at once, but only <strong>one device drives it</strong>. The others are
        view only. Everyone can always press <strong>E-STOP</strong>.
      </p>
      {!allowed ? (
        <p className="guide-warn">Your account is an observer (view only). You can follow the guide; ask an admin for operator access to move the mount.</p>
      ) : done ? null : other ? (
        <button onClick={() => confirm(`Take control from ${other}?`) && requestControl(true)}>Take over from {other}</button>
      ) : (
        <button className="primary" onClick={() => requestControl(false)}>
          Take control
        </button>
      )}
      <p className="hint">The control box at the top of the right column shows who is driving and holds the stop buttons.</p>
    </>
  );
}

function StepSite({ done, markDone, onOpenSettings }: StepProps) {
  const site = useStore((s) => s.config?.config.observer);
  const ctl = useStore((s) => isController(s));
  const [busy, setBusy] = useState(false);
  const useMine = () => {
    if (!navigator.geolocation) return toast("This browser cannot share its location", "error");
    setBusy(true);
    navigator.geolocation.getCurrentPosition(
      async (p) => {
        const r = await command("set_config", {
          observer: { latitude: +p.coords.latitude.toFixed(4), longitude: +p.coords.longitude.toFixed(4), elevation_m: Math.round(p.coords.altitude ?? site?.elevation_m ?? 0), name: "My location" },
        });
        setBusy(false);
        if (r.ok) {
          toast("Location saved");
          markDone();
        }
      },
      (e) => {
        setBusy(false);
        toast(`Location unavailable: ${e.message}`, "error");
      },
      { timeout: 15000 },
    );
  };
  return (
    <>
      <p>The sky depends on where you stand. The simulator computes every star, planet and rise time for this site:</p>
      <p className="guide-fact">
        <Icon name="pin" /> {site ? `${site.name} · ${site.latitude.toFixed(3)}°, ${site.longitude.toFixed(3)}° · ${site.elevation_m} m` : "…"}
      </p>
      <div className="guide-actions">
        <button className="primary" onClick={useMine} disabled={!ctl || busy}>
          {busy ? "Locating…" : "Use this device's location"}
        </button>
        <button onClick={onOpenSettings}>Enter coordinates…</button>
        {!done && <button className="linkish" onClick={markDone}>Keep {site?.name ?? "this site"}</button>}
      </div>
      <NeedControl />
    </>
  );
}

function StepPayload({ markDone }: StepProps) {
  const cfg = useStore((s) => s.config);
  const ctl = useStore((s) => isController(s));
  if (!cfg) return null;
  const choose = async (id: string) => {
    const r = await command("set_config", { payload: id });
    if (r.ok) markDone();
  };
  return (
    <>
      <p>
        Pick what sits on the mount. The 3D view, the speed limits and the lowest safe altitude follow the payload, and the
        exposure planner uses its field of view.
      </p>
      <ul className="guide-choices">
        {Object.values(cfg.payloads).map((p) => (
          <li key={p.id}>
            <button className={cfg.config.payload === p.id ? "sel" : ""} disabled={!ctl} onClick={() => void choose(p.id)} aria-pressed={cfg.config.payload === p.id}>
              <strong>{p.name}</strong>
              <small>
                {p.mass_kg} kg · field {p.fov_deg}° · max alt {p.max_altitude}°
              </small>
            </button>
          </li>
        ))}
      </ul>
      <NeedControl />
    </>
  );
}

function useSunAlt(): number | null {
  const epoch = useStore((s) => s.clock?.epoch);
  const [alt, setAlt] = useState<number | null>(null);
  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch(`/api/object?id=sun&t=${simNow()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((o: { alt?: number } | null) => !stop && typeof o?.alt === "number" && setAlt(o.alt))
        .catch(() => {});
    load();
    const id = window.setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [epoch]);
  return alt;
}

function StepNight({ done, markDone }: StepProps) {
  const sunAlt = useSunAlt();
  const ctl = useStore((s) => isController(s));
  const warp = useStore((s) => s.config?.capabilities.supports_time_warp ?? false);
  const live = useStore((s) => s.clock?.live ?? true);
  const tz = useStore((s) => s.prefs.timeZone);
  const [busy, setBusy] = useState(false);
  const dark = sunAlt != null && sunAlt < -12;
  useEffect(() => {
    if (dark && !done) markDone();
  }, [dark]);
  const jump = async () => {
    setBusy(true);
    try {
      const r = await fetch(`/api/tonight?t=${simNow()}&limit=1`);
      const d = (await r.json()) as { window: { start: number; end: number } | null; message?: string };
      if (!d.window) {
        toast(d.message ?? "No dark night here in the next 36 hours", "error");
        return;
      }
      const t = Math.max(simNow(), d.window.start + 45 * 60);
      const a = await command("set_clock", { time: t });
      if (a.ok) toast(`Simulated clock set to ${formatTime(t, tz)}`);
    } catch {
      toast("Could not reach the server", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p>
        Stars only show once the Sun is well below the horizon. The simulator has its own clock: you can jump to tonight and even
        speed time up. Hardware drivers always run in real time.
      </p>
      <p className="guide-fact">
        <Icon name="moon" /> {sunAlt == null ? "Checking the Sun…" : dark ? `Dark sky: the Sun is ${Math.abs(sunAlt).toFixed(0)}° below the horizon.` : sunAlt > 0 ? `Daytime: the Sun is ${sunAlt.toFixed(0)}° up.` : `Twilight: the Sun is ${Math.abs(sunAlt).toFixed(0)}° below the horizon.`}
        {!live && " (simulated time)"}
      </p>
      {!dark && (
        <div className="guide-actions">
          <button className="primary" onClick={jump} disabled={!ctl || !warp || busy}>
            {busy ? "Jumping…" : "Jump to tonight"}
          </button>
          <button className="linkish" onClick={markDone}>
            Stay in daylight
          </button>
        </div>
      )}
      {!warp && <p className="hint">Time jumps are only available with the simulated mount.</p>}
      <NeedControl />
    </>
  );
}

interface TonightTarget {
  id: string;
  name: string;
  type: string;
  kind: string;
  magnitude: number | null;
  max_alt: number;
  above_from: number;
  above_until: number;
}

function StepGoto({ done }: StepProps) {
  const ctl = useStore((s) => isController(s));
  const m = useStore((s) => s.mount);
  const epoch = useStore((s) => s.clock?.epoch);
  const [targets, setTargets] = useState<TonightTarget[] | null>(null);
  useEffect(() => {
    let stop = false;
    const now = simNow();
    fetch(`/api/tonight?t=${now}&limit=40&min_alt=25`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { targets: TonightTarget[] } | null) => {
        if (stop || !d) return;
        // Up now, brightest-ranked first.
        setTargets(d.targets.filter((t) => t.above_from <= now + 60 && t.above_until >= now + 600).slice(0, 4));
      })
      .catch(() => !stop && setTargets([]));
    return () => {
      stop = true;
    };
  }, [epoch]);
  const go = async (t: TonightTarget) => {
    select(t.id, true);
    const a = await command("goto", { target: t.id, track: true });
    if (a.ok) toast(`Slewing to ${t.name}`);
  };
  return (
    <>
      <p>
        Pick something that is up right now. The mount slews there (watch the 3D view and the dashed line on the sky map) and
        then <strong>tracks</strong> it, turning both axes to cancel the Earth's rotation.
      </p>
      {targets == null ? (
        <p className="hint">Finding good targets…</p>
      ) : targets.length === 0 ? (
        <p className="guide-warn">Nothing bright is high enough right now. Go back a step and jump to tonight, or search the sky map for any object.</p>
      ) : (
        <ul className="guide-choices">
          {targets.map((t) => (
            <li key={t.id}>
              <button disabled={!ctl} onClick={() => void go(t)} className={m?.target?.id === t.id ? "sel" : ""}>
                <strong>{t.name}</strong>
                <small>
                  {t.type}
                  {t.magnitude != null ? ` · mag ${t.magnitude.toFixed(1)}` : ""} · peaks at {t.max_alt.toFixed(0)}°
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {m?.target && (
        <p className="guide-fact">
          <Icon name="target" /> {m.mode === "slewing" ? `Slewing to ${m.target.name}…` : m.mode === "tracking" ? `Tracking ${m.target.name}` : `${m.target.name} (${m.mode})`}
          {done && " ✓"}
        </p>
      )}
      <NeedControl />
    </>
  );
}

function StepSafety({ done, markDone }: StepProps) {
  const mode = useStore((s) => s.mount?.mode);
  const ctl = useStore((s) => isController(s));
  return (
    <>
      <ul className="guide-list">
        <li>
          <strong>Stop</strong> brakes smoothly and ends GoTo, tracking and the session.
        </li>
        <li>
          <strong>E-STOP</strong> halts both axes at once from any device and blocks motion until the controlling device presses
          Reset. A real mount also needs a hardware stop switch.
        </li>
        <li>The manual pad only moves while you hold a button; letting go (or losing the connection) stops it.</li>
        <li>GoTo refuses the Sun and anything below the minimum altitude.</li>
      </ul>
      <div className="guide-actions">
        <button
          className="stop"
          onClick={async () => {
            const a = await command("stop");
            if (a.ok) markDone();
          }}
        >
          Try Stop now
        </button>
        {mode === "emergency_stop" && (
          <button className="warnbtn" disabled={!ctl} onClick={() => command("reset")}>
            Reset after E-STOP
          </button>
        )}
        {!done && (
          <button className="linkish" onClick={markDone}>
            Got it
          </button>
        )}
      </div>
    </>
  );
}

function StepNext({ markDone }: StepProps) {
  useEffect(markDone, []);
  return (
    <>
      <p>The simulated mount is set up. Ideas for what to try next:</p>
      <ul className="guide-list">
        <li>
          <strong>Observing session</strong>: queue several targets with times and let the mount work through them.
        </li>
        <li>
          <strong>Alignment</strong>: add simulated mount errors in Settings, then Sync or plate-solve to correct them.
        </li>
        <li>
          <strong>Hand controller</strong>: a large layout for a phone at the telescope.
        </li>
        <li>
          <strong>Recordings</strong>: record telemetry and replay the tracking error.
        </li>
        <li>
          <strong>Accounts</strong>: invite others as operators or observers from the account menu.
        </li>
      </ul>
    </>
  );
}

const STEPS: StepDef[] = [
  { id: "control", title: "Take control", icon: "remote", live: () => isController(getState()), body: (p) => <StepControl {...p} /> },
  { id: "site", title: "Where are you observing?", icon: "pin", body: (p) => <StepSite {...p} /> },
  { id: "payload", title: "Choose a payload", icon: "target", body: (p) => <StepPayload {...p} /> },
  { id: "night", title: "Make it night", icon: "moon", body: (p) => <StepNight {...p} /> },
  { id: "goto", title: "Your first GoTo", icon: "compass", live: () => getState().mount?.mode === "tracking" && !!getState().mount?.target, body: (p) => <StepGoto {...p} /> },
  { id: "safety", title: "Stopping safely", icon: "shield", body: (p) => <StepSafety {...p} /> },
  { id: "next", title: "You're set", icon: "sparkle", body: (p) => <StepNext {...p} /> },
];

export function SetupGuide({ onClose, onOpenSettings }: { onClose: () => void; onOpenSettings: () => void }) {
  const [g, setG] = useState<GuideState>(loadGuide);
  const [mini, setMini] = useState(false);
  // Re-render on store changes so live checks (control, tracking) update.
  useStore((s) => `${isController(s)}|${s.mount?.mode}|${s.mount?.target?.id}`);
  const step = Math.min(STEPS.length - 1, Math.max(0, g.step));
  const def = STEPS[step];
  const isDone = (i: number) => !!g.done[STEPS[i].id] || !!STEPS[i].live?.();
  const update = (patch: Partial<GuideState>) => {
    const next = { ...g, ...patch };
    setG(next);
    saveGuide(next);
  };
  const markDone = () => {
    if (!g.done[def.id]) update({ done: { ...g.done, [def.id]: true } });
  };
  const done = isDone(step);
  const last = step === STEPS.length - 1;
  const count = STEPS.filter((_, i) => isDone(i)).length;
  if (mini)
    return (
      <button className="guide-pill" onClick={() => setMini(false)} aria-label="Open the setup guide">
        <Icon name="compass" /> Setup guide · {step + 1}/{STEPS.length}: {def.title}
      </button>
    );
  return (
    <aside className="guide" aria-label="Simulator setup guide">
      <header>
        <Icon name="compass" />
        <strong>Simulator setup</strong>
        <span className="guide-count">
          {count}/{STEPS.length} done
        </span>
        <button className="ghost icon-btn" onClick={() => setMini(true)} aria-label="Minimise guide" title="Minimise">
          <Icon name="down" />
        </button>
        <button className="ghost icon-btn" onClick={onClose} aria-label="Close guide" title="Close (reopen from the account menu)">
          <Icon name="close" />
        </button>
      </header>
      <ol className="guide-steps">
        {STEPS.map((s, i) => (
          <li key={s.id}>
            <button
              className={`${i === step ? "cur" : ""}${isDone(i) ? " done" : ""}`}
              onClick={() => update({ step: i })}
              aria-current={i === step ? "step" : undefined}
              aria-label={`Step ${i + 1}: ${s.title}${isDone(i) ? " (done)" : ""}`}
              title={s.title}
            />
          </li>
        ))}
      </ol>
      <div className="guide-body">
        <h3>
          <span className={`guide-icon${done ? " done" : ""}`}>
            <Icon name={done ? "check" : def.icon} size={15} />
          </span>
          <span>
            <small>Step {step + 1} of {STEPS.length}</small>
            {def.title}
          </span>
        </h3>
        {def.body({ done, markDone, onOpenSettings })}
      </div>
      <footer>
        <button onClick={() => update({ step: step - 1 })} disabled={step === 0}>
          Back
        </button>
        {last ? (
          <button
            className="primary"
            onClick={() => {
              update({ finished: true });
              onClose();
            }}
          >
            Finish
          </button>
        ) : (
          <button className={done ? "primary" : ""} onClick={() => update({ step: step + 1 })}>
            {done ? "Next" : "Skip"}
          </button>
        )}
      </footer>
    </aside>
  );
}
