import { describe, expect, it } from "vitest";
import { DEADZONE, REFRESH_MS, needsSend, padIntent, shapeAxis, type PadState } from "./gamepad";

function pad(axes: number[], pressed: number[] = []): PadState {
  return { axes, buttons: Array.from({ length: 17 }, (_, i) => ({ pressed: pressed.includes(i) })) };
}

describe("gamepad mapping", () => {
  it("applies a dead zone and a quadratic response", () => {
    expect(shapeAxis(DEADZONE * 0.9)).toBe(0);
    expect(shapeAxis(-0.05)).toBe(0);
    expect(shapeAxis(1)).toBe(1);
    expect(shapeAxis(-1)).toBe(-1);
    expect(shapeAxis(1.3)).toBe(1); // out-of-range readings are clamped
    expect(shapeAxis(NaN)).toBe(0);
    const half = shapeAxis(0.575); // halfway between dead zone and full
    expect(half).toBeCloseTo(0.25, 2);
  });

  it("moves only while a shoulder button is held (dead man)", () => {
    expect(padIntent(pad([1, -1]), 2)).toEqual({ held: false, alt: 0, az: 0 });
    const lb = padIntent(pad([1, -1], [4]), 2);
    expect(lb.held).toBe(true);
    expect(lb.az).toBeCloseTo(2);
    expect(lb.alt).toBeCloseTo(2); // stick up (negative y) raises the altitude
    expect(padIntent(pad([0, 0], [5]), 2)).toMatchObject({ held: true, alt: 0, az: 0 });
  });

  it("uses the D-pad at full speed", () => {
    expect(padIntent(pad([0, 0], [5, 12]), 0.5)).toMatchObject({ alt: 0.5, az: 0 });
    expect(padIntent(pad([0.9, 0], [4, 14]), 0.5)).toMatchObject({ alt: 0, az: -0.5 });
    expect(padIntent(pad([0, 0], [13, 15]), 0.5)).toMatchObject({ held: false, alt: 0, az: 0 }); // not held
  });

  it("refreshes while moving and sends one stop when motion ends", () => {
    const still = { held: true, alt: 0, az: 0 };
    const move = { held: true, alt: 1, az: 0 };
    expect(needsSend(null, still, 0, 2)).toBe(false);
    expect(needsSend(still, move, 0, 2)).toBe(true);
    expect(needsSend(move, move, REFRESH_MS - 1, 2)).toBe(false);
    expect(needsSend(move, move, REFRESH_MS, 2)).toBe(true);
    expect(needsSend(move, { ...move, alt: 1.5 }, 10, 2)).toBe(true);
    expect(needsSend(move, { held: false, alt: 0, az: 0 }, 10, 2)).toBe(true);
    expect(needsSend({ held: false, alt: 0, az: 0 }, { held: false, alt: 0, az: 0 }, 1000, 2)).toBe(false);
  });
});
