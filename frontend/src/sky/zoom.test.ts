import { describe, expect, it } from "vitest";
import { wheelZoomFactor } from "./zoom";

describe("wheel zoom", () => {
  it("zooms in and out more quickly while preserving small trackpad movements", () => {
    expect(wheelZoomFactor(-100, 0, 600)).toBeCloseTo(0.741, 3);
    expect(wheelZoomFactor(100, 0, 600)).toBeCloseTo(1.35, 2);
    expect(wheelZoomFactor(1, 0, 600)).toBeCloseTo(1.003, 3);
  });
  it("normalizes line and page deltas and bounds a single event", () => {
    expect(wheelZoomFactor(3, 1, 600)).toBeCloseTo(wheelZoomFactor(48, 0, 600));
    expect(wheelZoomFactor(1, 2, 600)).toBeCloseTo(wheelZoomFactor(240, 0, 600));
    expect(wheelZoomFactor(-10000, 0, 600)).toBeCloseTo(wheelZoomFactor(-240, 0, 600));
  });
});
