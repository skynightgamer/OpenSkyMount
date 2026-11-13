import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { angleDeg, buildFigureMesh, figureNightFactor, figureUnder, fitFigure, FIGURE_INFO, MESH, pixelToVec, skyToTangent, tangentToVec, vecToPixel, type LumaAt } from "./figures";
import { focusedFigure, triangleTransform } from "./figurerender";
import { radecToVec, separationDeg, vecToRadec } from "./projection";

interface RawCatalog {
  stars: [number, number, number, number, number, number, number, number, string, string, string][];
  constellations: { id: string; label_ra: number; label_dec: number }[];
}
const raw = JSON.parse(readFileSync(new URL("../../../backend/openskymount/data/catalog.json", import.meta.url), "utf8")) as RawCatalog;
const catalogStar = new Map<number, [number, number]>(raw.stars.map((s) => [s[0], [s[1], s[2]]]));
const ids = Object.keys(FIGURE_INFO);

describe("tangent plane", () => {
  it("round-trips sky positions, also near the pole and across RA 0", () => {
    for (const [ra0, dec0, ra, dec] of [
      [83, -5, 90, 8],
      [359, 60, 5, 62],
      [200, 85, 100, 80],
    ]) {
      const [x, y] = skyToTangent(ra0, dec0, ra, dec);
      const [ra1, dec1] = vecToRadec(tangentToVec(ra0, dec0, x, y));
      expect(separationDeg(radecToVec(ra1, dec1), radecToVec(ra, dec))).toBeLessThan(1e-9);
    }
  });
});

describe("figure artwork", () => {
  it("ships a picture and three anchor stars for each figure, positions matching the star catalog", () => {
    expect(ids.length).toBeGreaterThanOrEqual(80);
    for (const id of ids) {
      const info = FIGURE_INFO[id];
      expect(info.anchors, id).toHaveLength(3);
      expect(existsSync(new URL(`../../public/figures/${id}.png`, import.meta.url)), id).toBe(true);
      for (const [px, py, hip, ra, dec] of info.anchors) {
        expect(px >= 0 && px <= info.w && py >= 0 && py <= info.h, `${id} anchor inside picture`).toBe(true);
        const cat = catalogStar.get(hip);
        expect(cat, `${id}: HIP ${hip} in the catalog`).toBeDefined();
        // Baked positions vs the catalog (independent of the build script's rounding): well under a picture pixel.
        expect(separationDeg(radecToVec(ra, dec), radecToVec(cat![0], cat![1])), `${id} HIP ${hip}`).toBeLessThan(0.001);
      }
    }
  });

  it("lands every anchor pixel exactly on its star", () => {
    for (const id of ids) {
      const info = FIGURE_INFO[id];
      const fit = fitFigure(info)!;
      expect(fit, id).not.toBeNull();
      for (const [px, py, hip, ra, dec] of info.anchors) {
        expect(separationDeg(pixelToVec(fit, px, py), radecToVec(ra, dec)), `${id} HIP ${hip}`).toBeLessThan(1e-7);
      }
    }
  });

  it("places each picture over its own constellation (independent check against the catalog's label position)", () => {
    const labels = new Map(raw.constellations.map((c) => [c.id, radecToVec(c.label_ra, c.label_dec)]));
    for (const id of ids) {
      const mesh = buildFigureMesh(id)!;
      expect(mesh, id).not.toBeNull();
      const label = labels.get(id)!;
      // Argo Navis (Car) spans Carina, Vela and Puppis, so its centre is a little further from Carina's label.
      expect(angleDeg(mesh.centre, label), id).toBeLessThan(id === "Car" ? 40 : 25);
      expect(mesh.radiusDeg, id).toBeGreaterThan(3);
    }
  });

  it("draws every picture the same way round (as seen from Earth), so none is mirrored", () => {
    const hands = new Set(ids.map((id) => buildFigureMesh(id)!.handedness));
    expect(hands.size).toBe(1);
  });

  it("keeps the mesh lattice consistent with the picture's pixel grid", () => {
    const mesh = buildFigureMesh("Ori")!;
    expect(mesh.grid).toHaveLength((MESH + 1) ** 2);
    const info = FIGURE_INFO.Ori;
    const fit = fitFigure(info)!;
    const corner = pixelToVec(fit, info.w, 0);
    expect(separationDeg(mesh.grid[MESH], corner)).toBeLessThan(1e-9);
  });

  it("leaves a figure out rather than misplace it: unknown id or collinear anchors", () => {
    expect(buildFigureMesh("NotAFigure")).toBeNull();
    const line = { ...FIGURE_INFO.Ori, anchors: [[0, 0, 1, 10, 10], [100, 100, 2, 11, 11], [200, 200, 3, 12, 12]] as [number, number, number, number, number][] };
    expect(buildFigureMesh("Ori", line)).toBeNull(); // collinear in the picture: no unique map
  });

  it("puts Orion's anchors where the sky has them: the picture's club is at the Gemini end, the feet at Rigel", () => {
    // Saiph (HIP 27366) is an anchor at picture pixel (329, 477); the lowest point of the picture is below it.
    const fit = fitFigure(FIGURE_INFO.Ori)!;
    const bottom = vecToRadec(pixelToVec(fit, 329, 512))[1];
    const saiph = catalogStar.get(27366)![1];
    expect(bottom).toBeLessThan(saiph); // picture y grows downward = south
    const left = vecToRadec(pixelToVec(fit, 0, 256))[0]; // west side of the picture or east, decided by handedness
    expect(Math.abs(left - 83)).toBeLessThan(30);
  });
});

