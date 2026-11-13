import { useEffect, useState } from "react";
import {
  ClockPanel,
  ControlHeader,
  EventLog,
  MotionPanel,
  SearchPanel,
  TargetPanel,
  Telemetry,
} from "./components/Panels";
import { AccountDialog, AccountMenu, LoginScreen, type AccountTab } from "./components/Account";
import { SettingsPanel, type SettingsTab } from "./components/Settings";
import { SetupGuide, resetGuide } from "./components/Guide";
import { Icon, Logo } from "./components/Icon";
import { WelcomeScreen, markWelcomeSeen, welcomeDue } from "./components/Welcome";
import { AlignmentPanel } from "./components/Planner";
import { HandController, LogsPanel, OrbitsPanel, SessionPanel } from "./components/Session";
import { useGamepad } from "./gamepad";
import { usePresence } from "./presence";
import MountView from "./mount/MountView";
import SkyMap, { type Layers } from "./sky/SkyMap";
import { MANUAL_SPEEDS, isController, useStore } from "./store";

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
  figures: "Figures",
  labels: "Labels",
  altaz: "Alt-az grid",
  equatorial: "Equatorial grid",
  dsos: "Messier",
  milkyway: "Milky Way",
  ecliptic: "Ecliptic",
  landscape: "Landscape",
};
const DEFAULT_LAYERS: Layers = { constellations: true, figures: true, labels: true, altaz: true, equatorial: false, dsos: true, milkyway: true, ecliptic: false, landscape: true };

function loadLayers(): Layers {
  try {
    const saved = JSON.parse(localStorage.getItem("osm-layers") ?? "{}") as Partial<Layers>;
    const out = { ...DEFAULT_LAYERS };
    for (const k of Object.keys(out) as (keyof Layers)[]) if (typeof saved[k] === "boolean") out[k] = saved[k]!;
    return out;
  } catch {
    return { ...DEFAULT_LAYERS };
  }
}

function flag(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function setFlag(key: string, on: boolean): void {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    /* per-device preference only */
  }
}

export default function App() {
  const auth = useStore((s) => s.auth);
  if (auth === "required") return <LoginScreen />;
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
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({ open: false, tab: "display" });
  const openSettings = (t: SettingsTab = "display") => setSettings({ open: true, tab: t });
  const closeSettings = () => setSettings((s) => ({ ...s, open: false }));
  const [follow, setFollow] = useState(false);
  const [layers, setLayersState] = useState<Layers>(loadLayers);
  const setLayers = (l: Layers) => {
    setLayersState(l);
    try {
      localStorage.setItem("osm-layers", JSON.stringify(l));
    } catch {
      /* per-device preference only */
    }
  };
  const [welcome, setWelcome] = useState(welcomeDue);
  const welcomePresence = usePresence(welcome, 320);
  const [guide, setGuideState] = useState(() => flag("osm-guide-open"));
  const setGuide = (on: boolean) => {
    setGuideState(on);
    setFlag("osm-guide-open", on);
  };
  const [account, setAccount] = useState<AccountTab | null>(null);
  const closeWelcome = () => {
    markWelcomeSeen();
    setWelcome(false);
  };
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
          <button className={`ghost${night ? " sel" : ""}`} onClick={() => setNight(!night)} aria-pressed={night} title="Night mode for use at the telescope">
            <Icon name="moon" />
            <span>Night mode</span>
          </button>
          <button
            className={`ghost${settings.open ? " sel" : ""}`}
            onClick={() => (settings.open ? closeSettings() : openSettings())}
            disabled={!site}
            aria-haspopup="dialog"
            aria-expanded={settings.open}
          >
            <Icon name="sliders" />
            <span>Settings</span>
          </button>
          <AccountMenu onAccount={setAccount} onWelcome={() => setWelcome(true)} onGuide={() => setGuide(true)} />
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
          <div className="layers">
            <div className="layer-chips" role="group" aria-label="Sky map layers">
              {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
                <label key={k} className="chip">
                  <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} /> {LAYER_LABEL[k]}
                </label>
              ))}
            </div>
            <label className="chip follow" title="Keep the mount's position in the centre of the map">
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
      {guide && !welcome && <SetupGuide onClose={() => setGuide(false)} onOpenSettings={() => openSettings("location")} />}
      <SettingsPanel open={settings.open} tab={settings.tab} onClose={closeSettings} />
      {account && <AccountDialog tab={account} onClose={() => setAccount(null)} />}
      {welcomePresence.mounted && (
        <WelcomeScreen
          closing={welcomePresence.closing}
          onClose={closeWelcome}
          onStartGuide={() => {
            closeWelcome();
            resetGuide();
            setGuide(true);
          }}
          onCreateAccount={() => {
            closeWelcome();
            setAccount("setup");
          }}
        />
      )}
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
