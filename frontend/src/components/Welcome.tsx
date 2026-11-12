// First-run welcome screen and the decorative starfield shared with the login screen.
import { useEffect, useRef } from "react";
import { useStore } from "../store";
import { Icon, Logo, type IconName } from "./Icon";

const WELCOME_KEY = "osm-welcome";

/** True until this browser has dismissed the welcome screen once. */
export function welcomeDue(): boolean {
  try {
    return localStorage.getItem(WELCOME_KEY) !== "1";
  } catch {
    return false; // no storage (private mode): do not nag on every load
  }
}

export function markWelcomeSeen(): void {
  try {
    localStorage.setItem(WELCOME_KEY, "1");
  } catch {
    /* per-device preference only */
  }
}

// ------------------------------------------------------------------ starfield
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/**
 * Decorative night sky (random stars, a soft Milky Way band, slow twinkle). Purely ornamental: it is not the real
 * sky, which the sky map shows. Static when the user prefers reduced motion.
 */
export function Starfield({ className = "" }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rand = rng(20261112);
    const stars = Array.from({ length: 900 }, () => {
      const m = rand();
      return {
        x: rand(),
        y: rand(),
        r: 0.35 + 1.5 * m ** 6,
        a: 0.25 + 0.75 * m ** 2,
        hue: rand(),
        ph: rand() * 6.28,
        f: 0.6 + 1.8 * rand(),
      };
    });
    // A few extra stars concentrated along the band.
    for (let i = 0; i < 700; i++) {
      const t = rand();
      const off = (rand() + rand() + rand() - 1.5) * 0.12;
      stars.push({ x: t, y: 0.82 - 0.62 * t + off, r: 0.3 + 0.6 * rand() ** 4, a: 0.2 + 0.5 * rand(), hue: rand(), ph: rand() * 6.28, f: 1 });
    }
    let raf = 0;
    const draw = (tMs: number) => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const bg = ctx.createLinearGradient(0, 0, 0, h);
      bg.addColorStop(0, "#03050a");
      bg.addColorStop(0.7, "#070b14");
      bg.addColorStop(1, "#0d1422");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);
      // Milky Way band: a few blurred, low-alpha strokes along a diagonal.
      ctx.save();
      ctx.translate(0, h * 0.82);
      ctx.rotate(-Math.atan2(h * 0.62, w));
      const len = Math.hypot(w, h * 0.62);
      for (const [width, alpha] of [[h * 0.34, 0.035], [h * 0.18, 0.05], [h * 0.07, 0.05]] as const) {
        const g = ctx.createLinearGradient(0, -width / 2, 0, width / 2);
        g.addColorStop(0, "rgba(160,180,220,0)");
        g.addColorStop(0.5, `rgba(185,200,235,${alpha})`);
        g.addColorStop(1, "rgba(160,180,220,0)");
        ctx.fillStyle = g;
        ctx.fillRect(-20, -width / 2, len + 40, width);
      }
      ctx.restore();
      const t = tMs / 1000;
      ctx.globalCompositeOperation = "lighter";
      for (const s of stars) {
        const tw = reduced ? 1 : 0.8 + 0.2 * Math.sin(t * s.f + s.ph);
        const c = s.hue < 0.15 ? "255,214,170" : s.hue > 0.8 ? "180,205,255" : "235,240,255";
        ctx.fillStyle = `rgba(${c},${(s.a * tw).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2);
        ctx.fill();
        if (s.r > 1.25) {
          ctx.fillStyle = `rgba(${c},${(0.12 * tw).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(s.x * w, s.y * h, s.r * 4, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalCompositeOperation = "source-over";
      // Horizon glow and a low hill line at the bottom.
      const hz = ctx.createLinearGradient(0, h * 0.78, 0, h);
      hz.addColorStop(0, "rgba(40,70,120,0)");
      hz.addColorStop(1, "rgba(60,95,150,0.22)");
      ctx.fillStyle = hz;
      ctx.fillRect(0, h * 0.78, w, h * 0.22);
      ctx.fillStyle = "#03050a";
      ctx.beginPath();
      ctx.moveTo(0, h);
      for (let x = 0; x <= w; x += 6) {
        const u = x / w;
        ctx.lineTo(x, h - 26 - 14 * Math.sin(u * 7 + 1) - 8 * Math.sin(u * 19 + 0.3) - 4 * Math.sin(u * 47));
      }
      ctx.lineTo(w, h);
      ctx.fill();
      if (!reduced) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className={`starfield ${className}`} aria-hidden="true" />;
}

// ------------------------------------------------------------------ welcome screen
const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: "sparkle", title: "A live sky", text: "Stars to magnitude 8, the Milky Way, planets, the Moon's phase, satellites and comets, computed for your site." },
  { icon: "target", title: "A virtual mount", text: "A 3D alt-az mount with real physics: speed limits, backlash, latency, cable wrap and faults you can inject." },
  { icon: "compass", title: "GoTo and tracking", text: "Point at any object, follow it across the sky, queue an observing session and replay the telemetry." },
  { icon: "shield", title: "Safe remote control", text: "One device drives at a time, E-STOP works from every screen, and optional accounts keep the controls private." },
];

export function WelcomeScreen({
  onClose,
  onStartGuide,
  onCreateAccount,
}: {
  onClose: () => void;
  onStartGuide: () => void;
  onCreateAccount: () => void;
}) {
  const info = useStore((s) => s.authInfo);
  const simulated = useStore((s) => s.config?.capabilities.simulated ?? true);
  const primary = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    primary.current?.focus();
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const user = info?.user;
  return (
    <div className="welcome" role="dialog" aria-modal="true" aria-labelledby="welcome-title">
      <Starfield />
      <div className="welcome-card">
        <button className="ghost welcome-close" onClick={onClose} aria-label="Close welcome screen">
          <Icon name="close" />
        </button>
        <div className="welcome-hero">
          <Logo size={44} />
          <div>
            <p className="welcome-kicker">{user?.kind === "account" ? `Welcome back, ${user.display_name}` : "Welcome"}</p>
            <h1 id="welcome-title">OpenSkyMount</h1>
          </div>
        </div>
        <p className="welcome-lead">
          Plan a night, point a telescope and track the sky with a motorised alt-az mount, right in your browser.
        </p>
        <ul className="welcome-features">
          {FEATURES.map((f) => (
            <li key={f.title}>
              <span className="wf-icon">
                <Icon name={f.icon} size={18} />
              </span>
              <span>
                <strong>{f.title}</strong>
                <small>{f.text}</small>
              </span>
            </li>
          ))}
        </ul>
        {simulated && (
          <p className="welcome-note">
            <span className="dot" /> Running on the built-in simulator: no hardware needed. Real-hardware behaviour is not verified yet.
          </p>
        )}
        <AccountNote onCreateAccount={onCreateAccount} />
        <div className="welcome-actions">
          <button ref={primary} className="primary big" onClick={onStartGuide}>
            <Icon name="compass" />
            Start the setup guide
          </button>
          <button className="big" onClick={onClose}>
            Explore on my own
          </button>
        </div>
        <p className="welcome-foot">You can reopen this screen and the guide from the account menu at the top right.</p>
      </div>
    </div>
  );
}

function AccountNote({ onCreateAccount }: { onCreateAccount: () => void }) {
  const info = useStore((s) => s.authInfo);
  if (!info) return null;
  const user = info.user;
  if (user?.kind === "account")
    return (
      <p className="welcome-account">
        <Icon name="user" /> Logged in as <strong>{user.display_name}</strong> ({user.role}).
        {user.role === "observer" && " Your account is view-only: you can watch, and stop the mount, but not move it."}
      </p>
    );
  if (info.mode === "open" && info.setup_allowed)
    return (
      <div className="welcome-account">
        <Icon name="shield" />
        <span>
          Sign-in is off, which is fine on this computer. Before opening OpenSkyMount to your network, create an admin account so
          that only people you invite can move the mount.
        </span>
        <button onClick={onCreateAccount}>Create admin account</button>
      </div>
    );
  if (info.mode === "open")
    return (
      <p className="welcome-account">
        <Icon name="shield" /> Sign-in is off. Accounts can be turned on from the computer that runs OpenSkyMount.
      </p>
    );
  return null;
}
