// Global client state + WebSocket connection to the OpenSkyMount server.
import { useSyncExternalStore } from "react";
import { DEFAULT_PREFS, type DisplayPrefs, isValidZone } from "./format";

export interface TargetSummary {
  id: string;
  name: string;
  kind: string;
  type: string;
  magnitude: number | null;
  constellation: string | null;
}

export interface MountSnapshot {
  time: number;
  mode: string;
  tracking: boolean;
  target: (TargetSummary & { alt: number; az: number; alt_rate: number; az_rate: number }) | null;
  position: { alt: number; az: number; alt_axis: number; az_axis: number; alt_rate: number; az_rate: number; ra?: number; dec?: number };
  tracking_error: number | null;
  true_pointing_error: number | null;
  flags: string[];
  field_rotation_rate: number | null;
  pointing_model: {
    terms: string;
    alt_off: number;
    az_off: number;
    tilt_n: number;
    tilt_e: number;
    tilt: number;
    rms: number | null;
    tilt_min_points: number;
    points: { mount_alt: number; mount_az: number; sky_alt: number; sky_az: number; label: string; time: number; source: string; residual: number }[];
  };
  driver: {
    connected: boolean;
    homed: boolean;
    homing: boolean;
    estop: boolean;
    faults: string[];
    alt_encoder: number | null;
    az_encoder: number | null;
    alt_moving: boolean;
    az_moving: boolean;
    motor: { alt_mode?: string; az_mode?: string; queued_commands?: number; injected_faults?: string[] };
    truth: { alt: number; az: number; alt_axis: number; az_axis: number } | null;
  };
  limits: { alt_min: number; alt_max: number; az_min: number; az_max: number; max_rate: number; max_accel: number };
  events: { t: number; level: string; msg: string }[];
}

export interface ClockInfo {
  live: boolean;
  rate: number;
  time: number;
  epoch: number;
  wall: number;
}

export interface ControlInfo {
  clients: { id: string; name: string; controller: boolean }[];
  controller: string | null;
  controller_name: string | null;
}

export interface Payload {
  id: string;
  name: string;
  mass_kg: number;
  shape: string;
  max_altitude: number;
  fov_deg: number;
}

export interface PublicConfig {
  config: {
    observer: { latitude: number; longitude: number; elevation_m: number; name: string; temperature_c: number | null; pressure_mbar: number | null };
    payload: string;
    limits: Record<string, number>;
    sim: Record<string, number | boolean>;
    control: Record<string, number | boolean>;
  };
  payloads: Record<string, Payload>;
  effective_limits: Record<string, number>;
  capabilities: { name: string; kind: string; simulated: boolean; supports_time_warp: boolean; has_axis_encoders: boolean };
  injectable_faults: string[];
  version: string;
  plate_solver: boolean;
}

export interface Toast {
  id: number;
  text: string;
  level: "info" | "error";
}

export interface State {
  connection: "connecting" | "open" | "closed";
  sessionId: string | null;
  config: PublicConfig | null;
  mount: MountSnapshot | null;
  clock: ClockInfo | null;
  clockReceived: number; // performance.now()/1000 at receipt
  control: ControlInfo | null;
  selectedId: string | null;
  toasts: Toast[];
  latencyMs: number | null;
  /** "unknown" until /api/auth answered; "required" shows the login screen. */
  auth: "unknown" | "required" | "ok";
  /** True when the server has a password set (shows the log-out button). */
  authEnabled: boolean;
  prefs: DisplayPrefs;
}

