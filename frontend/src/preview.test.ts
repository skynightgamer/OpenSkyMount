import { describe, expect, it } from "vitest";
import { cableBar, formatDuration, NEAR_LIMIT_DEG, signedDeg } from "./preview";

describe("formatDuration", () => {
  it("uses the unit that fits", () => {
    expect(formatDuration(0.4)).toBe("0 s");
    expect(formatDuration(38.2)).toBe("38 s");
    expect(formatDuration(60)).toBe("1 min");
    expect(formatDuration(130)).toBe("2 min 10 s");
    expect(formatDuration(3900)).toBe("1 h 05 min");
  });
});

describe("cableBar", () => {
  const lim = { az_min: -270, az_max: 270 };
  it("puts the limits at the ends, neutral in the middle and clamps markers", () => {
    expect(cableBar({ ...lim, now: 0, after: 270 })).toMatchObject({ now: 50, after: 100, neutral: 50 });
    expect(cableBar({ ...lim, now: -270, after: -400 })).toMatchObject({ now: 0, after: 0 });
    expect(cableBar({ ...lim, now: 135, after: -135 })).toMatchObject({ now: 75, after: 25 });
  });
  it("sizes the warning zone like the server's margin", () => {
    expect(cableBar({ ...lim, now: 0, after: 0 }).zone).toBeCloseTo((NEAR_LIMIT_DEG / 540) * 100);
  });
  it("copes with asymmetric limits", () => {
    const b = cableBar({ az_min: -90, az_max: 270, now: 0, after: 90 });
    expect(b.neutral).toBeCloseTo(25);
    expect(b.after).toBeCloseTo(50);
  });
});

describe("signedDeg", () => {
  it("rounds and signs with a typographic minus", () => {
    expect(signedDeg(120.4)).toBe("+120°");
    expect(signedDeg(-12.6)).toBe("−13°");
    expect(signedDeg(0.2)).toBe("0°");
  });
});
