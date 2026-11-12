// Sign-in, the account menu in the top bar and the account dialog (profile, password and logins, users, first admin).
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { formatDateTime } from "../format";
import { usePresence } from "../presence";
import { estopHttp, login, logout, refreshAuth, setupAdmin, toast, useStore, type AuthUser, type Role } from "../store";
import { Icon, Logo } from "./Icon";
import { Starfield } from "./Welcome";

// ------------------------------------------------------------------ API helpers
interface ApiResult<T> {
  ok: boolean;
  status: number;
  data: T & { message?: string };
}

async function api<T = Record<string, unknown>>(method: string, url: string, body?: unknown): Promise<ApiResult<T>> {
  try {
    const r = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = (await r.json().catch(() => ({}))) as T & { message?: string };
    return { ok: r.ok, status: r.status, data };
  } catch {
    return { ok: false, status: 0, data: { message: "server not reachable" } as T & { message?: string } };
  }
}

const errText = (r: ApiResult<unknown>) => r.data.message ?? (r.status ? `request failed (${r.status})` : "server not reachable");

interface AccountRow {
  username: string;
  display_name: string;
  role: Role;
  created_at: number;
  password_changed_at: number;
  last_login: number | null;
  sessions?: number;
  active?: boolean;
}

interface LoginRow {
  id: string;
  created: number;
  last_seen: number;
  client: string;
  agent: string;
  current: boolean;
}

const ROLE_TEXT: Record<Role, string> = {
  admin: "Admin: controls the mount and manages accounts",
  operator: "Operator: controls the mount",
  observer: "Observer: view only (can still stop the mount)",
};

/** A random password that is easy to read out: three word-like chunks and digits. */
export function generatePassword(): string {
  const a = "abcdefghijkmnpqrstuvwxyz";
  const pick = (n: number) => Array.from(crypto.getRandomValues(new Uint32Array(n)), (x) => a[x % a.length]).join("");
  const d = crypto.getRandomValues(new Uint32Array(1))[0] % 100;
  return `${pick(4)}-${pick(4)}-${pick(4)}-${String(d).padStart(2, "0")}`;
}

/** Short device description from a user-agent string (for the list of logins). */
export function deviceName(agent: string): string {
  const os = /iPhone/.test(agent) ? "iPhone" : /iPad/.test(agent) ? "iPad" : /Android/.test(agent) ? "Android" : /Mac OS X/.test(agent) ? "Mac" : /Windows/.test(agent) ? "Windows" : /Linux/.test(agent) ? "Linux" : "";
  const br = /Edg\//.test(agent) ? "Edge" : /Firefox\//.test(agent) ? "Firefox" : /Chrome\//.test(agent) ? "Chrome" : /Safari\//.test(agent) ? "Safari" : /python|curl|httpx/i.test(agent) ? "script" : "";
  return [br, os].filter(Boolean).join(" on ") || "Unknown device";
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Avatar({ user, size = 26 }: { user: AuthUser | null | undefined; size?: number }) {
  const kind = user?.kind ?? "open";
  return (
    <span className={`avatar role-${user?.role ?? "operator"} kind-${kind}`} style={{ width: size, height: size, fontSize: size * 0.4 }} aria-hidden="true">
      {kind === "account" ? initials(user!.display_name) : <Icon name={kind === "shared" ? "key" : "user"} size={Math.round(size * 0.58)} />}
    </span>
  );
}

/** Labelled input; the hint sits outside the label so it does not become part of the input's accessible name. */
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="field">
      <label>
        <span>{label}</span>
        {children}
      </label>
      {hint && <small>{hint}</small>}
    </div>
  );
}

