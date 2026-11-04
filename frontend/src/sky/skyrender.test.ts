/// <reference types="node" />
import { readFileSync } from "node:fs";
import { beforeAll, describe, expect, it } from "vitest";
import { altazToVec, basis, project, radecToVec, type Vec3 } from "./projection";
import { limbAngle, milkyWayVisibility, sampleMap, skyColor, type MwMap, type RGB, type SkyLight } from "./skyrender";

/** Minimal PNG decoder for the 8-bit greyscale, non-interlaced Milky Way map . */
async function decodeGreyPng(path: string): Promise<MwMap> {
  const buf = new Uint8Array(readFileSync(path));
  const dv = new DataView(buf.buffer);
  let off = 8;
  let w = 0;
  let h = 0;
  const idat: Uint8Array[] = [];
  while (off < buf.length) {
    const len = dv.getUint32(off);
    const type = String.fromCharCode(...buf.subarray(off + 4, off + 8));
    const data = buf.subarray(off + 8, off + 8 + len);
    if (type === "IHDR") {
      w = dv.getUint32(off + 8);
      h = dv.getUint32(off + 12);
      expect([data[8], data[9], data[12]]).toEqual([8, 0, 0]); // 8-bit, greyscale, not interlaced
    } else if (type === "IDAT") idat.push(data);
    off += 12 + len;
  }
  const stream = new Blob(idat).stream().pipeThrough(new DecompressionStream("deflate"));
  const raw = new Uint8Array(await new Response(stream).arrayBuffer());
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (w + 1)];
    for (let x = 0; x < w; x++) {
      const v = raw[y * (w + 1) + 1 + x];
      const a = x > 0 ? out[y * w + x - 1] : 0;
      const b = y > 0 ? out[(y - 1) * w + x] : 0;
      const c = x > 0 && y > 0 ? out[(y - 1) * w + x - 1] : 0;
      let p = 0;
      if (f === 1) p = a;
      else if (f === 2) p = b;
      else if (f === 3) p = (a + b) >> 1;
      else if (f === 4) {
        const pa = Math.abs(b - c);
        const pb = Math.abs(a - c);
        const pc = Math.abs(a + b - 2 * c);
        p = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      out[y * w + x] = (v + p) & 255;
    }
  }
  return { w, h, data: out };
}

// Galactic -> J2000 equatorial (IAU 1958 definition, J2000 rotation matrix from Hipparcos vol. 1, sec. 1.5.3).
const GAL_TO_EQ = [
  [-0.0548755604, 0.4941094279, -0.867666149],
  [-0.8734370902, -0.44482963, -0.1980763734],
  [-0.4838350155, 0.7469822445, 0.4559837762],
];
function galToRadec(l: number, b: number): [number, number] {
  const g = radecToVec(l, b);
  const e = [0, 1, 2].map((i) => GAL_TO_EQ[i][0] * g[0] + GAL_TO_EQ[i][1] * g[1] + GAL_TO_EQ[i][2] * g[2]);
  const ra = ((Math.atan2(e[1], e[0]) * 180) / Math.PI + 360) % 360;
  return [ra, (Math.asin(e[2]) * 180) / Math.PI];
}

describe("Milky Way map", () => {
  let mw: MwMap;
  beforeAll(async () => {
    mw = await decodeGreyPng(new URL("./milkyway.png", import.meta.url).pathname);
  });
  const at = (l: number, b: number) => sampleMap(mw, ...galToRadec(l, b));

  it("is a 0.5° equirectangular map", () => {
    expect([mw.w, mw.h]).toEqual([720, 360]);
  });

  it("follows the galactic plane (independent reference: galactic coordinates)", () => {
    expect(galToRadec(0, 90)[0]).toBeCloseTo(192.859, 2); // north galactic pole
    expect(galToRadec(0, 90)[1]).toBeCloseTo(27.128, 2);
    let plane = 0;
    let high = 0;
    let n = 0;
    for (let l = 0; l < 360; l += 5) {
      plane += Math.max(at(l, -2), at(l, 0), at(l, 2));
      high += at(l, 40) + at(l, -40);
      n++;
    }
    expect(plane / n).toBeGreaterThan(0.2);
    expect(high / (2 * n)).toBeLessThan(0.01);
    expect(at(0, 90)).toBe(0);
    expect(at(0, -90)).toBe(0);
  });

  it("is brightest toward the Sagittarius / Scutum star clouds, faint toward the anticentre", () => {
    expect(at(2, -5)).toBeGreaterThan(0.5); // Large Sagittarius Star Cloud (l ≈ 1.5°, b ≈ -5°)
    expect(at(27.5, -2.5)).toBeGreaterThan(0.5); // Scutum Star Cloud
    expect(at(180, 0)).toBeLessThan(0.35);
  });

  it("wraps in RA", () => {
    expect(sampleMap(mw, 359.9, 60)).toBeCloseTo(sampleMap(mw, -0.1, 60), 9);
  });
});

describe("sky colour model", () => {
  const light = (sunAlt: number, sunAz = 270, moon?: [number, number, number]): SkyLight => ({
    sun: altazToVec(sunAlt, sunAz),
    sunAlt,
    moon: moon ? altazToVec(moon[0], moon[1]) : null,
    moonAlt: moon ? moon[0] : -90,
    moonIllum: moon ? moon[2] : 0,
  });
  const col = (alt: number, az: number, l: SkyLight): RGB => skyColor(altazToVec(alt, az), alt, l, [0, 0, 0]);
  const lum = (c: RGB) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];

  it("night: dark zenith, brighter horizon", () => {
    const l = light(-40);
    expect(lum(col(90, 0, l))).toBeLessThan(10);
    expect(lum(col(2, 0, l))).toBeGreaterThan(lum(col(90, 0, l)));
  });

  it("twilight glows warm toward the Sun", () => {
    const l = light(-6, 270);
    const west = col(3, 270, l);
    const east = col(3, 90, l);
    expect(lum(west)).toBeGreaterThan(lum(east) * 2);
    expect(west[0]).toBeGreaterThan(west[2]); // reddish
  });

  it("day is blue and much brighter than night", () => {
    const day = col(60, 0, light(30));
    expect(day[2]).toBeGreaterThan(day[0]);
    expect(lum(day)).toBeGreaterThan(10 * lum(col(60, 0, light(-40))));
  });

  it("a full Moon brightens the night sky and hides most of the Milky Way", () => {
    const moonlit = light(-40, 270, [40, 180, 1]);
    expect(lum(col(40, 185, moonlit))).toBeGreaterThan(lum(col(40, 185, light(-40))) + 5);
    expect(milkyWayVisibility(light(-40))).toBe(1);
    expect(milkyWayVisibility(moonlit)).toBeLessThan(0.5);
    expect(milkyWayVisibility(light(-3))).toBe(0);
  });
});

describe("Moon bright limb", () => {
  it("points toward the Sun on screen", () => {
    const b = basis({ az: 180, alt: 30, fov: 90, width: 800, height: 600 });
    const proj = (v: Vec3) => project(b, v, -1);
    // Sun straight below the Moon: bright limb points down (+y on the canvas).
    expect(limbAngle(proj, altazToVec(30, 180), altazToVec(-20, 180))).toBeCloseTo(Math.PI / 2, 2);
    // Sun to the west (screen right when facing south): bright limb points right.
    const a = limbAngle(proj, altazToVec(30, 180), altazToVec(30, 200))!;
    expect(Math.cos(a)).toBeGreaterThan(0.9);
  });
});
