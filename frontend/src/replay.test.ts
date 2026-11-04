import { describe, expect, it } from "vitest";
import { parseRecording, sampleIndexAt, toCsv, trackingStats } from "./replay";

function s(t: number, m: string, e: number | null, id = "hip:1", te: number | null = null) {
  return JSON.stringify({ k: "s", w: t, t, m, id, ta: 10, tz: 20, a: 10, z: 20, xa: 10, xz: 20, ra: 0.001, rz: -0.002, e, te, f: [] });
}

const TEXT = [
  JSON.stringify({ k: "h", format: "openskymount-telemetry/1" }),
  s(100, "slewing", 5),
  JSON.stringify({ k: "e", t: 101, level: "info", msg: "Arrived" }),
  s(101, "tracking", 3 / 3600, "hip:1", 4 / 3600),
  s(102, "tracking", 4 / 3600, "hip:1", 4 / 3600),
  s(103, "tracking", 0 / 3600, "hip:1", 4 / 3600),
  s(300, "tracking", 10 / 3600, "planet:mars"),
  '{"k":"s","t":3', // truncated last line
].join("\n");

describe("telemetry recordings", () => {
  it("parses samples, events and the header, tolerating a truncated line", () => {
    const r = parseRecording(TEXT);
    expect(r.header.format).toBe("openskymount-telemetry/1");
    expect(r.samples).toHaveLength(5);
    expect(r.events[0].msg).toBe("Arrived");
    expect(r.skipped).toBe(1);
  });

  it("finds the sample at a time", () => {
    const { samples } = parseRecording(TEXT);
    expect(sampleIndexAt(samples, 0)).toBe(0);
    expect(sampleIndexAt(samples, 101.5)).toBe(1);
    expect(sampleIndexAt(samples, 102)).toBe(2);
    expect(sampleIndexAt(samples, 1e9)).toBe(4);
    expect(sampleIndexAt([], 5)).toBe(-1);
  });

  it("computes tracking statistics per target from tracking samples only", () => {
    const st = trackingStats(parseRecording(TEXT).samples);
    const star = st.find((x) => x.id === "hip:1")!;
    expect(star.samples).toBe(3); // the slewing sample is excluded
    expect(star.seconds).toBe(2);
    expect(star.rms).toBeCloseTo(Math.sqrt((9 + 16 + 0) / 3), 6);
    expect(star.max).toBeCloseTo(4, 6);
    expect(star.p95).toBeCloseTo(4, 6);
    expect(star.trueRms).toBeCloseTo(4, 6);
    expect(star.maxAxisRate).toBeCloseTo(0.002, 9);
    expect(st.find((x) => x.id === "planet:mars")!.trueRms).toBeNull();
  });

  it("exports CSV", () => {
    const csv = toCsv(parseRecording(TEXT).samples).trim().split("\n");
    expect(csv).toHaveLength(6);
    expect(csv[0].split(",")).toHaveLength(15);
    expect(csv[1]).toContain('"slewing"');
  });
});
