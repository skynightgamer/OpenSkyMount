import { describe, expect, it } from "vitest";
import {
  advanceMatrix,
  altazToVec,
  basis,
  formatDMS,
  formatRA,
  project,
  radecToVec,
  unproject,
  vecToAltaz,
  vecToRadec,
  separationDeg,
  type Mat3,
} from "./projection";

describe("projection", () => {
  it("round-trips alt/az and ra/dec", () => {
    const [alt, az] = vecToAltaz(altazToVec(23.5, 301.25));
    expect(alt).toBeCloseTo(23.5, 9);
    expect(az).toBeCloseTo(301.25, 9);
    const [ra, dec] = vecToRadec(radecToVec(279.2347, 38.7837));
    expect(ra).toBeCloseTo(279.2347, 9);
    expect(dec).toBeCloseTo(38.7837, 9);
  });

  it("centres the view direction and inverts the projection", () => {
    const view = { az: 135, alt: 40, fov: 90, width: 800, height: 600 };
    const b = basis(view);
    const c = project(b, altazToVec(40, 135))!;
    expect(c[0]).toBeCloseTo(400, 6);
    expect(c[1]).toBeCloseTo(300, 6);
    const p = project(b, altazToVec(55, 150))!;
    const back = unproject(b, p[0], p[1]);
    expect(separationDeg(back, altazToVec(55, 150))).toBeLessThan(1e-9);
  });

  it("puts east to the right and up at the top when facing south", () => {
    const b = basis({ az: 180, alt: 30, fov: 90, width: 800, height: 600 });
    const west = project(b, altazToVec(30, 200))!; // west of south
    const higher = project(b, altazToVec(50, 180))!;
    expect(west[0]).toBeGreaterThan(400); // facing south, west is on the right
    expect(higher[1]).toBeLessThan(300);
  });

  it("maps the field of view to the canvas", () => {
    const b = basis({ az: 0, alt: 0, fov: 90, width: 600, height: 600 });
    const edge = project(b, altazToVec(0, 45))!;
    expect(edge[0]).toBeCloseTo(600, 6);
  });

  it("advances the horizon matrix with Earth rotation (stars move westward)", () => {
    // Observer at the north pole: horizon frame = ICRS rotated, azimuth increases with hour angle.
    const m: Mat3 = [
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, 1],
    ];
    const v = radecToVec(0, 45);
    const [, az0] = vecToAltaz(advanceMatrix(m, 0).map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) as never);
    const m1 = advanceMatrix(m, 3600);
    const [alt1, az1] = vecToAltaz(m1.map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) as never);
    expect(alt1).toBeCloseTo(45, 9);
    const daz = ((az1 - az0 + 540) % 360) - 180;
    expect(Math.abs(daz)).toBeCloseTo(15.041, 2); // one sidereal hour of rotation
  });

  it("formats coordinates", () => {
    expect(formatRA(101.2872)).toBe("06h 45m 08.9s");
    expect(formatDMS(-16.7161)).toBe("−16° 42′ 58″");
  });
});
