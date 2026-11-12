// Settings sheet: drops from the top bar like the account menu (enter and exit animation, see presence.ts).
// Categories on the left; per-device display options apply at once, mount settings are saved together with Save
// (they need control authority). The Connections page shows how Alpaca programs reach this server.
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { resolveZone, timeZones, zoneLabel } from "../format";
import { usePresence } from "../presence";
import { command, getState, isController, setPrefs, simNow, toast, useStore } from "../store";
import { Icon, type IconName } from "./Icon";

export type SettingsTab = "display" | "location" | "mount" | "simulator" | "connections";

const TAB_INFO: Record<SettingsTab, { label: string; icon: IconName }> = {
  display: { label: "Display", icon: "eye" },
  location: { label: "Location", icon: "pin" },
  mount: { label: "Mount & safety", icon: "shield" },
  simulator: { label: "Simulator", icon: "flask" },
  connections: { label: "Connections", icon: "plug" },
};

const EXIT_MS = 160;

export function SettingsPanel({ open, tab, onClose }: { open: boolean; tab: SettingsTab; onClose: () => void }) {
  const p = usePresence(open, EXIT_MS);
  const ready = useStore((s) => !!s.config);
  if (!p.mounted || !ready) return null;
  const state = p.closing ? "closing" : "open";
  return (
    <>
      <div className="sheet-backdrop" data-state={state} onClick={onClose} aria-hidden="true" />
      <SettingsSheet initialTab={tab} state={state} onClose={onClose} />
    </>
  );
}

/** Mounted once per opening, so the form starts from the server's current configuration every time. */
function SettingsSheet({ initialTab, state, onClose }: { initialTab: SettingsTab; state: string; onClose: () => void }) {
  const cfg = useStore((s) => s.config)!;
  const ctl = useStore((s) => isController(s));
  const simulated = cfg.capabilities.simulated;
  const tabs = (Object.keys(TAB_INFO) as SettingsTab[]).filter((t) => t !== "simulator" || simulated);
  const [tab, setTab] = useState<SettingsTab>(tabs.includes(initialTab) ? initialTab : "display");
  const [initial] = useState(() => {
    const c = getState().config!.config;
    return { observer: { ...c.observer }, sim: { ...c.sim }, control: { ...c.control }, payload: c.payload };
  });
  const [obs, setObs] = useState(initial.observer);
  const [sim, setSim] = useState(initial.sim);
  const [control, setControl] = useState(initial.control);
  const [payload, setPayload] = useState(initial.payload);
  const [busy, setBusy] = useState(false);
  const dirtyOf = {
    location: JSON.stringify(obs) !== JSON.stringify(initial.observer),
    mount: JSON.stringify(control) !== JSON.stringify(initial.control) || payload !== initial.payload,
    simulator: JSON.stringify(sim) !== JSON.stringify(initial.sim),
  } as Partial<Record<SettingsTab, boolean>>;
  const dirty = Object.values(dirtyOf).some(Boolean);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Escape cancels; focus moves into the sheet and back to where it was on close.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    sheetRef.current?.focus({ preventScroll: true });
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      before?.focus?.({ preventScroll: true });
    };
  }, []);

  const save = async () => {
    setBusy(true);
    const r = await command("set_config", { observer: obs, sim, control, payload });
    setBusy(false);
    if (r.ok) {
      toast("Settings saved");
      onClose();
    }
  };
  const onNavKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const i = tabs.indexOf(tab) + (e.key === "ArrowDown" ? 1 : -1);
    const next = tabs[(i + tabs.length) % tabs.length];
    setTab(next);
    document.getElementById(`set-tab-${next}`)?.focus();
  };
  const serverTab = tab === "location" || tab === "mount" || tab === "simulator";

  return (
    <div className="settings" role="dialog" aria-modal="true" aria-label="Settings" data-state={state} ref={sheetRef} tabIndex={-1}>
      <header className="settings-head">
        <h2>Settings</h2>
        <button className="ghost icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">
          <Icon name="close" />
        </button>
      </header>
      <div className="settings-main">
        <nav className="settings-nav" role="tablist" aria-orientation="vertical" aria-label="Settings categories" onKeyDown={onNavKey}>
          {tabs.map((t) => (
            <button
              key={t}
              id={`set-tab-${t}`}
              role="tab"
              aria-selected={tab === t}
              tabIndex={tab === t ? 0 : -1}
              className={tab === t ? "sel" : ""}
              onClick={() => setTab(t)}
            >
              <Icon name={TAB_INFO[t].icon} />
              <span>{TAB_INFO[t].label}</span>
              {dirtyOf[t] && <i className="dirty-dot" title="Unsaved changes" />}
            </button>
          ))}
        </nav>
        <div className="settings-content" role="tabpanel" aria-labelledby={`set-tab-${tab}`} key={tab}>
          {tab === "display" && <DisplayPage />}
          {tab === "location" && <LocationPage obs={obs} setObs={setObs} />}
          {tab === "mount" && <MountPage payload={payload} setPayload={setPayload} control={control} setControl={setControl} />}
          {tab === "simulator" && <SimulatorPage sim={sim} setSim={setSim} />}
          {tab === "connections" && <ConnectionsPage />}
        </div>
      </div>
      <footer className="settings-foot">
        <span className={`settings-status${!ctl && (serverTab || dirty) ? " warn" : ""}`}>
          {!ctl && (serverTab || dirty)
            ? "Take control to change mount settings"
            : dirty
              ? "Unsaved changes"
              : serverTab
                ? "Saved for every device"
                : tab === "display"
                  ? "Applies at once, this browser only"
                  : ""}
        </span>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!ctl || !dirty || busy} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      </footer>
    </div>
  );
}