describe("rendering helpers", () => {
  it("builds an affine transform that maps the source triangle onto the destination exactly", () => {
    const s: [[number, number], [number, number], [number, number]] = [[0, 0], [100, 0], [0, 100]];
    const d: [[number, number], [number, number], [number, number]] = [[10, 20], [60, 35], [-5, 90]];
    const [a, b, c, dd, e, f] = triangleTransform(s, d)!;
    s.forEach(([x, y], i) => {
      expect(a * x + c * y + e).toBeCloseTo(d[i][0], 9);
      expect(b * x + dd * y + f).toBeCloseTo(d[i][1], 9);
    });
    expect(triangleTransform([[0, 0], [1, 1], [2, 2]], d)).toBeNull(); // degenerate triangle
  });

  it("brightens the figure the view is centred on, and none when zoomed far out or looking at empty sky", () => {
    const figs = ["Ori", "UMa", "Leo"].map((id) => buildFigureMesh(id)!);
    const centres = figs.map((f) => f.centre);
    expect(focusedFigure(figs, centres, figs[1].centre, 60)).toBe(1);
    expect(focusedFigure(figs, centres, figs[2].centre, 60)).toBe(2);
    expect(focusedFigure(figs, centres, figs[1].centre, 150)).toBe(-1); // whole-sky view: nothing singled out
    expect(focusedFigure(figs, centres, radecToVec(300, -60), 60)).toBe(-1); // far from all three
  });

  it("hides the art in daylight and shows it fully once the sky is dark", () => {
    expect(figureNightFactor(20)).toBe(0);
    expect(figureNightFactor(-4)).toBe(0);
    expect(figureNightFactor(-9)).toBeCloseTo(0.5, 9);
    expect(figureNightFactor(-18)).toBe(1);
  });
});

describe("clicking a figure", () => {
  const ori = FIGURE_INFO.Ori;
  const fit = fitFigure(ori)!;

  it("maps a sky direction back to the picture pixel it came from", () => {
    for (const [px, py] of [[0, 0], [ori.w / 2, ori.h / 2], [ori.w - 1, 10], [100, ori.h - 5]]) {
      const back = vecToPixel(fit, pixelToVec(fit, px, py))!;
      expect(back[0]).toBeCloseTo(px, 3);
      expect(back[1]).toBeCloseTo(py, 3);
    }
  });

  const figs = Object.keys(FIGURE_INFO).map((id) => buildFigureMesh(id)!);
  const oriMesh = figs.find((f) => f.id === "Ori")!;

  it("finds the figure under a painted point and none where there is only empty sky", () => {
    const paint: LumaAt = () => 255;
    const none: LumaAt = () => 0;
    expect(figureUnder(figs, oriMesh.centre, paint)).toBe("Ori");
    expect(figureUnder(figs, oriMesh.centre, none)).toBeNull();
  });

  it("reads the picture at the right pixel", () => {
    const seen: [number, number][] = [];
    figureUnder(figs, oriMesh.centre, (id, px, py) => (id === "Ori" ? (seen.push([px, py]), 255) : 0));
    expect(seen.length).toBeGreaterThan(0);
    expect(seen[0][0]).toBeGreaterThan(ori.w * 0.3);
    expect(seen[0][0]).toBeLessThan(ori.w * 0.7);
    expect(seen[0][1]).toBeGreaterThan(ori.h * 0.3);
    expect(seen[0][1]).toBeLessThan(ori.h * 0.7);
  });

  it("ignores directions outside every picture", () => {
    // The south celestial pole is far from Orion's picture.
    expect(figureUnder([oriMesh], radecToVec(0, -89), () => 255)).toBeNull();
  });
});
