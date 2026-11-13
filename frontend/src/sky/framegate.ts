// Frame pacing for the canvas render loops. Drawing the sky 60 times a second while nothing moves keeps a CPU core
// busy for no visible gain, so a loop draws when an input changed or something animates, and only ticks slowly
// otherwise.
import { SIDEREAL_RATE_RAD_S } from "./projection";

/** Slow decorative motion (the dashes around a tracked target) is drawn at this interval, about 20 frames/s. */
export const AMBIENT_MS = 50;
/**
 * An unchanged map is still presented this often, so an input the loop does not watch (the fading mount trail,
 * say) cannot go stale for long.
 */
export const HEARTBEAT_MS = 1000;
/** The cached sky is redrawn once Earth's rotation has moved it this far (px). */
export const DRIFT_PX = 0.4;

/** How the current frame animates: "full" every display frame, "ambient" at AMBIENT_MS, "none" not at all. */
export type Motion = "none" | "ambient" | "full";

/**
 * Upper bound (px) of how far the sky has turned in `simDtSeconds` of simulated time at projection scale `scale`
 * (px per radian at the view centre). The factor 2 is the stereographic stretch at the edge of a 170° view.
 */
export function skyDriftPx(simDtSeconds: number, scale: number): number {
  return Math.abs(simDtSeconds) * SIDEREAL_RATE_RAD_S * scale * 2;
}

/** Whether to put a frame on screen: always on a change or full motion, else by the ambient or heartbeat interval. */
export function shouldPresent(changed: boolean, motion: Motion, sinceLastMs: number): boolean {
  if (changed || motion === "full") return true;
  return sinceLastMs >= (motion === "ambient" ? AMBIENT_MS : HEARTBEAT_MS);
}

/**
 * `value * perUnit` rounded to a whole step, for change detection. Adds 0 so that -0 (a tiny negative rounds to it)
 * compares equal to 0 under `Object.is`; without that, noise around zero would look like a change on every frame.
 */
export function quantize(value: number, perUnit: number): number {
  return Math.round(value * perUnit) + 0;
}

/** Remembers the previous list of values and reports whether the next list differs. */
export class ChangeTracker {
  private last: readonly unknown[] | null = null;

  /** True when any value differs from the previous call; the first call always reports a change. */
  changed(values: readonly unknown[]): boolean {
    const prev = this.last;
    this.last = values;
    if (!prev || prev.length !== values.length) return true;
    for (let i = 0; i < values.length; i++) if (!Object.is(prev[i], values[i])) return true;
    return false;
  }
}
