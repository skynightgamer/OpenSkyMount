import { useEffect, useState } from "react";
import {
  ClockPanel,
  ControlHeader,
  EventLog,
  MotionPanel,
  SearchPanel,
  SettingsPanel,
  TargetPanel,
  Telemetry,
} from "./components/Panels";
import { Icon, Logo } from "./components/Icon";
import { AlignmentPanel } from "./components/Planner";
import { HandController, LogsPanel, OrbitsPanel, SessionPanel } from "./components/Session";
import { useGamepad } from "./gamepad";
import MountView from "./mount/MountView";
import SkyMap, { type Layers } from "./sky/SkyMap";
import { MANUAL_SPEEDS, estopHttp, isController, login, logout, toast, useStore } from "./store";

type Tab = "sky" | "mount" | "control" | "hand";
const TABS: Tab[] = ["sky", "mount", "control", "hand"];
const TAB_LABEL: Record<Tab, string> = { sky: "Sky", mount: "Mount", control: "Control", hand: "Hand" };

/** Initial view: ?view=hand (home-screen shortcut) or the last view used on this device. */
function initialTab(): Tab {
  const q = new URLSearchParams(location.search).get("view");
  if (q && (TABS as string[]).includes(q)) return q as Tab;
  try {
    const t = localStorage.getItem("osm-tab");
    if (t && (TABS as string[]).includes(t)) return t as Tab;
  } catch {
    /* no storage */
  }
  return "sky";
}
const LAYER_LABEL: Record<keyof Layers, string> = {
  constellations: "Constellations",
  labels: "Labels",
  altaz: "Alt-az grid",
  equatorial: "Equatorial grid",
  dsos: "Messier",
  milkyway: "Milky Way",
};

function Login() {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="login">
      <form
        className="login-box"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(await login(pw));
          setBusy(false);
        }}
      >
        <h1>
          <Logo size={24} />
          OpenSkyMount
        </h1>
        <p>This mount is password protected.</p>
        <label>
          <span>Password</span>
          <input type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
        </label>
        {err && <p className="login-error" role="alert">{err}</p>}
        <button className="primary" type="submit" disabled={busy || !pw}>
          {busy ? "Checking…" : "Log in"}
        </button>
        <hr />
        <p className="login-note">Emergency stop works without the password.</p>
        <button
          type="button"
          className="estop"
          onClick={async () => toast((await estopHttp()) ? "Emergency stop sent" : "Emergency stop failed: server not reachable", "error")}
        >
          E-STOP
        </button>
      </form>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.level}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const auth = useStore((s) => s.auth);
  if (auth === "required") return <Login />;
  if (auth === "unknown") return null; // wait for /api/auth so nothing fetches protected data first
  return <Main />;
}

function Main() {
  const [tab, setTabState] = useState<Tab>(initialTab);
  const setTab = (t: Tab) => {
    setTabState(t);
    try {
      localStorage.setItem("osm-tab", t);
    } catch {
      /* per-device preference only */
    }
  };
  const canMove = useStore((s) => isController(s) && !!s.mount && !["emergency_stop", "fault", "disconnected"].includes(s.mount.mode));
  useGamepad(canMove, MANUAL_SPEEDS);
  const [settings, setSettings] = useState(false);
  const [follow, setFollow] = useState(false);
  const [layers, setLayers] = useState<Layers>({ constellations: true, labels: true, altaz: true, equatorial: false, dsos: true, milkyway: true });
  const conn = useStore((s) => s.connection);
  const site = useStore((s) => s.config?.config.observer);
  const caps = useStore((s) => s.config?.capabilities);
  const toasts = useStore((s) => s.toasts);
  const latency = useStore((s) => s.latencyMs);
  const authEnabled = useStore((s) => s.authEnabled);
  const toggle = (k: keyof Layers) => setLayers({ ...layers, [k]: !layers[k] });
  const [night, setNight] = useState(() => {
    try {
      return localStorage.getItem("osm-night") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    // Red-light mode preserves the observer's dark adaptation (applies to canvas and 3D too).
    document.documentElement.classList.toggle("night", night);
    try {
      localStorage.setItem("osm-night", night ? "1" : "0");
    } catch {
      /* per-device preference only */
    }
  }, [night]);

  return (
    <div className={`app tab-${tab}`}>
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span>OpenSkyMount</span>
        </div>
        <div className="site" title="Observer location">
          <Icon name="pin" size={14} />
          <span>{site ? `${site.name} · ${site.latitude.toFixed(3)}°, ${site.longitude.toFixed(3)}°` : "…"}</span>
        </div>
        <div className={`conn conn-${conn}`}>{conn === "open" ? (caps?.simulated ? "Simulator connected" : caps?.name) : conn}
          {conn === "open" && latency != null && <span className="latency">{latency.toFixed(0)} ms</span>}</div>
        <div className="top-actions">
          <button className={`ghost hand-toggle${tab === "hand" ? " sel" : ""}`} onClick={() => setTab(tab === "hand" ? "sky" : "hand")} aria-pressed={tab === "hand"} title="Large hand-controller layout (for phones)">
            <Icon name="remote" />
            <span>Hand controller</span>
          </button>
          <button className={`ghost${night ? " sel" : ""}`} onClick={() => setNight(!night)} aria-pressed={night} title="Red light for use at the telescope">
            <Icon name="moon" />
            <span>Red light</span>
          </button>
          <button className="ghost" onClick={() => setSettings(true)} disabled={!site}>
            <Icon name="sliders" />
            <span>Settings</span>
          </button>
          {authEnabled && (
            <button className="ghost" onClick={() => void logout()}>
              <Icon name="logout" />
              <span>Log out</span>
            </button>
          )}
        </div>
      </header>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "sel" : ""} onClick={() => setTab(t)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </nav>
      <main className="layout">
        <section className="col sky-col">
          <SearchPanel />
          <SkyMap layers={layers} followMount={follow} />
          <div className="layers" role="group" aria-label="Sky map layers">
            {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
              <label key={k} className="chip">
                <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} /> {LAYER_LABEL[k]}
              </label>
            ))}
            <label className="chip follow">
              <input type="checkbox" checked={follow} onChange={() => setFollow(!follow)} /> Follow mount
            </label>
          </div>
        </section>
        <section className="col mount-col">
          <MountView />
          {caps?.simulated && <div className="sim-note">Simulated mount. Real hardware behaviour is not verified.</div>}
        </section>
        <section className="col control-col">
          <ControlHeader />
          <TargetPanel />
          <MotionPanel />
          <SessionPanel />
          <AlignmentPanel />
          <ClockPanel />
          <OrbitsPanel />
          <LogsPanel />
          <EventLog />
        </section>
        <section className="col hand-col">{tab === "hand" && <HandController />}</section>
      </main>
      <Telemetry />
      {settings && <SettingsPanel onClose={() => setSettings(false)} />}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.level}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
