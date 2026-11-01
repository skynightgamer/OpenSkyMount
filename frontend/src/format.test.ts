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

describe("display preferences", () => {
  it("formats angles in both modes with fixed width", async () => {
    const { angleFmt } = await import("./format");
    const dms = angleFmt({ angles: "dms", timeZone: "UTC" });
    const dec = angleFmt({ angles: "decimal", timeZone: "UTC" });
    expect(dms.ra(279.23473479)).toBe("18h 36m 56.3s");
    expect(dms.ra(359.99999)).toBe("00h 00m 00.0s");
    expect(dec.ra(279.23473479)).toBe("279.2347°");
    expect(dms.az(5.5)).toBe(`${F}${F}5° 30′ 00″`);
    expect(dms.az(194.3336).length).toBe(dms.az(5).length);
    expect(dec.dec(-5.25)).toBe(`${F}−5.2500°`);
    expect(dms.alt(75.959)).toBe("+75° 57′ 32″");
  });
  it("round-trips datetime-local values through a time zone, including DST", async () => {
    const { toZonedInput, fromZonedInput, formatTime } = await import("./format");
    const t = Date.UTC(2026, 10, 1, 1, 0) / 1000; // 2026-11-01 01:00 UTC
    expect(toZonedInput(t, "UTC")).toBe("2026-11-01T01:00");
    expect(toZonedInput(t, "Europe/Berlin")).toBe("2026-11-01T02:00"); // CET, UTC+1
    expect(toZonedInput(t, "America/New_York")).toBe("2026-10-31T21:00"); // still EDT, UTC-4
    expect(fromZonedInput("2026-11-01T02:00", "Europe/Berlin")).toBe(t);
    expect(fromZonedInput("2026-07-01T12:00", "Europe/Berlin")).toBe(Date.UTC(2026, 6, 1, 10, 0) / 1000);
    expect(fromZonedInput("garbage", "UTC")).toBeNaN();
    expect(formatTime(t, "Asia/Tokyo")).toMatch(/10:00/);
  });
});