const PREFS_KEY = "osm-display";
function loadPrefs(): DisplayPrefs {
  try {
    const p = { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") } as DisplayPrefs;
    if (p.angles !== "dms" && p.angles !== "decimal") p.angles = DEFAULT_PREFS.angles;
    if (typeof p.timeZone !== "string" || !isValidZone(p.timeZone)) p.timeZone = DEFAULT_PREFS.timeZone;
    return p;
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

let state: State = {
  connection: "connecting",
  sessionId: null,
  config: null,
  mount: null,
  clock: null,
  clockReceived: 0,
  control: null,
  selectedId: null,
  toasts: [],
  latencyMs: null,
  auth: "unknown",
  authEnabled: false,
  prefs: loadPrefs(),
};

/** Display preferences are per device (kept in this browser only). */
export function setPrefs(patch: Partial<DisplayPrefs>): void {
  const prefs = { ...state.prefs, ...patch };
  setState({ prefs });
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* private mode: preference lasts for this page only */
  }
}

const listeners = new Set<() => void>();

export function getState(): State {
  return state;
}

export function setState(patch: Partial<State>): void {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => selector(state),
  );
}

let toastId = 0;
export function toast(text: string, level: "info" | "error" = "info"): void {
  const id = ++toastId;
  setState({ toasts: [...state.toasts.slice(-3), { id, text, level }] });
  setTimeout(() => setState({ toasts: state.toasts.filter((t) => t.id !== id) }), level === "error" ? 6000 : 3000);
}

/** Current simulation time (Unix seconds) extrapolated from the last server clock message. */
export function simNow(): number {
  const c = state.clock;
  if (!c) return Date.now() / 1000;
  return c.time + (performance.now() / 1000 - state.clockReceived) * c.rate;
}

export function isController(s: State = state): boolean {
  return !!s.sessionId && s.control?.controller === s.sessionId;
}

// ------------------------------------------------------------------ socket
type Ack = { ok: boolean; message?: string; [k: string]: unknown };

let ws: WebSocket | null = null;
let seq = 0;
const pending = new Map<number, (a: Ack) => void>();
let retry = 0;
let pingTimer: number | undefined;

function clientName(): string {
  const ua = navigator.userAgent;
  const dev = /iPhone|Android.*Mobile/.test(ua) ? "phone" : /iPad|Android/.test(ua) ? "tablet" : "desktop";
  return `${dev}-${Math.random().toString(36).slice(2, 6)}`;
}
const NAME = clientName();

let reconnectTimer: number | undefined;

/** Check whether the server wants a login, then open the WebSocket. */
export async function start(): Promise<void> {
  try {
    const r = await fetch("/api/auth");
    const a = (await r.json()) as { required: boolean; authenticated: boolean };
    setState({ authEnabled: a.required, auth: a.required && !a.authenticated ? "required" : "ok" });
    if (a.required && !a.authenticated) return;
  } catch {
    // Server unreachable: show the app; the socket below retries with backoff and reports the connection state.
    setState({ auth: "ok" });
  }
  connect();
}

export async function login(password: string): Promise<string | null> {
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
    const body = (await r.json().catch(() => ({}))) as { message?: string };
    if (!r.ok) return body.message ?? `login failed (${r.status})`;
  } catch {
    return "server not reachable";
  }
  setState({ auth: "ok" });
  connect();
  return null;
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" }).catch(() => {});
  clearTimeout(reconnectTimer);
  const sock = ws;
  ws = null;
  sock?.close();
  setState({ auth: "required", connection: "closed", sessionId: null });
}

/** Emergency stop over plain HTTP: works without a login (used on the login screen). */
export async function estopHttp(): Promise<boolean> {
  try {
    const r = await fetch("/api/estop", { method: "POST" });
    return r.ok;
  } catch {
    return false;
  }
}

export function connect(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  setState({ connection: "connecting" });
  const sock = new WebSocket(`${proto}://${location.host}/ws?name=${encodeURIComponent(NAME)}`);
  ws = sock;
  sock.onopen = () => {
    retry = 0;
    setState({ connection: "open" });
    clearInterval(pingTimer);
    pingTimer = window.setInterval(() => send({ type: "ping", t: performance.now() }), 3000);
  };
  sock.onmessage = (ev) => handle(JSON.parse(ev.data));
  sock.onclose = (ev) => {
    clearInterval(pingTimer);
    pending.forEach((resolve) => resolve({ ok: false, message: "connection lost" }));
    pending.clear();
    // A new connection is a new session: control must be re-acquired explicitly.
    setState({ connection: "closed", sessionId: null });
    if (ws !== sock) return; // closed on purpose (log out)
    if (ev.code === 4401) {
      // Session no longer valid (server restarted or logged out elsewhere): ask for the password again.
      setState({ auth: "required", authEnabled: true });
      return;
    }
    const delay = Math.min(10000, 500 * 2 ** retry++);
    reconnectTimer = window.setTimeout(connect, delay);
  };
}

function send(msg: object): boolean {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
    return true;
  }
  return false;
}

function handle(msg: Record<string, unknown>): void {
  switch (msg.type) {
    case "welcome":
      setState({ sessionId: msg.session_id as string, config: msg as unknown as PublicConfig });
      break;
    case "config":
      setState({ config: msg as unknown as PublicConfig });
      break;
    case "state":
      setState({
        mount: msg.mount as MountSnapshot,
        clock: msg.clock as ClockInfo,
        clockReceived: performance.now() / 1000,
        control: msg.control as ControlInfo,
      });
      break;
    case "control":
      setState({ control: msg as unknown as ControlInfo });
      break;
    case "pong":
      if (typeof msg.t === "number") setState({ latencyMs: performance.now() - msg.t });
      break;
    case "ack": {
      const cb = pending.get(msg.seq as number);
      if (cb) {
        pending.delete(msg.seq as number);
        cb(msg as unknown as Ack);
      }
      break;
    }
    case "control_result":
      if (!msg.granted && msg.message && msg.message !== "released") toast(String(msg.message), "error");
      break;
    case "error":
      toast(String(msg.message), "error");
      break;
  }
}

export function command(cmd: string, args: Record<string, unknown> = {}, quiet = false): Promise<Ack> {
  const s = ++seq;
  return new Promise((resolve) => {
    if (!send({ type: "command", seq: s, cmd, args })) {
      if (!quiet) toast("Not connected", "error");
      resolve({ ok: false, message: "not connected" });
      return;
    }
    pending.set(s, (ack) => {
      if (!ack.ok && !quiet) toast(ack.message ?? "Command failed", "error");
      resolve(ack);
    });
  });
}

export function requestControl(force = false): void {
  send({ type: "request_control", force });
}

export function releaseControl(): void {
  send({ type: "release_control" });
}

export function select(id: string | null): void {
  setState({ selectedId: id });
}
