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
  capabilities: { name: string; kind: string; simulated: boolean; supports_time_warp: boolean; has_axis_encoders: boolean; firmware?: string };
  injectable_faults: string[];
  version: string;
  plate_solver: boolean;
}

export interface QueueItem {
  id: string;
  target: string;
  name: string;
  start_at: number | null;
  duration_s: number | null;
  status: "pending" | "waiting" | "slewing" | "tracking" | "done" | "skipped" | "interrupted";
  message: string;
  started_at: number | null;
  tracking_since: number | null;
  finished_at: number | null;
}

export interface SessionInfo {
  running: boolean;
  current: string | null;
  remaining_s: number | null;
  items: QueueItem[];
}

export interface RecordingInfo {
  active: boolean;
  name: string | null;
  samples: number;
  bytes: number;
}

export type Role = "admin" | "operator" | "observer";

export interface AuthUser {
  kind: "open" | "shared" | "account";
  role: Role;
  username: string | null;
  display_name: string;
}

/** Answer of GET /api/auth. */
export interface AuthInfo {
  required: boolean;
  authenticated: boolean;
  mode: "open" | "password" | "accounts";
  user: AuthUser | null;
  password_login: boolean;
  setup_allowed: boolean;
  min_password_length: number;
  roles: Role[];
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
  session: SessionInfo | null;
  recording: RecordingInfo | null;
  /** Changes whenever orbital elements (satellites, comets, asteroids) are loaded or removed. */
  orbitsRevision: number;
  selectedId: string | null;
  /** Ask the sky map to centre on an object (`n` changes on every request). */
  centerReq: { id: string; n: number } | null;
  toasts: Toast[];
  latencyMs: number | null;
  /** "unknown" until /api/auth answered; "required" shows the login screen. */
  auth: "unknown" | "required" | "ok";
  /** True when the server requires a login (shared password or accounts). */
  authEnabled: boolean;
  /** Who this browser is signed in as, and what the server offers (accounts, first-run setup). */
  authInfo: AuthInfo | null;
  prefs: DisplayPrefs;
  /** Manual slew rate (deg/s) shared by the on-screen pad, the hand controller and a gamepad. */
  manualSpeed: number;
  gamepad: { id: string | null; held: boolean; moving: boolean };
}

export const MANUAL_SPEEDS = [0.05, 0.5, 2, 5];

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
  session: null,
  recording: null,
  orbitsRevision: 0,
  selectedId: null,
  centerReq: null,
  toasts: [],
  latencyMs: null,
  auth: "unknown",
  authEnabled: false,
  authInfo: null,
  prefs: loadPrefs(),
  manualSpeed: 1,
  gamepad: { id: null, held: false, moving: false },
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

/** False for view-only (observer) accounts: they cannot take control. */
export function canControl(s: State = state): boolean {
  return s.authInfo?.user?.role !== "observer";
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

/** Fetch GET /api/auth into the store. Returns null if the server is unreachable. */
export async function refreshAuth(): Promise<AuthInfo | null> {
  try {
    const r = await fetch("/api/auth");
    const a = (await r.json()) as AuthInfo;
    setState({ authInfo: a, authEnabled: a.required, auth: a.required && !a.authenticated ? "required" : "ok" });
    return a;
  } catch {
    return null;
  }
}

/** Check whether the server wants a login, then open the WebSocket. */
export async function start(): Promise<void> {
  const a = await refreshAuth();
  if (!a) {
    // Server unreachable: show the app; the socket below retries with backoff and reports the connection state.
    setState({ auth: "ok" });
  } else if (a.required && !a.authenticated) return;
  connect();
}

/** Close the socket on purpose (no reconnect) and open a new one, e.g. after the login changed. */
function reconnect(): void {
  clearTimeout(reconnectTimer);
  const sock = ws;
  ws = null;
  sock?.close();
  connect();
}

async function postJson(url: string, body: unknown): Promise<{ ok: boolean; status: number; data: Record<string, unknown> }> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  return { ok: r.ok, status: r.status, data };
}

/** Log in with an account (`username`) or the shared password (no `username`). Returns an error text or null. */
export async function login(password: string, username?: string, remember = false): Promise<string | null> {
  try {
    const r = await postJson("/api/login", { username: username || undefined, password, remember });
    if (!r.ok) return (r.data.message as string) ?? `login failed (${r.status})`;
  } catch {
    return "server not reachable";
  }
  await refreshAuth();
  setState({ auth: "ok" });
  reconnect();
  return null;
}

/** Create the first (admin) account. Turns sign-in on for every device. Returns an error text or null. */
export async function setupAdmin(username: string, password: string, displayName: string): Promise<string | null> {
  try {
    const r = await postJson("/api/setup", { username, password, display_name: displayName, remember: true });
    if (!r.ok) return (r.data.message as string) ?? `setup failed (${r.status})`;
  } catch {
    return "server not reachable";
  }
  await refreshAuth();
  setState({ auth: "ok" });
  reconnect();
  return null;
}

export async function logout(everywhere = false): Promise<void> {
  await postJson("/api/logout", { everywhere }).catch(() => {});
  clearTimeout(reconnectTimer);
  const sock = ws;
  ws = null;
  sock?.close();
  setState({ connection: "closed", sessionId: null });
  const a = await refreshAuth();
  if (a && !a.required) connect(); // nothing to log out of (open mode)
  else setState({ auth: "required" });
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
      // Login no longer valid (server restarted, logged out elsewhere, account changed, or sign-in was just turned
      // on). Ask the server what to do: log in again, or reconnect if this browser holds a newer login.
      void refreshAuth().then((a) => {
        if (ws !== null && ws !== sock) return; // someone already reconnected
        if (!a || a.authenticated) reconnectTimer = window.setTimeout(connect, 300);
        else setState({ auth: "required", authEnabled: true });
      });
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
        session: (msg.session as SessionInfo) ?? null,
        recording: (msg.recording as RecordingInfo) ?? null,
        ...(typeof msg.orbits_revision === "number" && msg.orbits_revision !== state.orbitsRevision
          ? { orbitsRevision: msg.orbits_revision }
          : {}),
      });
      break;
    case "orbits":
      if (typeof msg.revision === "number") setState({ orbitsRevision: msg.revision });
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
    case "auth_changed":
      // Our account changed (role, name, password elsewhere): fetch who we are now.
      void refreshAuth();
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

/** Select an object; `center`: also turn the sky map toward it. */
export function select(id: string | null, center = false): void {
  setState(center && id ? { selectedId: id, centerReq: { id, n: (state.centerReq?.n ?? 0) + 1 } } : { selectedId: id });
}