// ------------------------------------------------------------------ login screen
export function LoginScreen() {
  const info = useStore((s) => s.authInfo);
  const toasts = useStore((s) => s.toasts);
  const accounts = info?.mode === "accounts";
  const [shared, setShared] = useState(false);
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [remember, setRemember] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const useName = accounts && !shared;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(await login(pw, useName ? user.trim() : undefined, remember));
    setBusy(false);
  };
  return (
    <div className="login">
      <Starfield />
      <form className="login-box" onSubmit={submit}>
        <h1>
          <Logo size={30} />
          OpenSkyMount
        </h1>
        <p>{useName ? "Log in to see the sky and control the mount." : "This mount is password protected."}</p>
        {useName && (
          <Field label="User name">
            <input autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus value={user} onChange={(e) => setUser(e.target.value)} />
          </Field>
        )}
        <Field label="Password">
          <input type="password" autoComplete="current-password" autoFocus={!useName} value={pw} onChange={(e) => setPw(e.target.value)} />
        </Field>
        <label className="check-row">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          <span>Keep me logged in on this device</span>
        </label>
        {err && (
          <p className="login-error" role="alert">
            {err}
          </p>
        )}
        <button className="primary" type="submit" disabled={busy || !pw || (useName && !user.trim())}>
          {busy ? "Checking…" : "Log in"}
        </button>
        {accounts && info?.password_login && (
          <button type="button" className="linkish" onClick={() => setShared(!shared)}>
            {shared ? "Log in with a user name instead" : "Use the shared password instead"}
          </button>
        )}
        {useName && <p className="login-note">Forgot your password? Ask an admin to reset it.</p>}
        <hr />
        <p className="login-note">Emergency stop works without logging in.</p>
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

// ------------------------------------------------------------------ account menu (top bar)
export type AccountTab = "profile" | "security" | "users" | "setup";

export function AccountMenu({ onAccount, onWelcome, onGuide }: { onAccount: (t: AccountTab) => void; onWelcome: () => void; onGuide: () => void }) {
  const info = useStore((s) => s.authInfo);
  const [open, setOpen] = useState(false);
  const presence = usePresence(open, 160);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", down);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("keydown", key);
    };
  }, [open]);
  const user = info?.user;
  const kind = user?.kind ?? "open";
  const name = kind === "account" ? user!.display_name : kind === "shared" ? "Shared password" : "Guest";
  const go = (f: () => void) => () => {
    setOpen(false);
    f();
  };
  return (
    <div className="account" ref={ref}>
      <button className={`ghost account-btn${open ? " sel" : ""}`} onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} aria-label="Account" title={`Account: ${name}`}>
        <Avatar user={user} size={22} />
        <span>{name}</span>
      </button>
      {presence.mounted && (
        <div className="menu" role="menu" data-state={presence.closing ? "closing" : "open"}>
          <div className="menu-head">
            <Avatar user={user} size={36} />
            <div>
              <strong>{name}</strong>
              <small>
                {kind === "account" ? `@${user!.username} · ${user!.role}` : kind === "shared" ? "operator · no personal account" : "sign-in is off"}
              </small>
            </div>
          </div>
          {kind === "account" && (
            <button role="menuitem" onClick={go(() => onAccount("profile"))}>
              <Icon name="user" /> Account settings
            </button>
          )}
          {kind === "account" && user!.role === "admin" && (
            <button role="menuitem" onClick={go(() => onAccount("users"))}>
              <Icon name="users" /> Manage users
            </button>
          )}
          {info?.setup_allowed && (
            <button role="menuitem" onClick={go(() => onAccount("setup"))}>
              <Icon name="shield" /> Create admin account
            </button>
          )}
          {kind === "open" && !info?.setup_allowed && <p className="menu-note">Accounts can be created on the computer that runs OpenSkyMount.</p>}
          <div className="menu-sep" />
          <button role="menuitem" onClick={go(onGuide)}>
            <Icon name="compass" /> Simulator setup guide
          </button>
          <button role="menuitem" onClick={go(onWelcome)}>
            <Icon name="sparkle" /> Welcome screen
          </button>
          {info?.required && (
            <>
              <div className="menu-sep" />
              <button role="menuitem" onClick={go(() => void logout())}>
                <Icon name="logout" /> Log out
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ account dialog
export function AccountDialog({ tab: initial, onClose }: { tab: AccountTab; onClose: () => void }) {
  const info = useStore((s) => s.authInfo);
  const user = info?.user;
  const isAccount = user?.kind === "account";
  const isAdmin = isAccount && user?.role === "admin";
  const tabs: [AccountTab, string][] = [];
  if (isAccount) tabs.push(["profile", "Profile"], ["security", "Password & logins"]);
  if (isAdmin) tabs.push(["users", "Users"]);
  if (info?.setup_allowed) tabs.push(["setup", "Create admin account"]);
  const [tab, setTab] = useState<AccountTab>(tabs.some(([t]) => t === initial) ? initial : (tabs[0]?.[0] ?? "setup"));
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Account">
      <div className="modal-body account-dialog">
        <header>
          <div className="acct-title">
            <Avatar user={user} size={34} />
            <div>
              <h2>{isAccount ? user!.display_name : "Accounts"}</h2>
              <small>{isAccount ? `@${user!.username} · ${user!.role}` : info?.mode === "open" ? "Sign-in is off" : "Logged in with the shared password"}</small>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close">
            Close
          </button>
        </header>
        {tabs.length > 1 && (
          <div className="seg acct-tabs" role="tablist">
            {tabs.map(([t, label]) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "sel" : ""} onClick={() => setTab(t)}>
                {label}
              </button>
            ))}
          </div>
        )}
        {tab === "profile" && isAccount && <Profile />}
        {tab === "security" && isAccount && <Security />}
        {tab === "users" && isAdmin && <Users me={user!.username!} />}
        {tab === "setup" && <SetupAdminForm onDone={onClose} />}
        {!tabs.length && <p className="hint">Log in with an account to manage it.</p>}
      </div>
    </div>
  );
}

function Profile() {
  const tz = useStore((s) => s.prefs.timeZone);
  const [acct, setAcct] = useState<AccountRow | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void api<{ user: AccountRow }>("GET", "/api/account").then((r) => {
      if (r.ok) {
        setAcct(r.data.user);
        setName(r.data.user.display_name);
      }
    });
  }, []);
  if (!acct) return <p className="hint">Loading…</p>;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const r = await api<{ user: AccountRow }>("PATCH", "/api/account", { display_name: name });
    setBusy(false);
    if (!r.ok) return toast(errText(r), "error");
    setAcct(r.data.user);
    toast("Profile saved");
    void refreshAuth();
  };
  return (
    <form className="acct-section" onSubmit={save}>
      <Field label="Display name" hint="Shown to other devices, e.g. “Night Owl has control”.">
        <input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
      </Field>
      <dl className="acct-facts">
        <dt>User name</dt>
        <dd>@{acct.username}</dd>
        <dt>Role</dt>
        <dd>{ROLE_TEXT[acct.role]}</dd>
        <dt>Member since</dt>
        <dd>{formatDateTime(acct.created_at, tz)}</dd>
        <dt>Last login</dt>
        <dd>{acct.last_login ? formatDateTime(acct.last_login, tz) : "—"}</dd>
      </dl>
      <div className="row">
        <button className="primary" type="submit" disabled={busy || name.trim() === acct.display_name}>
          Save profile
        </button>
      </div>
    </form>
  );
}