// ------------------------------------------------------------------ building blocks
function Group({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="set-group">
      <h3>{title}</h3>
      {desc && <p className="set-desc">{desc}</p>}
      <div className="set-rows">{children}</div>
    </section>
  );
}

/** One setting: label (and hint) on the left, the control on the right. The hint describes, it does not name. */
function Row({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="set-row">
      <div className="set-label">
        <label htmlFor={id}>{label}</label>
        {hint && <small id={`${id}-h`}>{hint}</small>}
      </div>
      <div className="set-ctl">{children(id, hint ? `${id}-h` : undefined)}</div>
    </div>
  );
}

function NumRow({ label, hint, unit, value, step, onChange, disabled }: { label: string; hint?: ReactNode; unit?: string; value: number; step: number; onChange: (v: number) => void; disabled?: boolean }) {
  return (
    <Row label={label} hint={hint}>
      {(id, d) => (
        <span className="num-field">
          <input id={id} aria-describedby={d} type="number" step={step} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
          {unit && <span className="unit">{unit}</span>}
        </span>
      )}
    </Row>
  );
}

function SwitchRow({ label, hint, checked, onChange, disabled }: { label: string; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <Row label={label} hint={hint}>
      {(id, d) => <input id={id} aria-describedby={d} type="checkbox" role="switch" className="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />}
    </Row>
  );
}

function Copyable({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 1200);
    } catch {
      toast("Copy is not available here; select the text instead", "error");
    }
  };
  return (
    <span className="copy-field">
      <code>{text}</code>
      <button className="ghost icon-btn" onClick={copy} aria-label={`Copy ${label}`} title="Copy">
        <Icon name={done ? "check" : "copy"} size={14} />
      </button>
    </span>
  );
}

// ------------------------------------------------------------------ pages
function DisplayPage() {
  const prefs = useStore((s) => s.prefs);
  const zones = useState(() => timeZones())[0];
  const now = simNow();
  const zone = resolveZone(prefs.timeZone);
  const label = zoneLabel(now, prefs.timeZone);
  return (
    <Group title="Angles and time" desc="Saved in this browser only; other devices keep their own choice.">
      <Row label="Angle format" hint={prefs.angles === "dms" ? "18h 36m 56s, +38° 47′ 01″" : "279.2347°, +38.7837°"}>
        {(id, d) => (
          <select id={id} aria-describedby={d} value={prefs.angles} onChange={(e) => setPrefs({ angles: e.target.value as "dms" | "decimal" })} aria-label="Angle format">
            <option value="dms">Sexagesimal</option>
            <option value="decimal">Decimal degrees</option>
          </select>
        )}
      </Row>
      <Row label="Time zone" hint={`Times are shown in ${zone}${label !== zone ? ` (${label})` : ""}`}>
        {(id, d) => (
          <select id={id} aria-describedby={d} value={prefs.timeZone} onChange={(e) => setPrefs({ timeZone: e.target.value })} aria-label="Time zone">
            <option value="device">This device ({resolveZone("device")})</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        )}
      </Row>
    </Group>
  );
}

type Observer = NonNullable<ReturnType<typeof getState>["config"]>["config"]["observer"];

