import { describe, expect, it } from "vitest";
import { vecToRadec } from "./projection";
import {
  compassPoint,
  eclipticVec,
  flightAt,
  landscapeAlt,
  makeFlight,
  mixHex,
  RETICLE,
  reticleAt,
  slerp,
  ridgeAlt,
  separation,
  shortestDelta,
} from "./scenery";

describe("ecliptic", () => {
  it("passes through the equinoxes and solstices at their catalogued J2000 positions", () => {
    // Independent reference: the solstice points lie at RA 6h / 18h, Dec ±ε, ε(J2000) = 23°26′21.406″.
    const eps = 23 + 26 / 60 + 21.406 / 3600;
    const [ra0, dec0] = vecToRadec(eclipticVec(0));
    expect(ra0).toBeCloseTo(0, 9);
    expect(dec0).toBeCloseTo(0, 9);
    const [ra90, dec90] = vecToRadec(eclipticVec(90));
    expect(ra90).toBeCloseTo(90, 9);
    expect(dec90).toBeCloseTo(eps, 6);
    const [ra270, dec270] = vecToRadec(eclipticVec(270));
    expect(ra270).toBeCloseTo(270, 9);
    expect(dec270).toBeCloseTo(-eps, 6);
  });
});

describe("decorative skyline", () => {
  it("is low, positive and seamless at azimuth 0/360", () => {
    for (let az = 0; az < 360; az += 0.05) {
      const h = landscapeAlt(az);
      expect(h).toBeGreaterThan(0.05);
      expect(h).toBeLessThan(3);
      const r = ridgeAlt(az);
      expect(r).toBeGreaterThan(0.1);
      expect(r).toBeLessThan(2.5);
    }
    expect(landscapeAlt(359.99999999)).toBeCloseTo(landscapeAlt(0), 2);
    expect(landscapeAlt(360)).toBe(landscapeAlt(0));
    expect(ridgeAlt(360)).toBeCloseTo(ridgeAlt(0), 9);
    expect(landscapeAlt(-10)).toBeCloseTo(landscapeAlt(350), 9);
  });

  it("has forest in places and open land elsewhere", () => {
    const heights = Array.from({ length: 3600 }, (_, i) => landscapeAlt(i / 10));
    const max = Math.max(...heights);
    const min = Math.min(...heights);
    expect(max - min).toBeGreaterThan(0.6);
  });
});

describe("camera flights", () => {
  it("goes the short way round in azimuth and lands exactly", () => {
    expect(shortestDelta(350, 10)).toBe(20);
    expect(shortestDelta(10, 350)).toBe(-20);
    const f = makeFlight({ az: 350, alt: 20, fov: 90 }, { az: 20, alt: 40, fov: 30 }, 1000);
    const mid = flightAt(f, 1000 + f.dur / 2);
    expect(mid.az > 350 || mid.az < 20).toBe(true);
    const end = flightAt(f, 1000 + f.dur + 5);
    expect(end).toEqual({ az: 20, alt: 40, fov: 30, done: true });
  });

  it("zooms out on long flights and jumps at once with reduced motion", () => {
    const f = makeFlight({ az: 0, alt: 10, fov: 20 }, { az: 180, alt: 10, fov: 20 }, 0);
    expect(flightAt(f, f.dur / 2).fov).toBeGreaterThan(40);
    const r = makeFlight({ az: 0, alt: 10, fov: 20 }, { az: 180, alt: 10, fov: 20 }, 0, true);
    expect(flightAt(r, 0)).toMatchObject({ az: 180, done: true });
  });

  it("measures angles on the sphere", () => {
    expect(separation({ alt: 0, az: 0 }, { alt: 0, az: 90 })).toBeCloseTo(90, 9);
    expect(separation({ alt: 90, az: 0 }, { alt: 90, az: 200 })).toBeCloseTo(0, 6);
  });
});

describe("labels and colours", () => {
  it("names compass points", () => {
    expect(compassPoint(0)).toBe("N");
    expect(compassPoint(158)).toBe("SSE");
    expect(compassPoint(359)).toBe("N");
  });
  it("mixes colours", () => {
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mixHex("#102030", "#102030", 0.3)).toBe("#102030");
  });
});

describe("selection reticle", () => {
  it("slerp stays on the unit sphere and hits both ends", () => {
    const a: [number, number, number] = [1, 0, 0];
    const b: [number, number, number] = [0, 0.6, 0.8];
    expect(slerp(a, b, 0)).toEqual(a);
    const end = slerp(a, b, 1);
    for (let i = 0; i < 3; i++) expect(end[i]).toBeCloseTo(b[i], 12);
    for (const t of [0.1, 0.5, 0.9]) {
      const v = slerp(a, b, t);
      expect(Math.hypot(...v)).toBeCloseTo(1, 12);
      // Constant angular speed: the angle from a grows linearly with t.
      expect(Math.acos(v[0])).toBeCloseTo(t * Math.PI / 2, 9);
    }
  });

  it("a fresh selection settles in from a wider, transparent ring and ripples once", () => {
    const start = reticleAt(0, 12, null);
    expect(start.alpha).toBe(0);
    expect(start.radius).toBeGreaterThan(20);
    expect(start.label).toBe(0);
    const mid = reticleAt(RETICLE.rippleDelayMs + RETICLE.rippleMs / 2, 12, null);
    expect(mid.ripple).toBeGreaterThan(12);
    expect(mid.rippleAlpha).toBeGreaterThan(0);
    const end = reticleAt(5000, 12, null);
    expect(end).toEqual({ radius: 12, alpha: 1, glide: 1, ripple: 0, rippleAlpha: 0, label: 1 });
  });

  it("switching selections glides and resizes without a ripple", () => {
    const half = reticleAt(RETICLE.glideMs / 2, 16, 10);
    expect(half.alpha).toBe(1);
    expect(half.glide).toBeCloseTo(0.5, 6);
    expect(half.radius).toBeCloseTo(13, 6);
    expect(half.ripple).toBe(0);
    expect(reticleAt(RETICLE.glideMs, 16, 10).radius).toBe(16);
  });

  it("reduced motion shows the final pose at once", () => {
    expect(reticleAt(0, 12, 30, true)).toEqual({ radius: 12, alpha: 1, glide: 1, ripple: 0, rippleAlpha: 0, label: 1 });
  });
});
