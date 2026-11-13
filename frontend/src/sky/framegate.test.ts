import { describe, expect, it } from "vitest";
import { AMBIENT_MS, ChangeTracker, DRIFT_PX, HEARTBEAT_MS, quantize, shouldPresent, skyDriftPx } from "./framegate";
import { SIDEREAL_RATE_RAD_S } from "./projection";

describe("skyDriftPx", () => {
  it("is proportional to elapsed simulated time and to the zoom", () => {
    expect(skyDriftPx(10, 500)).toBeCloseTo(2 * skyDriftPx(5, 500), 12);
    expect(skyDriftPx(10, 1000)).toBeCloseTo(2 * skyDriftPx(10, 500), 12);
    expect(skyDriftPx(-7, 500)).toBeCloseTo(skyDriftPx(7, 500), 12);
  });

  it("bounds the real movement of a star at the view centre, at the rate of Earth's rotation", () => {
    // 1 s of a star on the celestial equator moves SIDEREAL_RATE rad; at the centre 1 rad is `scale` px.
    expect(skyDriftPx(1, 500)).toBeGreaterThanOrEqual(SIDEREAL_RATE_RAD_S * 500);
  });

  it("stays under the redraw threshold for seconds at real-time rate, but not at high time rates", () => {
    const scale = 600; // a typical 100° view on a desktop canvas
    expect(skyDriftPx(1, scale)).toBeLessThan(DRIFT_PX); // 1 s at 1x
    expect(skyDriftPx(60, scale)).toBeGreaterThan(DRIFT_PX); // a minute later the sky has to be redrawn
    expect(skyDriftPx(1000 / 60, scale)).toBeGreaterThan(DRIFT_PX); // one display frame at 1000x: redraw each frame
  });
});

describe("shouldPresent", () => {
  it("always presents a change and full motion", () => {
    expect(shouldPresent(true, "none", 0)).toBe(true);
    expect(shouldPresent(false, "full", 0)).toBe(true);
  });

  it("holds ambient motion to the ambient interval", () => {
    expect(shouldPresent(false, "ambient", AMBIENT_MS - 1)).toBe(false);
    expect(shouldPresent(false, "ambient", AMBIENT_MS)).toBe(true);
  });

  it("only ticks on the heartbeat when nothing changes or moves", () => {
    expect(shouldPresent(false, "none", 16)).toBe(false);
    expect(shouldPresent(false, "none", HEARTBEAT_MS - 1)).toBe(false);
    expect(shouldPresent(false, "none", HEARTBEAT_MS)).toBe(true);
  });
});

describe("ChangeTracker", () => {
  it("reports the first call and then only real differences", () => {
    const t = new ChangeTracker();
    const obj = {};
    expect(t.changed([1, "a", obj, null])).toBe(true);
    expect(t.changed([1, "a", obj, null])).toBe(false);
    expect(t.changed([1, "a", {}, null])).toBe(true); // a new object is a change (identity)
    expect(t.changed([1, "a", {}, null])).toBe(true);
    const same = {};
    t.changed([1, "a", same, null]);
    expect(t.changed([2, "a", same, null])).toBe(true);
    expect(t.changed([2, "a", same, null])).toBe(false);
  });

  it("treats NaN as unchanged and a different length as a change", () => {
    const t = new ChangeTracker();
    t.changed([NaN]);
    expect(t.changed([NaN])).toBe(false);
    expect(t.changed([NaN, 1])).toBe(true);
  });
});

describe("quantize", () => {
  it("rounds to whole steps", () => {
    expect(quantize(1.26, 4)).toBe(5);
    expect(quantize(-1.26, 4)).toBe(-5);
  });

  it("gives tiny negatives and positives the same zero, so noise around 0 reads as no change", () => {
    expect(Object.is(quantize(-0.001, 10), quantize(0.001, 10))).toBe(true);
    const t = new ChangeTracker();
    t.changed([quantize(-0.001, 10)]);
    expect(t.changed([quantize(0.002, 10)])).toBe(false);
  });
});
