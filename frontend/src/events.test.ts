import { describe, expect, it } from "vitest";
import { type SkyEvent, sessionWindow, untilText } from "./events";

const ev = (kind: string, time: number): SkyEvent => ({
  id: kind, kind, time, title: "x", detail: "", target: "moon", importance: 2, alt: 30, sun_alt: -20, visible: true,
});

describe("untilText", () => {
  const now = 1_000_000;
  it("says how far away an event is in the unit that matters", () => {
    expect(untilText(now + 20, now)).toBe("now");
    expect(untilText(now + 600, now)).toBe("in 10 min");
    expect(untilText(now + 5 * 3600, now)).toBe("in 5 h");
    expect(untilText(now + 3 * 86400, now)).toBe("in 3 d");
  });
  it("keeps an event that has just started as now and calls older ones past", () => {
    expect(untilText(now - 1800, now)).toBe("now");
    expect(untilText(now - 7200, now)).toBe("past");
  });
});

describe("sessionWindow", () => {
  it("starts a little before the event and watches a shower for an hour, anything else for half an hour", () => {
    expect(sessionWindow(ev("conjunction", 5000))).toEqual({ start_at: 4700, duration_s: 1800 });
    expect(sessionWindow(ev("meteor_shower", 5000))).toEqual({ start_at: 4700, duration_s: 3600 });
  });
});
