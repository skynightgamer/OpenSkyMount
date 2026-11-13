import { describe, expect, it } from "vitest";
import { frameOutline, payloadFov, rotationText } from "./planning";
import { radecToVec, separationDeg, type Vec3 } from "./sky/projection";

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

describe("frameOutline", () => {
  const centre = radecToVec(120, 20);
  const up: Vec3 = [0, 0, 1];
  const pts = frameOutline(centre, up, 40, 30);

  it("goes round the frame with the requested number of points, all on the unit sphere", () => {
    expect(pts).toHaveLength(32);
    for (const p of pts) expect(Math.hypot(...p)).toBeCloseTo(1, 12);
  });

  it("has corners at the camera's half-diagonal from the centre", () => {
    const corners = [0, 8, 16, 24].map((i) => pts[i]);
    const diag = Math.atan(Math.hypot(Math.tan((20 * Math.PI) / 180), Math.tan((15 * Math.PI) / 180))) * (180 / Math.PI);
    for (const c of corners) expect(separationDeg(centre, c)).toBeCloseTo(diag, 6);
  });

  it("puts the top edge toward `up` and the picture's right toward the east", () => {
    const topMiddle = pts[4]; // halfway along the first (top) edge
    const bottomMiddle = pts[20];
    expect(topMiddle[2]).toBeGreaterThan(bottomMiddle[2]); // higher declination at the top
    const rightMiddle = pts[12];
    const leftMiddle = pts[28];
    const east: Vec3 = [-Math.sin((120 * Math.PI) / 180), Math.cos((120 * Math.PI) / 180), 0];
    expect(dot(rightMiddle, east)).toBeGreaterThan(dot(leftMiddle, east));
  });

  it("measures the frame width along its middle line", () => {
    expect(separationDeg(pts[28], pts[12])).toBeCloseTo(40, 6); // left and right edge midpoints
    expect(separationDeg(pts[4], pts[20])).toBeCloseTo(30, 6); // top and bottom
  });

  it("turns with `up`: a frame whose top points east has its width north-south", () => {
    const sideways = frameOutline(centre, [-Math.sin((120 * Math.PI) / 180), Math.cos((120 * Math.PI) / 180), 0], 40, 30);
    expect(Math.abs(sideways[4][2] - sideways[20][2])).toBeLessThan(Math.abs(pts[4][2] - pts[20][2]));
  });

  it("copes with an `up` along the centre", () => {
    expect(frameOutline([0, 0, 1], [0, 0, 1], 20, 20)).toHaveLength(32);
  });
});

describe("payloadFov", () => {
  it("matches the exposure planner's smartphone field of view and swaps nothing by itself", () => {
    const f = payloadFov("smartphone");
    expect(f.w).toBeCloseTo(71, 0);
    expect(f.h).toBeCloseTo(56.4, 0);
    expect(payloadFov(undefined)).toEqual(f);
    expect(payloadFov("telescope").w).toBeLessThan(3);
  });
});

describe("rotationText", () => {
  it("signs with a minus and keeps digits for small angles", () => {
    expect(rotationText(-64.5)).toBe("−65°");
    expect(rotationText(3.14)).toBe("3.1°");
    expect(rotationText(0)).toBe("0°");
    expect(rotationText(-0.0107)).toBe("−0.011°");
  });
});