function LocationPage({ obs, setObs }: { obs: Observer; setObs: (o: Observer) => void }) {
  const ctl = useStore((s) => isController(s));
  const [locating, setLocating] = useState(false);
  const geo = () => {
    if (!navigator.geolocation) return toast("Geolocation not available", "error");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setLocating(false);
        setObs({ ...obs, latitude: +p.coords.latitude.toFixed(4), longitude: +p.coords.longitude.toFixed(4), elevation_m: Math.round(p.coords.altitude ?? obs.elevation_m), name: "My location" });
      },
      (e) => {
        setLocating(false);
        toast(`Location unavailable: ${e.message}`, "error");
      },
      { timeout: 15000 },
    );
  };
  return (
    <Group title="Observer location" desc="Every position, rise and set time is computed for this site.">
      <Row label="Name">{(id) => <input id={id} value={obs.name} disabled={!ctl} onChange={(e) => setObs({ ...obs, name: e.target.value })} />}</Row>
      <NumRow label="Latitude" hint="North positive, south negative" unit="°" step={0.0001} value={obs.latitude} disabled={!ctl} onChange={(v) => setObs({ ...obs, latitude: v })} />
      <NumRow label="Longitude" hint="East positive, west negative" unit="°" step={0.0001} value={obs.longitude} disabled={!ctl} onChange={(v) => setObs({ ...obs, longitude: v })} />
      <NumRow label="Elevation" hint="Above sea level" unit="m" step={1} value={obs.elevation_m} disabled={!ctl} onChange={(v) => setObs({ ...obs, elevation_m: v })} />
      <div className="set-actions">
        <button type="button" onClick={geo} disabled={!ctl || locating}>
          <Icon name="pin" size={14} /> {locating ? "Locating…" : "Use this device's location"}
        </button>
        <small>Optional. The browser asks first; nothing leaves this network.</small>
      </div>
    </Group>
  );
}

type Rec = Record<string, number | boolean>;

