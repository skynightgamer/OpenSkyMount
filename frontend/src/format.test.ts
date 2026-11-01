import { describe, expect, it } from "vitest";
import { FIGURE_SPACE as F, fixedAngle, fixedDMS, fixedNum } from "./format";

describe("fixed-width readouts", () => {
  it("keeps a constant length for any value in range", () => {
    const lens = new Set([-89.999, -5, -0.0001, 0, 3.2, 45.123, 89.9].map((v) => fixedNum(v, 2, 3, true).length));
    expect(lens.size).toBe(1);
    const az = new Set([0, 5.5, 99.12, 359.999].map((v) => fixedNum(v, 3, 3).length));
    expect(az.size).toBe(1);
    expect(fixedNum(null, 3, 3)).toHaveLength(7);
  });
  it("formats sign and padding", () => {
    expect(fixedNum(5, 2, 3, true)).toBe(`${F}+5.000`);
    expect(fixedNum(-43.3031, 2, 3, true)).toBe("−43.303");
    expect(fixedNum(-0.0001, 2, 3, true)).toBe(`${F}+0.000`);
    expect(fixedNum(7.5, 3, 1)).toBe(`${F}${F}7.5`);
  });
  it("angles pick a unit and stay 7 wide", () => {
    expect(fixedAngle(1 / 3600)).toBe(`${F}${F}${F}1.0″`);
    expect(fixedAngle(0.5)).toBe(`${F}30.00′`);
    expect(fixedAngle(55.54)).toBe(`${F}55.54°`);
    for (const v of [0, 0.001, 0.2, 3, 120, null]) expect(fixedAngle(v)).toHaveLength(7);
  });
  it("DMS is fixed width and rounds carries correctly", () => {
    expect(fixedDMS(60.2061)).toBe("+60° 12′ 22″");
    expect(fixedDMS(-5.5)).toBe(`${F}−5° 30′ 00″`);
    expect(fixedDMS(38.99999)).toBe("+39° 00′ 00″");
    expect(fixedDMS(-89.5)).toHaveLength(fixedDMS(1).length);
  });
});
