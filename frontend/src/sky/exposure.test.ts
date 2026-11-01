import { describe, expect, it } from "vitest";
import {
  CAMERA_PRESETS,
  fieldOfView,
  formatSeconds,
  maxExposureFieldRotation,
  maxExposureUntracked,
  pixelScale,
} from "./exposure";

const phone = CAMERA_PRESETS.phone.cam;

describe("exposure planner", () => {
  it("computes image scale and field of view", () => {
    expect(pixelScale(phone)).toBeCloseTo((206.265 * 1.2) / 6.9, 6); // 35.87"/px
    expect(fieldOfView(9.8, 6.9)).toBeCloseTo(70.76, 1);
    expect(fieldOfView(36, 200)).toBeCloseTo(10.29, 2);
  });

  it("limits tracked exposures by field rotation at the frame corner", () => {
    // 2.4"/s rotation; corner = hypot(9.8, 7.4)/2 mm = 5117 px; 1.5 px allowed -> 25.2 s.
    const t = maxExposureFieldRotation(2.4 / 3600, phone, 1.5);
    expect(t).toBeCloseTo(25.2, 1);
    expect(maxExposureFieldRotation(-2.4 / 3600, phone, 1.5)).toBeCloseTo(t, 9); // sign irrelevant
    expect(maxExposureFieldRotation(0, phone, 1.5)).toBe(Infinity);
    // Longer focal length at the same sensor does not change the rotation limit (it is angular in the frame).
    expect(maxExposureFieldRotation(2.4 / 3600, { ...phone, focal: 50 }, 1.5)).toBeCloseTo(t, 9);
  });

  it("limits untracked exposures by sidereal drift", () => {
    // Equator: 1.5 px * 35.87"/px / 15.041"/s = 3.58 s; at dec 60 twice as long.
    expect(maxExposureUntracked(0, phone, 1.5)).toBeCloseTo(3.577, 2);
    expect(maxExposureUntracked(60, phone, 1.5)).toBeCloseTo(2 * 3.577, 2);
    expect(maxExposureUntracked(90, phone, 1.5)).toBe(Infinity);
  });

  it("formats durations", () => {
    expect(formatSeconds(0.5)).toBe("0.50 s");
    expect(formatSeconds(25.2)).toBe("25 s");
    expect(formatSeconds(125)).toBe("2 min 05 s");
    expect(formatSeconds(Infinity)).toBe("no limit");
  });
});