function Security() {
  const info = useStore((s) => s.authInfo);
  const tz = useStore((s) => s.prefs.timeZone);
  const min = info?.min_password_length ?? 8;
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [logins, setLogins] = useState<LoginRow[] | null>(null);
  const load = () => void api<{ sessions: LoginRow[] }>("GET", "/api/account").then((r) => r.ok && setLogins(r.data.sessions));
  useEffect(load, []);
  const mismatch = pw2.length > 0 && pw !== pw2;
  const change = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    const r = await api("POST", "/api/account/password", { current_password: cur, new_password: pw });
    setBusy(false);
    if (!r.ok) return setErr(errText(r));
    setCur("");
    setPw("");
    setPw2("");
    toast("Password changed. Other devices were logged out.");
    load();
  };
  return (
    <div className="acct-section">
      <form onSubmit={change} className="acct-form">
        <h3>Change password</h3>
        <Field label="Current password">
          <input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        </Field>
        <Field label="New password" hint={`At least ${min} characters.`}>
          <input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        </Field>
        <Field label="Repeat new password">
          <input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} aria-invalid={mismatch} />
        </Field>
        {mismatch && <p className="login-error">The new passwords do not match.</p>}
        {err && (
          <p className="login-error" role="alert">
            {err}
          </p>
        )}
        <div className="row">
          <button className="primary" type="submit" disabled={busy || !cur || pw.length < min || pw !== pw2}>
            Change password
          </button>
        </div>
      </form>
      <h3>Where you are logged in</h3>
      {!logins ? (
        <p className="hint">Loading…</p>
      ) : (
        <ul className="logins">
          {logins.map((s) => (
            <li key={s.id}>
              <span>
                <strong>{deviceName(s.agent)}</strong>
                {s.current && <span className="badge on">this device</span>}
                <small>
                  {s.client} · last active {formatDateTime(s.last_seen, tz)}
                </small>
              </span>
              {!s.current && (
                <button
                  onClick={async () => {
                    const r = await api("DELETE", `/api/account/sessions/${s.id}`);
                    if (!r.ok) toast(errText(r), "error");
                    load();
                  }}
                >
                  Log out
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        <button className="warnbtn" onClick={() => confirm("Log out on every device, including this one?") && void logout(true)}>
          Log out everywhere
        </button>
      </div>
    </div>
  );
}

function Users({ me }: { me: string }) {
  const info = useStore((s) => s.authInfo);
  const tz = useStore((s) => s.prefs.timeZone);
  const min = info?.min_password_length ?? 8;
  const [rows, setRows] = useState<AccountRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [resetFor, setResetFor] = useState<string | null>(null);
  const load = () => void api<{ accounts: AccountRow[] }>("GET", "/api/accounts").then((r) => (r.ok ? setRows(r.data.accounts) : toast(errText(r), "error")));
  useEffect(load, []);
  const patch = async (u: string, body: Record<string, unknown>, done: string) => {
    const r = await api("PATCH", `/api/accounts/${encodeURIComponent(u)}`, body);
    if (!r.ok) toast(errText(r), "error");
    else toast(done);
    load();
    if (u === me) void refreshAuth();
    return r.ok;
  };
  return (
    <div className="acct-section">
      <div className="users-head">
        <p className="hint">
          Admins manage accounts, operators drive the mount, observers watch. Everyone can press E-STOP.
        </p>
        {!adding && (
          <button className="primary" onClick={() => setAdding(true)}>
            Add user
          </button>
        )}
      </div>
      {adding && (
        <AddUser
          min={min}
          onCancel={() => setAdding(false)}
          onDone={() => {
            setAdding(false);
            load();
          }}
        />
      )}
      {!rows ? (
        <p className="hint">Loading…</p>
      ) : (
        <ul className="users">
          {rows.map((a) => (
            <li key={a.username}>
              <div className="user-row">
                <Avatar user={{ kind: "account", role: a.role, username: a.username, display_name: a.display_name }} size={30} />
                <span className="user-name">
                  <strong>
                    {a.display_name}
                    {a.username === me && <span className="badge">you</span>}
                    {a.active && <span className="badge on">online</span>}
                  </strong>
                  <small>
                    @{a.username} · last login {a.last_login ? formatDateTime(a.last_login, tz) : "never"}
                  </small>
                </span>
                <select
                  value={a.role}
                  aria-label={`Role of ${a.username}`}
                  onChange={(e) => void patch(a.username, { role: e.target.value }, `${a.display_name} is now ${e.target.value}`)}
                >
                  {(info?.roles ?? ["admin", "operator", "observer"]).map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <div className="user-actions">
                <button className="linkish" onClick={() => setResetFor(resetFor === a.username ? null : a.username)}>
                  Reset password
                </button>
                <button
                  className="linkish danger"
                  onClick={async () => {
                    if (!confirm(`Delete the account ${a.username}? This cannot be undone.`)) return;
                    const r = await api("DELETE", `/api/accounts/${encodeURIComponent(a.username)}`);
                    if (!r.ok) toast(errText(r), "error");
                    else toast(`${a.username} deleted`);
                    load();
                    if (a.username === me) void refreshAuth();
                  }}
                >
                  Delete
                </button>
              </div>
              {resetFor === a.username && (
                <ResetPassword
                  min={min}
                  onSave={async (pw) => {
                    if (await patch(a.username, { password: pw }, `New password set for ${a.username}; their devices were logged out`)) setResetFor(null);
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PasswordWithGenerator({ value, onChange, min, label = "Password" }: { value: string; onChange: (v: string) => void; min: number; label?: string }) {
  const [show, setShow] = useState(false);
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <span className="pw-row">
        <input id={id} type={show ? "text" : "password"} autoComplete="new-password" value={value} onChange={(e) => onChange(e.target.value)} />
        <button
          type="button"
          onClick={() => {
            onChange(generatePassword());
            setShow(true);
          }}
        >
          Generate
        </button>
        <button type="button" className="ghost" onClick={() => setShow(!show)} aria-pressed={show}>
          {show ? "Hide" : "Show"}
        </button>
      </span>
      <small>At least {min} characters. Share it in person; the user can change it later.</small>
    </div>
  );
}

function ResetPassword({ min, onSave }: { min: number; onSave: (pw: string) => void }) {
  const [pw, setPw] = useState("");
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(pw);
      }}
    >
      <PasswordWithGenerator value={pw} onChange={setPw} min={min} label="New password" />
      <div className="row">
        <button className="primary" type="submit" disabled={pw.length < min}>
          Set password
        </button>
      </div>
    </form>
  );
}

function AddUser({ min, onCancel, onDone }: { min: number; onCancel: () => void; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [display, setDisplay] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const r = await api("POST", "/api/accounts", { username: username.trim(), display_name: display, role, password: pw });
    setBusy(false);
    if (!r.ok) return setErr(errText(r));
    toast(`Account ${username.trim().toLowerCase()} created`);
    onDone();
  };
  return (
    <form className="inline-form add-user" onSubmit={submit}>
      <h3>New user</h3>
      <Field label="User name" hint="2–32 characters: letters, digits, “.”, “_” or “-”.">
        <input autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
      </Field>
      <Field label="Display name (optional)">
        <input value={display} maxLength={40} onChange={(e) => setDisplay(e.target.value)} />
      </Field>
      <Field label="Role" hint={ROLE_TEXT[role]}>
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="operator">Operator</option>
          <option value="observer">Observer</option>
          <option value="admin">Admin</option>
        </select>
      </Field>
      <PasswordWithGenerator value={pw} onChange={setPw} min={min} />
      {err && (
        <p className="login-error" role="alert">
          {err}
        </p>
      )}
      <div className="row">
        <button className="primary" type="submit" disabled={busy || !username.trim() || pw.length < min}>
          Create user
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function SetupAdminForm({ onDone }: { onDone: () => void }) {
  const info = useStore((s) => s.authInfo);
  const min = info?.min_password_length ?? 8;
  const [username, setUsername] = useState("");
  const [display, setDisplay] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Success closes the dialog: the store then shows this browser logged in as the new admin.
  useStoreEffect(info?.user?.kind === "account", onDone);
  if (!info?.setup_allowed)
    return <p className="hint">Accounts already exist, or this device may not create the first one. Create it on the computer that runs OpenSkyMount.</p>;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(await setupAdmin(username.trim(), pw, display));
    setBusy(false);
  };
  return (
    <form className="acct-section" onSubmit={submit}>
      <div className="callout">
        <Icon name="shield" />
        <p>
          The first account is an <strong>admin</strong>. As soon as it exists, <strong>everyone has to log in</strong>, also on
          this computer; devices that are open now are asked to log in. Add more people later under Users. Forgot the admin
          password? Reset it on this computer with <code>python -m openskymount.server.accounts passwd &lt;name&gt;</code>.
        </p>
      </div>
      <Field label="User name" hint="2–32 characters: letters, digits, “.”, “_” or “-”.">
        <input autoCapitalize="none" spellCheck={false} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label="Display name (optional)">
        <input value={display} maxLength={40} onChange={(e) => setDisplay(e.target.value)} />
      </Field>
      <Field label="Password" hint={`At least ${min} characters.`}>
        <input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
      </Field>
      <Field label="Repeat password">
        <input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
      </Field>
      {pw2.length > 0 && pw !== pw2 && <p className="login-error">The passwords do not match.</p>}
      {err && (
        <p className="login-error" role="alert">
          {err}
        </p>
      )}
      <div className="row">
        <button className="primary" type="submit" disabled={busy || !username.trim() || pw.length < min || pw !== pw2}>
          Create admin account and turn on sign-in
        </button>
      </div>
    </form>
  );
}

/** Call `fn` once when `cond` becomes true. */
function useStoreEffect(cond: boolean, fn: () => void) {
  const fired = useRef(false);
  useEffect(() => {
    if (cond && !fired.current) {
      fired.current = true;
      fn();
    }
  }, [cond]);
}
