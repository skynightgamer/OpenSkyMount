// Manual slews from a game controller (browser Gamepad API, "standard" button layout).
//
// Dead man: the mount only moves while a shoulder button (LB or RB) is held. The rates are re-sent every
// 250 ms while held; the server stops the slew if refreshes stop for 1 s (tab hidden, controller unplugged,
// connection lost). Releasing the button sends an explicit zero rate.
//
//   LB / RB held   enable motion        left stick / D-pad   alt (up/down) and az (left/right)
//   B              Stop                 Back / View / Select  E-STOP
//   Y / X          next / previous speed step
import { useEffect, useRef } from "react";
import { command, getState, setState } from "./store";

export const DEADZONE = 0.15;
export const REFRESH_MS = 250;
const BTN = { A: 0, B: 1, X: 2, Y: 3, LB: 4, RB: 5, BACK: 8, UP: 12, DOWN: 13, LEFT: 14, RIGHT: 15 } as const;

export interface PadState {
  axes: readonly number[];
  buttons: readonly { pressed: boolean; value?: number }[];
}

export interface PadIntent {
  /** Dead-man button held. */
  held: boolean;
  /** Requested rates in deg/s (zero unless held). */
  alt: number;
  az: number;
}

/** Stick value -> -1..1 with a dead zone and a quadratic response for fine control near the centre. */
export function shapeAxis(v: number, deadzone = DEADZONE): number {
  if (!Number.isFinite(v)) return 0;
  const a = Math.min(1, Math.abs(v));
  if (a <= deadzone) return 0;
  const x = (a - deadzone) / (1 - deadzone);
  return Math.sign(v) * x * x;
}

const pressed = (p: PadState, i: number) => !!p.buttons[i]?.pressed;

/** Map one gamepad reading to slew rates. ``speed`` is the full-deflection rate (deg/s). */
export function padIntent(p: PadState, speed: number): PadIntent {
  const held = pressed(p, BTN.LB) || pressed(p, BTN.RB);
  if (!held) return { held, alt: 0, az: 0 };
  let az = shapeAxis(p.axes[0] ?? 0);
  let alt = -shapeAxis(p.axes[1] ?? 0); // stick up is negative on the standard layout
  // The D-pad overrides the stick with full speed in its direction.
  if (pressed(p, BTN.UP) || pressed(p, BTN.DOWN)) alt = pressed(p, BTN.UP) ? 1 : -1;
  if (pressed(p, BTN.LEFT) || pressed(p, BTN.RIGHT)) az = pressed(p, BTN.RIGHT) ? 1 : -1;
  return { held, alt: alt * speed + 0, az: az * speed + 0 }; // "+ 0" turns -0 into 0
}

/** Should a new manual command be sent now? (changed noticeably, or the dead-man refresh is due) */
export function needsSend(prev: PadIntent | null, next: PadIntent, sinceMs: number, speed: number): boolean {
  const moving = next.alt !== 0 || next.az !== 0;
  const wasMoving = !!prev && (prev.alt !== 0 || prev.az !== 0);
  if (!moving) return wasMoving; // one explicit zero when motion ends
  if (!wasMoving) return true;
  const tol = 0.05 * speed;
  if (Math.abs(next.alt - prev!.alt) > tol || Math.abs(next.az - prev!.az) > tol) return true;
  return sinceMs >= REFRESH_MS;
}

export interface GamepadStatus {
  id: string | null;
  held: boolean;
  moving: boolean;
}

/** Polls the first connected gamepad. Motion only while ``enabled`` (this client has control, nothing latched);
 * Stop and E-STOP always work. The status is published as ``state.gamepad``. */
export function useGamepad(enabled: boolean, speeds: number[]): void {
  const ref = useRef({ enabled, speeds });
  ref.current = { enabled, speeds };
  const setStatus = (g: GamepadStatus) => setState({ gamepad: g });
  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.getGamepads) return;
    let raf = 0;
    let last: PadIntent | null = null;
    let lastSent = 0;
    let prevButtons: boolean[] = [];
    let shown: GamepadStatus = { id: null, held: false, moving: false };
    const halt = () => {
      if (last && (last.alt !== 0 || last.az !== 0)) command("manual", { alt_rate: 0, az_rate: 0 }, true);
      last = null;
    };
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const pad = Array.from(navigator.getGamepads()).find((g) => g && g.connected) ?? null;
      const { enabled: en, speeds: steps } = ref.current;
      const sp = getState().manualSpeed;
      if (!pad) {
        halt();
        if (shown.id !== null) setStatus((shown = { id: null, held: false, moving: false }));
        return;
      }
      const btn = pad.buttons.map((b) => b.pressed);
      const edge = (i: number) => btn[i] && !prevButtons[i];
      if (edge(BTN.BACK)) command("estop");
      else if (edge(BTN.B)) command("stop");
      if (edge(BTN.Y) || edge(BTN.X)) {
        const i = Math.max(0, steps.indexOf(sp));
        setState({ manualSpeed: steps[Math.max(0, Math.min(steps.length - 1, i + (edge(BTN.Y) ? 1 : -1)))] });
      }
      prevButtons = btn;
      const intent = en ? padIntent(pad, sp) : { held: false, alt: 0, az: 0 };
      const now = performance.now();
      if (needsSend(last, intent, now - lastSent, sp)) {
        command("manual", { alt_rate: intent.alt, az_rate: intent.az }, true);
        lastSent = now;
      }
      last = intent;
      const moving = intent.alt !== 0 || intent.az !== 0;
      if (shown.id !== pad.id || shown.held !== intent.held || shown.moving !== moving) {
        setStatus((shown = { id: pad.id, held: intent.held, moving }));
      }
    };
    const onHide = () => {
      if (document.hidden) halt();
    };
    raf = requestAnimationFrame(loop);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onHide);
      halt();
    };
  }, []);
}
