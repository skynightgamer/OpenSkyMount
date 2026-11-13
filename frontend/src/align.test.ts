import { describe, expect, it } from "vitest";
import { type AlignStar, arcText, centringOffset, nextStarIndex, suspectPoint } from "./align";

const star = (name: string): AlignStar => ({ id: name, name, magnitude: 1, alt: 40, az: 100, constellation: null });

describe("nextStarIndex", () => {
  const stars = [star("A"), star("B"), star("C")];
  it("walks through the stars as alignment points appear, in any order", () => {
    expect(nextStarIndex(stars, [])).toBe(0);
    expect(nextStarIndex(stars, [{ label: "A" }])).toBe(1);
    expect(nextStarIndex(stars, [{ label: "B" }, { label: "A" }])).toBe(2);
    expect(nextStarIndex(stars, [{ label: "A" }, { label: "B" }, { label: "C" }, { label: "other" }])).toBe(-1);
  });
  it("comes back to a star whose point was removed to be redone", () => {
    expect(nextStarIndex(stars, [{ label: "A" }, { label: "C" }])).toBe(1);
  });
});

describe("centringOffset", () => {
  it("is null without simulator truth or a target", () => {
    expect(centringOffset(null, { alt: 10, az: 10 })).toBeNull();
    expect(centringOffset({ alt: 10, az: 10 }, null)).toBeNull();
  });
  it("gives arc minutes, scaling azimuth by cos(altitude)", () => {
    const o = centringOffset({ alt: 60.1, az: 100.2 }, { alt: 60, az: 100 })!;
    expect(o.alt).toBeCloseTo(6, 6);
    expect(o.az).toBeCloseTo(0.2 * 60 * 0.5, 6); // cos 60° = 0.5
    expect(o.total).toBeCloseTo(Math.hypot(6, 6), 6);
  });
  it("takes the short way across north", () => {
    expect(centringOffset({ alt: 0, az: 359.9 }, { alt: 0, az: 0.1 })!.az).toBeCloseTo(-12, 6);
  });
});

describe("suspectPoint", () => {
  const pts = (...r: number[]) => r.map((residual, i) => ({ label: `p${i}`, residual }));
  it("needs three points", () => {
    expect(suspectPoint(pts(0.5, 0.01))).toBe(-1);
  });
  it("accepts a consistent set and flags a clear outlier", () => {
    expect(suspectPoint(pts(0.01, 0.012, 0.011, 0.013))).toBe(-1);
    expect(suspectPoint(pts(0.01, 0.012, 0.2, 0.011))).toBe(2);
  });
  it("ignores outliers that are below three arc minutes", () => {
    expect(suspectPoint(pts(0.001, 0.001, 0.02))).toBe(-1);
  });
});

describe("arcText", () => {
  it("switches from arc seconds to arc minutes", () => {
    expect(arcText(0.005)).toBe("18″");
    expect(arcText(0.05)).toBe("3.0′");
    expect(arcText(null)).toBe("—");
  });
});