function MountPage({ payload, setPayload, control, setControl }: { payload: string; setPayload: (p: string) => void; control: Rec; setControl: (c: Rec) => void }) {
  const cfg = useStore((s) => s.config)!;
  const ctl = useStore((s) => isController(s));
  const p = cfg.payloads[payload];
  const num = (key: string, label: string, unit: string, step: number, hint?: string) => (
    <NumRow label={label} hint={hint} unit={unit} step={step} value={control[key] as number} disabled={!ctl} onChange={(v) => setControl({ ...control, [key]: v })} />
  );
  return (
    <>
      <Group title="Payload">
        <Row label="Instrument" hint={p ? `${p.mass_kg} kg · field ${p.fov_deg}° · reaches ${p.max_altitude}° altitude` : undefined}>
          {(id, d) => (
            <select id={id} aria-describedby={d} value={payload} disabled={!ctl} onChange={(e) => setPayload(e.target.value)}>
              {Object.values(cfg.payloads).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          )}
        </Row>
      </Group>
      <Group title="Pointing">
        {num("goto_tolerance_deg", "GoTo tolerance", "°", 0.005, "A GoTo has arrived inside this error")}
        <SwitchRow label="Track after GoTo" hint="Keep following the target once it is reached" checked={!!control.track_after_goto} disabled={!ctl} onChange={(v) => setControl({ ...control, track_after_goto: v })} />
      </Group>
      <Group title="Safety">
        {num("min_target_altitude", "Lowest target altitude", "°", 1, "Targets below this are refused")}
        {num("sun_exclusion_deg", "Sun exclusion radius", "°", 1, "No GoTo or slew closer to the Sun")}
        <SwitchRow label="Unwind cable wrap" hint="Turn back 360° automatically at the azimuth limit" checked={!!control.auto_unwind} disabled={!ctl} onChange={(v) => setControl({ ...control, auto_unwind: v })} />
      </Group>
    </>
  );
}

function SimulatorPage({ sim, setSim }: { sim: Rec; setSim: (s: Rec) => void }) {
  const cfg = useStore((s) => s.config)!;
  const ctl = useStore((s) => isController(s));
  const num = (key: string, label: string, unit: string, step: number, hint?: string) => (
    <NumRow label={label} hint={hint} unit={unit} step={step} value={sim[key] as number} disabled={!ctl} onChange={(v) => setSim({ ...sim, [key]: v })} />
  );
  return (
    <>
      <Group title="Mechanics" desc="Imperfections of the simulated mount. Real hardware is not affected.">
        {num("command_latency_s", "Command latency", "s", 0.01)}
        {num("backlash_alt", "Backlash, altitude", "°", 0.01)}
        {num("backlash_az", "Backlash, azimuth", "°", 0.01)}
        {num("encoder_noise", "Encoder noise", "° rms", 0.001)}
      </Group>
      <Group title="Alignment errors" desc="Stay uncorrected until you Sync or plate-solve.">
        {num("az_zero_offset", "Azimuth zero error", "°", 0.05, "True north misalignment")}
        {num("alt_zero_offset", "Altitude zero error", "°", 0.05)}
        {num("tilt_magnitude", "Base tilt", "°", 0.05)}
        {num("tilt_direction", "Tilt direction", "° az", 1, "Azimuth towards which the base dips")}
      </Group>
      <Group title="Fault injection" desc="Takes effect at once, not with Save.">
        <FaultButtons faults={cfg.injectable_faults} disabled={!ctl} />
      </Group>
    </>
  );
}

const EMPTY: never[] = [];

function FaultButtons({ faults, disabled }: { faults: string[]; disabled: boolean }) {
  const active = useStore((s) => s.mount?.driver.motor.injected_faults ?? EMPTY);
  return (
    <div className="faults">
      {faults.map((f) => (
        <button key={f} disabled={disabled} aria-pressed={active.includes(f)} className={active.includes(f) ? "warnbtn" : ""} onClick={() => command("inject_fault", { fault: f, active: !active.includes(f) })}>
          {f.replace(/_/g, " ")}
        </button>
      ))}
    </div>
  );
}

/** Answer of GET /api/alpaca. */
interface AlpacaInfo {
  enabled: boolean;
  device_type: string;
  device_number: number;
  device_name: string;
  path: string;
  port: number | null;
  listen_host: string | null;
  lan: boolean;
  addresses: string[];
  discovery: { enabled: boolean; active: boolean; port: number };
  auth: "none" | "basic";
  unique_id: string;
  clients: { name: string; address: string; idle_s: number }[];
}

function ConnectionsPage() {
  const caps = useStore((s) => s.config!.capabilities);
  const version = useStore((s) => s.config!.version);
  const [info, setInfo] = useState<AlpacaInfo | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch("/api/alpaca")
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d: AlpacaInfo) => !stop && setInfo(d))
        .catch(() => !stop && setFailed(true));
    load();
    const id = window.setInterval(load, 3000); // client list
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);
  const port = info?.port ?? (Number(location.port) || (location.protocol === "https:" ? 443 : 80));
  const host = info?.addresses[0] ?? location.hostname;
  return (
    <>
      <Group title="Mount driver">
        <dl className="set-facts">
          <dt>Driver</dt>
          <dd>{caps.name}</dd>
          <dt>Kind</dt>
          <dd>{caps.simulated ? "Simulator (no hardware)" : caps.kind}</dd>
          {caps.firmware ? (
            <>
              <dt>Firmware</dt>
              <dd>{caps.firmware}</dd>
            </>
          ) : null}
          <dt>OpenSkyMount</dt>
          <dd>{version}</dd>
        </dl>
      </Group>
      <Group
        title="ASCOM Alpaca"
        desc="Planetarium and imaging programs that speak Alpaca can read the position and send GoTo, Sync and Stop. Checked with the alpyca library; other programs are not verified yet."
      >
        {failed && !info && <p className="set-desc warn">Connection details are not available.</p>}
        {info && !info.enabled && <p className="set-desc">Alpaca is off: the server was started with --no-alpaca.</p>}
        {info?.enabled && (
          <>
            <dl className="set-facts">
              <dt>Device</dt>
              <dd>
                {info.device_type} {info.device_number}
              </dd>
              <dt>Address</dt>
              <dd className="addr-list">
                {info.addresses.map((a) => (
                  <Copyable key={a} text={a} label={`address ${a}`} />
                ))}
              </dd>
              <dt>Port</dt>
              <dd>
                <Copyable text={String(port)} label="port" />
              </dd>
              <dt>Device URL</dt>
              <dd>
                <Copyable text={`http://${host.includes(":") ? `[${host}]` : host}:${port}${info.path}`} label="device URL" />
              </dd>
              <dt>Discovery</dt>
              <dd>{info.discovery.active ? `On (UDP ${info.discovery.port})` : info.discovery.enabled ? `Not available (UDP ${info.discovery.port} in use)` : "Off"}</dd>
              <dt>Login</dt>
              <dd>{info.auth === "basic" ? "HTTP Basic: any user name with the shared password, or an account" : "None"}</dd>
              <dt>Clients</dt>
              <dd>{info.clients.length ? info.clients.map((c) => `${c.name} (${c.address})`).join(", ") : "None connected"}</dd>
            </dl>
            {!info.lan && (
              <p className="set-note">
                <Icon name="help" size={14} />
                <span>
                  The server only listens on this computer, so other computers cannot connect and discovery stays local. Start it with{" "}
                  <code>--host 0.0.0.0</code> (and a password) to use Alpaca over the network.
                </span>
              </p>
            )}
          </>
        )}
      </Group>
    </>
  );
}
