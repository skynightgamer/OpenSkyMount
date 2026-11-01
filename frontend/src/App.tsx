import { useEffect, useState } from "react";
import {
  ClockPanel,
  ControlAuthority,
  EventLog,
  MotionPanel,
  SearchPanel,
  SettingsPanel,
  TargetPanel,
  Telemetry,
} from "./components/Panels";
import { AlignmentPanel } from "./components/Planner";
import MountView from "./mount/MountView";
import SkyMap, { type Layers } from "./sky/SkyMap";
import { useStore } from "./store";

type Tab = "sky" | "mount" | "control";
const LAYER_LABEL: Record<keyof Layers, string> = {
  constellations: "Constellations",
  labels: "Labels",
  altaz: "Alt-az grid",
  equatorial: "Equatorial grid",
  dsos: "Messier",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("sky");
  const [settings, setSettings] = useState(false);
  const [follow, setFollow] = useState(false);
  const [layers, setLayers] = useState<Layers>({ constellations: true, labels: true, altaz: true, equatorial: false, dsos: true });
  const conn = useStore((s) => s.connection);
  const site = useStore((s) => s.config?.config.observer);
  const caps = useStore((s) => s.config?.capabilities);
  const toasts = useStore((s) => s.toasts);
  const latency = useStore((s) => s.latencyMs);
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
        <div className="brand">OpenSkyMount</div>
        <div className="site" title="Observer location">
          {site ? `${site.name} · ${site.latitude.toFixed(3)}°, ${site.longitude.toFixed(3)}°` : "…"}
        </div>
        <div className={`conn conn-${conn}`}>{conn === "open" ? (caps?.simulated ? "Simulator connected" : caps?.name) : conn}
          {conn === "open" && latency != null && <span className="latency">{latency.toFixed(0)} ms</span>}</div>
        <button className={night ? "sel" : ""} onClick={() => setNight(!night)} aria-pressed={night} title="Red light for use at the telescope">
          Red light
        </button>
        <button onClick={() => setSettings(true)} disabled={!site}>
          Settings
        </button>
      </header>
      <nav className="tabs" role="tablist">
        {(["sky", "mount", "control"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "sel" : ""} onClick={() => setTab(t)}>
            {t === "sky" ? "Sky" : t === "mount" ? "Mount" : "Control"}
          </button>
        ))}
      </nav>
      <main className="layout">
        <section className="col sky-col">
          <SearchPanel />
          <SkyMap layers={layers} followMount={follow} />
          <div className="layers">
            {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
              <label key={k}>
                <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} /> {LAYER_LABEL[k]}
              </label>
            ))}
            <label>
              <input type="checkbox" checked={follow} onChange={() => setFollow(!follow)} /> Follow mount
            </label>
          </div>
        </section>
        <section className="col mount-col">
          <MountView />
          {caps?.simulated && <div className="sim-note">Simulated mount. Real hardware behaviour is not verified.</div>}
        </section>
        <section className="col control-col">
          <ControlAuthority />
          <TargetPanel />
          <MotionPanel />
          <AlignmentPanel />
          <ClockPanel />
          <EventLog />
        </section>
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
