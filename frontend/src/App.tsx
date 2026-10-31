import { useState } from "react";
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
import MountView from "./mount/MountView";
import SkyMap, { type Layers } from "./sky/SkyMap";
import { useStore } from "./store";

type Tab = "sky" | "mount" | "control";

export default function App() {
  const [tab, setTab] = useState<Tab>("sky");
  const [settings, setSettings] = useState(false);
  const [follow, setFollow] = useState(false);
  const [layers, setLayers] = useState<Layers>({ constellations: true, labels: true, altaz: true, equatorial: false, dsos: true });
  const conn = useStore((s) => s.connection);
  const site = useStore((s) => s.config?.config.observer);
  const caps = useStore((s) => s.config?.capabilities);
  const toasts = useStore((s) => s.toasts);
  const toggle = (k: keyof Layers) => setLayers({ ...layers, [k]: !layers[k] });

  return (
    <div className={`app tab-${tab}`}>
      <header className="topbar">
        <div className="brand">
          <span className="logo">◎</span> OpenSkyMount
        </div>
        <div className="site" title="Observer location">
          {site ? `${site.name} · ${site.latitude.toFixed(3)}°, ${site.longitude.toFixed(3)}°` : "…"}
        </div>
        <div className={`conn conn-${conn}`}>{conn === "open" ? (caps?.simulated ? "Simulator connected" : caps?.name) : conn}</div>
        <button className="icon" onClick={() => setSettings(true)} disabled={!site} aria-label="Settings">
          ⚙
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
                <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} /> {k === "altaz" ? "alt-az grid" : k === "dsos" ? "Messier" : k}
              </label>
            ))}
            <label>
              <input type="checkbox" checked={follow} onChange={() => setFollow(!follow)} /> follow mount
            </label>
          </div>
        </section>
        <section className="col mount-col">
          <MountView />
          {caps?.simulated && <div className="sim-note">Simulated mount — behaviour of real hardware is not verified.</div>}
        </section>
        <section className="col control-col">
          <ControlAuthority />
          <TargetPanel />
          <MotionPanel />
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
