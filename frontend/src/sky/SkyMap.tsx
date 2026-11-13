// Interactive planetarium view (canvas 2D, stereographic projection of the local sky).
import { useEffect, useRef, useState } from "react";
import { getState, select, simNow, useStore } from "../store";
import {
  advanceMatrix,
  altazToVec,
  basis,
  bvColor,
  DEG,
  mulMtV,
  mulMV,
  project,
  radecToVec,
  unproject,
  vecToAltaz,
  vecToRadec,
  type Basis,
  type Mat3,
  type Vec3,
  type View,
} from "./projection";
import { drawSkyBackground, glowSprite, hexRgb, limbAngle, moonLitPath, SPRITE_CORE, type SkyLight } from "./skyrender";
import {
  compassPoint,
  eclipticVec,
  flightAt,
  landscapeAlt,
  makeFlight,
  reticleAt,
  RETICLE,
  ridgeAlt,
  sceneryColors,
  separation,
  shortestDelta,
  slerp,
  wrap360,
  type Flight,
  type ViewPose,
} from "./scenery";
import { buildFigureMesh, FIGURE_INFO, figureNightFactor, figureUnder, type FigureMesh } from "./figures";
import { drawFigureLayer, figureImageRevision, figureLayerSettling, figureLuma } from "./figurerender";
import { ChangeTracker, DRIFT_PX, quantize, shouldPresent, skyDriftPx, type Motion } from "./framegate";
import { wheelZoomFactor } from "./zoom";
import { frameOutline, type MosaicPreview } from "../planning";
import { Icon } from "../components/Icon";

/** Users who ask for less motion get instant camera moves and no pulsing markers. */
const REDUCED_MOTION = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
/** How long (ms) a new selection's reticle animates (settle, ripple, name tag); every display frame is drawn meanwhile. */
const RETICLE_BUSY_MS = RETICLE.rippleDelayMs + RETICLE.rippleMs + 100;

interface Catalog {
  stars: [number, number, number, number, number, string][];
  constellations: { id: string; name: string; english: string; label_ra: number; label_dec: number; lines: [number, number][][] }[];
  dsos: { id: string; name: string; type: string; mag: number | null; ra: number; dec: number }[];
}

interface Body {
  id: string;
  name: string;
  kind: string;
  ra_j2000: number;
  dec_j2000: number;
  alt: number;
  az: number;
  magnitude: number | null;
}

interface Frame {
  time: number;
  matrix: Mat3;
  bodies: Body[];
  sun_alt: number;
  sky_state: string;
  moon: { illuminated: number };
}

interface Prepared {
  starVec: Float64Array;
  stars: Catalog["stars"];
  lines: { con: string; vecs: Vec3[][]; label: Vec3; name: string; english: string }[];
  dsos: (Catalog["dsos"][number] & { v: Vec3 })[];
  /** Constellation artwork pinned to the sky (see figures.ts). */
  figures: FigureMesh[];
}

interface StarTier {
  stars: Catalog["stars"];
  starVec: Float64Array;
}

function prepareStars(stars: Catalog["stars"]): StarTier {
  const starVec = new Float64Array(stars.length * 3);
  stars.forEach((s, i) => starVec.set(radecToVec(s[1], s[2]), i * 3));
  return { stars, starVec };
}

// Stars fainter than magnitude 6.5 (~32k) are fetched only once the user zooms in.
const FAINT_FOV = 35;
let faintTier: StarTier | null = null;
let faintRequested = false;
function requestFaint(): void {
  if (faintRequested) return;
  faintRequested = true;
  fetch("/api/catalog?tier=faint")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`catalog: HTTP ${r.status}`))))
    .then((c: { stars: Catalog["stars"] }) => (faintTier = prepareStars(c.stars)))
    .catch(() => (faintRequested = false));
}

function findStar(cat: Prepared | null, hip: number): Catalog["stars"][number] | undefined {
  return cat?.stars.find((x) => x[0] === hip) ?? faintTier?.stars.find((x) => x[0] === hip);
}

let catalogPromise: Promise<Prepared> | null = null;
export function loadCatalog(): Promise<Prepared> {
  if (!catalogPromise) {
    catalogPromise = fetch("/api/catalog")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`catalog: HTTP ${r.status}`))))
      .then((c: Catalog) => {
        const starVec = new Float64Array(c.stars.length * 3);
        c.stars.forEach((s, i) => starVec.set(radecToVec(s[1], s[2]), i * 3));
        const figures: FigureMesh[] = [];
        for (const id of Object.keys(FIGURE_INFO)) {
          const f = buildFigureMesh(id);
          if (f) figures.push(f);
        }
        return {
          figures,
          starVec,
          stars: c.stars,
          lines: c.constellations.map((k) => ({
            con: k.id,
            name: k.name,
            english: k.english,
            label: radecToVec(k.label_ra, k.label_dec),
            vecs: k.lines.map((seg) => seg.map((p) => radecToVec(p[0], p[1]))),
          })),
          dsos: c.dsos.map((d) => ({ ...d, v: radecToVec(d.ra, d.dec) })),
        };
      })
      .catch((e) => {
        catalogPromise = null; // do not cache a failure: the next mount retries
        throw e;
      });
  }
  return catalogPromise;
}

export interface Layers {
  constellations: boolean;
  figures: boolean;
  labels: boolean;
  altaz: boolean;
  equatorial: boolean;
  dsos: boolean;
  milkyway: boolean;
  ecliptic: boolean;
  landscape: boolean;
}

// User-supplied objects (orbital elements): drawn smaller, coloured by kind.
const KIND_COLORS: Record<string, string> = { satellite: "#9fdcff", comet: "#a8f0c0", asteroid: "#d8c8a8" };

const BODY_COLORS: Record<string, string> = {
  Sun: "#ffd25e",
  Moon: "#e9ecf5",
  Mercury: "#c9b9a6",
  Venus: "#fff4d0",
  Mars: "#ff8a5c",
  Jupiter: "#f1d7b0",
  Saturn: "#e8d18f",
  Uranus: "#a8e6f0",
  Neptune: "#8fb0ff",
};

interface Hit {
  id: string;
  x: number;
  y: number;
}

export default function SkyMap({ layers, followMount }: { layers: Layers; followMount: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<View>({ az: 180, alt: 35, fov: 100, width: 300, height: 300 });
  const frameRef = useRef<{ f: Frame; fetchedAt: number } | null>(null);
  const catRef = useRef<Prepared | null>(null);
  const hitsRef = useRef<Hit[]>([]);
  const layersRef = useRef(layers);
  const followRef = useRef(followMount);
  const flightRef = useRef<Flight | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const observer = useStore((s) => s.config?.config.observer);
  const orbitsRev = useStore((s) => s.orbitsRevision);
  const centerReq = useStore((s) => s.centerReq);
  const selectedId = useStore((s) => s.selectedId);
  layersRef.current = layers;
  followRef.current = followMount;

  /** Smooth camera move to a pose (missing fields keep their value). */
  const flyTo = (to: Partial<ViewPose>) => {
    const v = viewRef.current;
    const from = { az: v.az, alt: v.alt, fov: v.fov };
    const target = { az: wrap360(to.az ?? v.az), alt: Math.max(-30, Math.min(90, to.alt ?? v.alt)), fov: clampFov(to.fov ?? v.fov) };
    flightRef.current = makeFlight(from, target, performance.now(), REDUCED_MOTION);
  };

  // Centre the view on an object picked in search, "Best tonight" or the centre button.
  useEffect(() => {
    if (!centerReq) return;
    let cancelled = false;
    fetch(`/api/object?id=${encodeURIComponent(centerReq.id)}&t=${simNow()}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((o: { alt?: number; az?: number } | null) => {
        if (cancelled || !o || typeof o.alt !== "number" || typeof o.az !== "number") return;
        flyTo({ az: o.az, alt: Math.max(-20, Math.min(85, o.alt)), fov: Math.min(viewRef.current.fov, 60) });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [centerReq?.n]);

  // Catalog (once) and sky frames (periodically and on time jumps / site changes).
  useEffect(() => {
    loadCatalog()
      .then((c) => (catRef.current = c))
      .catch(() => {});
  }, []);
  useEffect(() => {
    let stop = false;
    let inflight = false;
    const refresh = async () => {
      if (inflight) return;
      inflight = true;
      try {
        const t = simNow();
        const r = await fetch(`/api/sky?t=${t}`);
        if (r.ok) frameRef.current = { f: await r.json(), fetchedAt: t };
      } catch {
        /* retry on next tick */
      } finally {
        inflight = false;
      }
    };
    refresh();
    const id = window.setInterval(() => {
      if (stop) return;
      const fr = frameRef.current;
      const rate = getState().clock?.rate ?? 1;
      // Earth rotation is extrapolated client-side; refresh planets / precise matrix regularly, and often while a
      // satellite (about 1°/s) is above the horizon.
      const fast = !!fr?.f.bodies.some((b) => b.kind === "satellite");
      if (!fr || Math.abs(simNow() - fr.fetchedAt) > (fast ? Math.max(1, rate) : Math.max(20, rate * 2))) refresh();
    }, 500);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [observer?.latitude, observer?.longitude, observer?.elevation_m, orbitsRev]);

  // Render loop. Redrawing the whole sky 60 times a second while nothing moves is what kept a CPU core busy on an
  // idle mount, so the loop only draws what changed: the sky (stars, figures, grids, ground) goes into an off-screen
  // canvas that is redrawn when the camera, the layers, the data or Earth's rotation (a fraction of a pixel) changed;
  // the overlay (mount, selection, status pill) is composited on top when it changes or animates. A fully idle map
  // presents about once a second.
  useEffect(() => {
    let raf = 0;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const skyCanvas = document.createElement("canvas");
    skyCanvas.className = "sky-canvas"; // not in the page; the class lets tests that hook the sky drawing see this layer
    const sky = skyCanvas.getContext("2d")!;
    const skyInputs = new ChangeTracker();
    const overlayInputs = new ChangeTracker();
    let skyNow = NaN; // simulation time the cached sky was drawn for
    let layersObj: Layers | null = null;
    let layersKey = "";
    let lastFrame = performance.now();
    let lastPresent = -Infinity;
    let presents = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const wrap = wrapRef.current;
      if (!wrap) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1); // 3x phones: 2.25x the pixels for no visible gain
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      if (w < 1 || h < 1) return; // hidden (another tab on a phone): nothing to draw
      const view = viewRef.current;
      view.width = w;
      view.height = h;
      const st = getState();
      const tMs = performance.now();
      const dt = Math.min(0.25, (tMs - lastFrame) / 1000);
      lastFrame = tMs;
      const fl = flightRef.current;
      if (fl) {
        const p = flightAt(fl, tMs);
        view.az = p.az;
        view.alt = p.alt;
        view.fov = p.fov;
        if (p.done) flightRef.current = null;
      } else if (followRef.current && st.mount) {
        // Ease toward the mount instead of snapping (also smooths the 10 Hz telemetry steps).
        const k = REDUCED_MOTION ? 1 : 1 - Math.exp(-dt / 0.15);
        view.az = wrap360(view.az + shortestDelta(view.az, st.mount.position.az) * k);
        view.alt += (Math.max(-20, Math.min(90, st.mount.position.alt)) - view.alt) * k;
      }

      // Does the cached sky still match what should be on screen?
      const fr = frameRef.current;
      const cat = catRef.current;
      const now = simNow();
      const b = basis(view);
      const quarterPx = b.scale * DEG * 4; // a quarter pixel per degree at the view centre, to ignore sub-pixel creep
      if (layersRef.current !== layersObj) {
        layersObj = layersRef.current;
        layersKey = JSON.stringify(layersObj);
      }
      const skyChanged = skyInputs.changed([
        w,
        h,
        dpr,
        quantize(shortestDelta(0, view.az), quarterPx), // signed, so creeping across north is not a jump
        quantize(view.alt, quarterPx),
        quantize(Math.log(view.fov), 1000),
        layersKey,
        fr,
        cat,
        faintTier,
        figureImageRevision(),
        st.selectedId,
      ]);
      const redrawSky = skyChanged || figureLayerSettling() || !(skyDriftPx(now - skyNow, b.scale) < DRIFT_PX);
      if (redrawSky) {
        const pw = Math.round(w * dpr);
        const ph = Math.round(h * dpr);
        if (canvas.width !== pw || canvas.height !== ph) {
          canvas.width = pw;
          canvas.height = ph;
          canvas.style.width = `${w}px`;
          canvas.style.height = `${h}px`;
        }
        if (skyCanvas.width !== pw || skyCanvas.height !== ph) {
          skyCanvas.width = pw;
          skyCanvas.height = ph;
        }
        sky.setTransform(dpr, 0, 0, dpr, 0, 0);
        renderSky(sky, view, fr, cat, layersRef.current, hitsRef, tMs, now);
        skyNow = now;
      }

      // Does the overlay need a new frame?
      const mount = st.mount;
      // Positions in half pixels, azimuth around 0 kept signed: encoder noise at north (0.002° and 359.998°) must not
      // look like a jump across the sky.
      const halfPx = quarterPx / 2;
      const overlayChanged = overlayInputs.changed([
        mount ? quantize(mount.position.alt, halfPx) : null,
        mount ? quantize(shortestDelta(0, mount.position.az), halfPx) : null,
        mount?.mode,
        mount?.target ? quantize(mount.target.alt, halfPx) : null,
        mount?.target ? quantize(shortestDelta(0, mount.target.az), halfPx) : null,
        st.selectedId,
        st.slewPath,
        st.mosaicPreview,
      ]);
      const reticleBusy = !!fr && !!st.selectedId && (selAnim.id !== st.selectedId || tMs - selAnim.t0 < RETICLE_BUSY_MS);
      // Slewing pulses and runs dashes, a tracked target's dashes turn slowly, everything else is still.
      const motion: Motion = fl
        ? "full"
        : REDUCED_MOTION
          ? "none"
          : reticleBusy || mount?.mode === "slewing"
            ? "full"
            : mount?.target
              ? "ambient"
              : "none";
      if (!redrawSky && !shouldPresent(overlayChanged, motion, tMs - lastPresent)) return;
      lastPresent = tMs;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      ctx.drawImage(skyCanvas, 0, 0);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      renderOverlay(ctx, view, fr, cat, tMs, now);
      canvas.dataset.draws = String(++presents); // lets the browser tests count presented frames
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, []);

  // Pointer interaction: drag to pan, wheel / pinch to zoom, click to select.
  useEffect(() => {
    const canvas = canvasRef.current!;
    const pointers = new Map<number, { x: number; y: number }>();
    let moved = 0;
    let pinchDist = 0;
    const down = (e: PointerEvent) => {
      flightRef.current = null; // the user takes over the camera
      canvas.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      moved = 0;
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
      }
    };
    const move = (e: PointerEvent) => {
      const p = pointers.get(e.pointerId);
      if (!p) {
        const hit = nearestHit(hitsRef.current, e.offsetX, e.offsetY, 10);
        setHover(hit ? hit.id : null);
        canvas.style.cursor = hit ? "pointer" : "grab";
        return;
      }
      const v = viewRef.current;
      if (pointers.size === 2) {
        pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinchDist > 0) v.fov = clampFov(v.fov * (pinchDist / d));
        pinchDist = d;
        moved += 10;
        return;
      }
      const dx = e.offsetX - p.x;
      const dy = e.offsetY - p.y;
      moved += Math.abs(dx) + Math.abs(dy);
      const b = basis(v);
      const degPerPx = 2 * Math.atan(1 / b.scale / 2) * (180 / Math.PI) * 2 * 0.5;
      v.az = (((v.az - dx * degPerPx) % 360) + 360) % 360;
      v.alt = Math.max(-30, Math.min(90, v.alt + dy * degPerPx));
      pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
    };
    const up = (e: PointerEvent) => {
      pointers.delete(e.pointerId);
      if (moved < 5 && e.type === "pointerup") click(e.offsetX, e.offsetY);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      flightRef.current = null;
      // Browsers report wheel distances in pixels, lines or pages. Limit a single event so a
      // coarse wheel cannot jump straight to an endpoint, while trackpads remain continuous.
      viewRef.current.fov = clampFov(viewRef.current.fov * wheelZoomFactor(e.deltaY, e.deltaMode, canvas.clientHeight));
    };
    const click = (x: number, y: number) => {
      const hit = nearestHit(hitsRef.current, x, y, 14);
      if (hit) {
        select(hit.id);
        return;
      }
      // Empty sky: the painted figure under the cursor selects its constellation, otherwise the J2000 position.
      const fr = frameRef.current;
      if (!fr) return;
      const v = unproject(basis(viewRef.current), x, y);
      const [alt] = vecToAltaz(v);
      const m = advanceMatrix(fr.f.matrix, simNow() - fr.f.time);
      const j2000 = mulMtV(m, v);
      const cat = catRef.current;
      if (cat && layersRef.current.figures && figureNightFactor(fr.f.sun_alt) > 0.3) {
        const fig = figureUnder(cat.figures, j2000, figureLuma);
        if (fig && cat.lines.some((c) => c.con === fig)) return select(`con:${fig}`);
      }
      const [ra, dec] = vecToRadec(j2000);
      select(alt > -90 ? `radec:${ra.toFixed(4)},${dec.toFixed(4)}` : null);
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", wheel, { passive: false });
    // Double-click: centre on that point and zoom in.
    const dbl = (e: MouseEvent) => {
      const v = unproject(basis(viewRef.current), e.offsetX, e.offsetY);
      const [alt, az] = vecToAltaz(v);
      flyTo({ az, alt, fov: viewRef.current.fov * 0.55 });
    };
    canvas.addEventListener("dblclick", dbl);
    return () => {
      canvas.removeEventListener("dblclick", dbl);
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("wheel", wheel);
    };
  }, []);

  const zoom = (f: number) => flyTo({ fov: (flightRef.current?.to.fov ?? viewRef.current.fov) * f });
  const face = (az: number) => flyTo({ az, alt: 25 });

  return (
    <div className="sky-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="sky-canvas" aria-label="Sky map. Drag to pan, scroll or pinch to zoom, click an object to select it, double-click to centre." />
      <div className="sky-tools">
        <div className="toolgroup">
          <button onClick={() => zoom(0.7)} aria-label="Zoom in" title="Zoom in">
            <Icon name="plus" />
          </button>
          <button onClick={() => zoom(1.4)} aria-label="Zoom out" title="Zoom out">
            <Icon name="minus" />
          </button>
        </div>
        <div className="toolgroup">
          {(["N", "E", "S", "W"] as const).map((d, i) => (
            <button key={d} onClick={() => face(i * 90)} aria-label={`Face ${d}`} title={`Face ${d}`}>
              {d}
            </button>
          ))}
        </div>
        <div className="toolgroup">
          <button onClick={() => selectedId && select(selectedId, true)} disabled={!selectedId} aria-label="Centre on selection" title="Centre on the selected object">
            <Icon name="target" />
          </button>
        </div>
      </div>
      {hover && <div className="sky-hover">{hoverLabel(hover, catRef.current, frameRef.current?.f)}</div>}
    </div>
  );
}

function clampFov(f: number): number {
  return Math.max(2, Math.min(170, f));
}

function nearestHit(hits: Hit[], x: number, y: number, maxPx: number): Hit | null {
  let best: Hit | null = null;
  let bd = maxPx * maxPx;
  for (const h of hits) {
    const d = (h.x - x) ** 2 + (h.y - y) ** 2;
    if (d < bd) {
      bd = d;
      best = h;
    }
  }
  return best;
}

function hoverLabel(id: string, cat: Prepared | null, f?: Frame): string {
  if (id.startsWith("hip:") && cat) {
    const hip = Number(id.slice(4));
    const s = findStar(cat, hip);
    return s ? `${s[5] || "HIP " + hip}  (mag ${s[3].toFixed(1)})` : id;
  }
  if (id.startsWith("dso:") && cat) {
    const d = cat.dsos.find((x) => "dso:" + x.id === id);
    return d ? `${d.id} ${d.name}` : id;
  }
  if (id.startsWith("con:") && cat) {
    const c = cat.lines.find((x) => x.con === id.slice(4));
    return c ? constellationTitle(c) : id;
  }
  const b = f?.bodies.find((x) => x.id === id);
  return b ? b.name : id;
}

/** "Canis Major · Greater Dog": the Latin name with its English translation when they differ. */
function constellationTitle(c: { name: string; english: string }): string {
  return c.english && c.english.toLowerCase() !== c.name.toLowerCase() ? `${c.name} · ${c.english}` : c.name;
}

// ------------------------------------------------------------------ drawing
const GROUND_COLORS = { day: "#25302a", twilight: "#12161a", night: "#0b0c0e" };
function groundColor(sunAlt: number): string {
  return sunAlt > 0 ? GROUND_COLORS.day : sunAlt > -12 ? GROUND_COLORS.twilight : GROUND_COLORS.night;
}

function polyline(ctx: CanvasRenderingContext2D, b: Basis, pts: Vec3[], maxJump: number) {
  let started = false;
  let last: [number, number, number] | null = null;
  ctx.beginPath();
  for (const v of pts) {
    const p = project(b, v, -0.3);
    if (!p || (last && Math.hypot(p[0] - last[0], p[1] - last[1]) > maxJump)) {
      started = false;
      last = p;
      if (p) {
        ctx.moveTo(p[0], p[1]);
        started = true;
      }
      continue;
    }
    if (!started) {
      ctx.moveTo(p[0], p[1]);
      started = true;
    } else ctx.lineTo(p[0], p[1]);
    last = p;
  }
  ctx.stroke();
}

/** Constellation stick figure: each segment stops `gap` px short of its stars, like a printed chart. */
function stickFigure(ctx: CanvasRenderingContext2D, b: Basis, pts: Vec3[], maxJump: number, gap: number) {
  let last: [number, number, number] | null = null;
  for (const v of pts) {
    const p = project(b, v, -0.3);
    if (p && last) {
      const dx = p[0] - last[0];
      const dy = p[1] - last[1];
      const d = Math.hypot(dx, dy);
      if (d > 2.5 * gap && d < maxJump) {
        const ux = dx / d;
        const uy = dy / d;
        ctx.moveTo(last[0] + ux * gap, last[1] + uy * gap);
        ctx.lineTo(p[0] - ux * gap, p[1] - uy * gap);
      }
    }
    last = p;
  }
}

/** Extra magnitudes of atmospheric extinction at altitude `alt` (deg), k = 0.25 mag per airmass. */
function extinction(alt: number): number {
  if (alt <= 0) return 0; // below the horizon: drawn dimmed instead
  const airmass = 1 / (Math.sin(alt * DEG) + 0.50572 * Math.pow(alt + 6.07995, -1.6364)); // Kasten & Young 1989
  return 0.25 * (airmass - 1);
}

/** Chart symbol for a deep-sky object type (common atlas conventions). */
function dsoSymbol(ctx: CanvasRenderingContext2D, type: string, x: number, y: number) {
  const t = type.toLowerCase();
  ctx.beginPath();
  if (t.includes("galaxy")) {
    ctx.strokeStyle = "rgba(255,150,140,0.8)";
    ctx.ellipse(x, y, 5.5, 2.6, -0.5, 0, Math.PI * 2);
    ctx.stroke();
  } else if (t.includes("globular")) {
    ctx.strokeStyle = "rgba(255,214,140,0.85)";
    ctx.arc(x, y, 4.5, 0, Math.PI * 2);
    ctx.moveTo(x - 4.5, y);
    ctx.lineTo(x + 4.5, y);
    ctx.moveTo(x, y - 4.5);
    ctx.lineTo(x, y + 4.5);
    ctx.stroke();
  } else if (t.includes("open") || t.includes("asterism")) {
    ctx.strokeStyle = t.includes("open") ? "rgba(255,214,140,0.6)" : "rgba(200,200,200,0.5)";
    ctx.setLineDash([2, 2]);
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  } else if (t.includes("planetary")) {
    ctx.strokeStyle = "rgba(140,230,190,0.85)";
    ctx.arc(x, y, 3.2, 0, Math.PI * 2);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      ctx.moveTo(x + dx * 3.2, y + dy * 3.2);
      ctx.lineTo(x + dx * 6, y + dy * 6);
    }
    ctx.stroke();
  } else {
    // emission / reflection nebulae, supernova remnants
    ctx.strokeStyle = "rgba(140,230,190,0.85)";
    ctx.roundRect(x - 5, y - 4, 10, 8, 2.5);
    ctx.stroke();
  }
}

/**
 * The sky itself: background, grids, figures, stars, planets, ground and compass. It only changes with the camera, the
 * layers, new data or Earth's rotation, so the render loop caches it and redraws it when one of those changes.
 * `now` is the simulation time the sky is drawn for.
 */
function renderSky(
  ctx: CanvasRenderingContext2D,
  view: View,
  fr: { f: Frame; fetchedAt: number } | null,
  cat: Prepared | null,
  layers: Layers,
  hitsRef: { current: Hit[] },
  tMs: number,
  now: number,
) {
  const { width: w, height: h } = view;
  const b = basis(view);
  const st = getState();
  const sunAlt = fr?.f.sun_alt ?? -30;
  const m = fr ? advanceMatrix(fr.f.matrix, now - fr.f.time) : null;
  const hits: Hit[] = [];
  const selId = st.selectedId; // its name tag replaces the plain label
  const maxJump = Math.max(w, h) * 0.5;

  // Sky background with twilight, moonlight and the Milky Way.
  const sunB = fr?.f.bodies.find((x) => x.kind === "sun");
  const moonB = fr?.f.bodies.find((x) => x.kind === "moon");
  const light: SkyLight = {
    sun: m && sunB ? mulMV(m, radecToVec(sunB.ra_j2000, sunB.dec_j2000)) : [0, 0, -1],
    sunAlt,
    moon: m && moonB ? mulMV(m, radecToVec(moonB.ra_j2000, moonB.dec_j2000)) : null,
    moonAlt: moonB?.alt ?? -90,
    moonIllum: fr?.f.moon.illuminated ?? 0,
  };
  drawSkyBackground(ctx, b, w, h, m, light, layers.milkyway, Math.floor(now / 4));

  if (fr && cat && m) {
    const limitMag =
      sunAlt > 0 ? 1.0 : sunAlt > -6 ? 2.5 : view.fov > 120 ? 5.0 : view.fov > 60 ? 5.5 : view.fov > FAINT_FOV ? 6.5 : view.fov > 15 ? 7.3 : 8.0;
    if (view.fov <= FAINT_FOV && sunAlt < -6) requestFaint();

    if (layers.equatorial) {
      ctx.strokeStyle = "rgba(90,160,255,0.2)";
      ctx.lineWidth = 1;
      for (let ra = 0; ra < 360; ra += 30) {
        const pts: Vec3[] = [];
        for (let dec = -90; dec <= 90; dec += 3) pts.push(mulMV(m, radecToVec(ra, dec)));
        polyline(ctx, b, pts, maxJump);
      }
      for (let dec = -75; dec <= 75; dec += 15) {
        const pts: Vec3[] = [];
        for (let ra = 0; ra <= 360; ra += 3) pts.push(mulMV(m, radecToVec(ra, dec)));
        ctx.strokeStyle = dec === 0 ? "rgba(90,160,255,0.42)" : "rgba(90,160,255,0.2)";
        polyline(ctx, b, pts, maxJump);
      }
    }

    if (layers.ecliptic) {
      // The Sun's yearly path; the Moon and planets stay close to it.
      const pts: Vec3[] = [];
      for (let lon = 0; lon <= 360; lon += 2) pts.push(mulMV(m, eclipticVec(lon)));
      ctx.strokeStyle = "rgba(255,200,110,0.5)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([7, 6]);
      polyline(ctx, b, pts, maxJump);
      ctx.setLineDash([]);
      if (layers.labels) {
        for (let lon = 15; lon < 360; lon += 30) {
          const p = project(b, mulMV(m, eclipticVec(lon)), 0);
          if (p && p[0] > 60 && p[0] < w - 80 && p[1] > 50 && p[1] < h - 50) {
            ctx.fillStyle = "rgba(255,206,130,0.7)";
            ctx.font = "italic 11px system-ui, sans-serif";
            ctx.textAlign = "left";
            ctx.fillText("Ecliptic", p[0] + 4, p[1] - 5);
            break;
          }
        }
      }
    }

    if (layers.figures && cat.figures.length) {
      // Painted constellation art, behind the stick figures and stars.
      drawFigureLayer({ ctx, b, m, figs: cat.figures, w, h, dpr: ctx.getTransform().a, fov: view.fov, sunAlt, maxJump, tMs, reducedMotion: REDUCED_MOTION, highlight: selId?.startsWith("con:") ? selId.slice(4) : null });
    }

    if (layers.constellations) {
      ctx.strokeStyle = "rgba(125,165,225,0.34)";
      ctx.lineWidth = 1;
      ctx.lineCap = "round";
      const gap = view.fov < 40 ? 6 : 4;
      ctx.beginPath();
      for (const c of cat.lines) for (const seg of c.vecs) stickFigure(ctx, b, seg.map((v) => mulMV(m, v)), maxJump, gap);
      ctx.stroke();
      if (layers.labels && view.fov < 150) {
        ctx.fillStyle = "rgba(150,172,210,0.5)";
        ctx.font = "italic 11px system-ui, sans-serif";
        ctx.textAlign = "center";
        const sub = view.fov < 80; // zoomed in enough to have room for the English name under the Latin one
        for (const c of cat.lines) {
          const p = project(b, mulMV(m, c.label), 0);
          if (!p) continue;
          ctx.font = "italic 11px system-ui, sans-serif";
          ctx.fillText(c.name, p[0], p[1]);
          if (sub && c.english && c.english.toLowerCase() !== c.name.toLowerCase()) {
            ctx.font = "10px system-ui, sans-serif";
            ctx.fillText(c.english, p[0], p[1] + 12);
          }
        }
      }
    }

    if (layers.dsos && view.fov < 120) {
      ctx.lineWidth = 1.1;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "left";
      for (const d of cat.dsos) {
        if ((d.mag ?? 9) > limitMag + 3) continue;
        const p = project(b, mulMV(m, d.v), 0);
        if (!p || p[0] < -10 || p[0] > w + 10 || p[1] < -10 || p[1] > h + 10) continue;
        dsoSymbol(ctx, d.type, p[0], p[1]);
        if (layers.labels && view.fov < 70 && selId !== "dso:" + d.id) {
          ctx.fillStyle = "rgba(200,215,205,0.7)";
          ctx.fillText(d.id, p[0] + 7, p[1] + 3);
        }
        hits.push({ id: "dso:" + d.id, x: p[0], y: p[1] });
      }
    }

    // Stars (bright tier, then the faint tier when zoomed in and loaded). Additive glow sprites for the brighter
    // stars, plain dots for the faint ones; dimmed by atmospheric extinction near the horizon.
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    const zoomBoost = Math.max(0, Math.log2(100 / view.fov)) * 0.35;
    const tiers: StarTier[] = [{ stars: cat.stars, starVec: cat.starVec }];
    if (limitMag > 6.5 && faintTier) tiers.push(faintTier);
    const labels: [string, number, number][] = [];
    const day = sunAlt > -4;
    ctx.globalCompositeOperation = day ? "source-over" : "lighter";
    for (const tier of tiers) {
      const sv = tier.starVec;
      for (let i = 0; i < tier.stars.length; i++) {
        const s = tier.stars[i];
        if (s[3] > limitMag) break; // sorted by magnitude
        const x = sv[i * 3];
        const y = sv[i * 3 + 1];
        const z = sv[i * 3 + 2];
        const v: Vec3 = [
          m[0][0] * x + m[0][1] * y + m[0][2] * z,
          m[1][0] * x + m[1][1] * y + m[1][2] * z,
          m[2][0] * x + m[2][1] * y + m[2][2] * z,
        ];
        const p = project(b, v, 0);
        if (!p || p[0] < -20 || p[0] > w + 20 || p[1] < -20 || p[1] > h + 20) continue;
        const alt = Math.asin(Math.max(-1, Math.min(1, v[2]))) / DEG;
        const mag = s[3] + extinction(alt);
        if (mag > limitMag + 0.3 && s[3] > 2) continue;
        const r = Math.max(0.55, (6.9 - mag) * 0.38 + zoomBoost);
        const below = v[2] < 0 ? 0.35 : 1;
        const faint = mag > 6.5 ? 0.55 + (0.3 * (8 - mag)) / 1.5 : mag > 5 ? 0.75 + 0.25 * (6.5 - mag) / 1.5 : 1;
        const color = bvColor(s[4]);
        ctx.globalAlpha = Math.max(0.15, below * faint);
        if (r > 1.25 && !day) {
          const hs = r / SPRITE_CORE;
          ctx.drawImage(glowSprite(color), p[0] - hs, p[1] - hs, 2 * hs, 2 * hs);
        } else {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
          ctx.fill();
        }
        if (layers.labels && s[5] && selId !== "hip:" + s[0] && (s[3] < 1.6 || (view.fov < 60 && s[3] < 3.5) || (view.fov < 20 && s[3] < 6.5))) {
          labels.push([s[5], p[0] + r + 3, p[1] + 3]);
        }
        if (s[3] < 5.5 || view.fov < 40) hits.push({ id: "hip:" + s[0], x: p[0], y: p[1] });
      }
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = "rgba(220,230,255,0.78)";
    for (const [t, x, y] of labels) ctx.fillText(t, x, y);

    // Sun, Moon, planets.
    for (const body of fr.f.bodies) {
      const v = mulMV(m, radecToVec(body.ra_j2000, body.dec_j2000));
      const p = project(b, v, 0);
      if (!p) continue;
      const big = body.kind === "sun" || body.kind === "moon";
      const user = body.kind in KIND_COLORS;
      const r = big ? Math.max(5, (0.26 * b.scale * Math.PI) / 180 * 2) : user ? 2.5 : 3.5;
      const color = BODY_COLORS[body.name] ?? KIND_COLORS[body.kind] ?? "#ffffff";
      ctx.globalAlpha = v[2] < 0 ? 0.45 : 1;
      if (!user) {
        // glow
        const gr = body.kind === "sun" ? r * 9 : body.kind === "moon" ? r * (2 + 3 * fr.f.moon.illuminated) : r * 3.2;
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha *= body.kind === "moon" ? 0.5 : 0.8;
        ctx.drawImage(glowSprite(color), p[0] - gr, p[1] - gr, 2 * gr, 2 * gr);
        ctx.globalCompositeOperation = "source-over";
        ctx.globalAlpha = v[2] < 0 ? 0.45 : 1;
      }
      if (body.kind === "moon" && light.sun) {
        // Earthshine disc, then the sunlit part with the bright limb toward the Sun.
        ctx.fillStyle = "#2a2e38";
        ctx.beginPath();
        ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
        ctx.fill();
        const ang = limbAngle((q) => project(b, q, -1), v, light.sun);
        ctx.save();
        ctx.translate(p[0], p[1]);
        ctx.rotate(ang ?? 0);
        const [cr, cg, cb] = hexRgb(color);
        const grad = ctx.createRadialGradient(-r * 0.25, -r * 0.2, 0, 0, 0, r);
        grad.addColorStop(0, `rgb(${cr},${cg},${cb})`);
        grad.addColorStop(1, `rgb(${Math.round(cr * 0.8)},${Math.round(cg * 0.8)},${Math.round(cb * 0.82)})`);
        ctx.fillStyle = grad;
        moonLitPath(ctx, r, fr.f.moon.illuminated);
        ctx.fill();
        ctx.restore();
      } else {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      if (layers.labels && body.id !== selId) {
        ctx.fillStyle = "#ffe6b0";
        ctx.font = "12px system-ui, sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(body.name, p[0] + r + 4, p[1] - 4);
      }
      hits.push({ id: body.id, x: p[0], y: p[1] });
    }
  }

  // Ground (alt < 0) and the optional decorative skyline: translucent so objects behind stay visible.
  drawTerrain(ctx, b, w, h, sunAlt, layers.landscape, maxJump, view.fov);

  if (layers.altaz) {
    ctx.strokeStyle = "rgba(255,200,120,0.18)";
    ctx.lineWidth = 1;
    for (let alt = 15; alt < 90; alt += 15) {
      const pts: Vec3[] = [];
      for (let az = 0; az <= 360; az += 3) pts.push(altazToVec(alt, az));
      polyline(ctx, b, pts, maxJump);
    }
    for (let az = 0; az < 360; az += 30) {
      const pts: Vec3[] = [];
      for (let alt = 0; alt <= 90; alt += 3) pts.push(altazToVec(alt, az));
      polyline(ctx, b, pts, maxJump);
    }
  }
  // True horizon (altitude 0) and compass points. Fainter when the decorative skyline is drawn over it.
  ctx.strokeStyle = layers.landscape ? "rgba(255,190,110,0.45)" : "rgba(255,190,110,0.8)";
  ctx.lineWidth = layers.landscape ? 1 : 1.5;
  const hz: Vec3[] = [];
  for (let az = 0; az <= 360; az += 2) hz.push(altazToVec(0, az));
  polyline(ctx, b, hz, maxJump);
  ctx.fillStyle = "#f0a23a";
  ctx.font = "bold 13px system-ui, sans-serif";
  ctx.textAlign = "center";
  ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].forEach((label, i) => {
    const p = project(b, altazToVec(-2.5, i * 45), 0);
    if (p) ctx.fillText(label, p[0], p[1] + 10);
  });
  hitsRef.current = hits;
}

/**
 * What sits on top of the cached sky: the mount trail, slew line, selection reticle, target and mount markers and the
 * status pill. These move with the mount and animate, so they are drawn on every presented frame.
 */
function renderOverlay(ctx: CanvasRenderingContext2D, view: View, fr: { f: Frame; fetchedAt: number } | null, cat: Prepared | null, tMs: number, now: number) {
  const { width: w, height: h } = view;
  const b = basis(view);
  const st = getState();
  const tSec = tMs / 1000;
  const maxJump = Math.max(w, h) * 0.5;
  ctx.lineCap = "round";

  // Mount trail, slew line, selection, target and mount markers.
  const mount = st.mount;
  if (mount) {
    recordTrail(mount.position.alt, mount.position.az, tSec);
    drawTrail(ctx, b, tSec, maxJump);
  }
  const mountVec = mount ? altazToVec(mount.position.alt, mount.position.az) : null;
  const targetVec = mount?.target ? altazToVec(mount.target.alt, mount.target.az) : null;
  if (mount && mountVec && targetVec && mount.mode === "slewing") {
    // Line of sight from the mount to its target (an indicator, not the axis path, which moves alt and az separately).
    ctx.strokeStyle = "rgba(255,179,71,0.6)";
    ctx.lineWidth = 1.4;
    ctx.setLineDash([2, 6]);
    ctx.lineDashOffset = REDUCED_MOTION ? 0 : -((tMs / 30) % 8);
    polyline(ctx, b, greatCircle(mountVec, targetVec), maxJump);
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
  }
  if (fr && st.mosaicPreview) drawMosaicPreview(ctx, b, st.mosaicPreview, advanceMatrix(fr.f.matrix, now - fr.f.time), maxJump);
  const preview = st.slewPath;
  if (preview && preview.id === st.selectedId && mount?.mode !== "slewing") drawSlewPath(ctx, b, preview.path, maxJump);
  if (fr && st.selectedId) {
    if (selAnim.id !== st.selectedId) {
      // Glide over from the previous selection if its reticle was on screen a moment ago; otherwise settle in.
      const prev = lastReticle && tMs - lastReticle.t < 250 ? lastReticle : null;
      selAnim = { id: st.selectedId, t0: tMs, from: prev?.v ?? null, fromR: prev?.r ?? null };
    }
    const sel = markerVec(st.selectedId, fr, cat, now);
    if (sel) {
      const R = reticleRadius(st.selectedId, cat, b);
      lastReticle = drawSelection(ctx, b, sel, selAnim, R, selectionLabel(st.selectedId, cat, fr.f), tMs, w);
    }
  } else selAnim = { id: null, t0: 0, from: null, fromR: null };
  if (mount && targetVec) drawTargetMark(ctx, b, targetVec, tMs, mount.mode);
  if (mount && mountVec) {
    // A wide canvas can show directions past 90° from the view centre. Clip at the canvas edges instead.
    const p = project(b, mountVec, -1);
    const margin = 50; // room for the crosshair, pulse and label
    if (p && p[0] >= -margin && p[0] <= w + margin && p[1] >= -margin && p[1] <= h + margin) {
      const moving = ["slewing", "tracking", "manual", "homing"].includes(mount.mode);
      if (moving) {
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = 0.35;
        ctx.drawImage(glowSprite("#ff5b7a"), p[0] - 22, p[1] - 22, 44, 44);
        ctx.globalCompositeOperation = "source-over";
        ctx.globalAlpha = 1;
      }
      if (mount.mode === "slewing" && !REDUCED_MOTION) {
        const ph = (tMs / 1100) % 1;
        ctx.strokeStyle = `rgba(255,91,122,${0.55 * (1 - ph)})`;
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(p[0], p[1], 8 + 18 * ph, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.strokeStyle = "#ff5b7a";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p[0], p[1], 8, 0, Math.PI * 2);
      ctx.moveTo(p[0] - 16, p[1]);
      ctx.lineTo(p[0] - 4, p[1]);
      ctx.moveTo(p[0] + 4, p[1]);
      ctx.lineTo(p[0] + 16, p[1]);
      ctx.moveTo(p[0], p[1] - 16);
      ctx.lineTo(p[0], p[1] - 4);
      ctx.moveTo(p[0], p[1] + 4);
      ctx.lineTo(p[0], p[1] + 16);
      ctx.stroke();
      ctx.fillStyle = "#ff8fa3";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("mount", p[0] + 12, p[1] + 20);
    }
  }

  // Status pill: light, field of view and where the view is looking.
  const state = fr?.f.sky_state ?? "loading…";
  const hud = `${state.charAt(0).toUpperCase()}${state.slice(1)}  ·  FOV ${view.fov.toFixed(0)}°  ·  facing ${compassPoint(view.az)} ${view.az.toFixed(0)}°, alt ${view.alt.toFixed(0)}°`;
  pill(ctx, hud, 10, h - 10, "rgba(205,214,228,0.82)", "rgba(8,12,20,0.62)", "rgba(255,255,255,0.08)");
}

// ------------------------------------------------------------------ markers
let selAnim: { id: string | null; t0: number; from: Vec3 | null; fromR: number | null } = { id: null, t0: 0, from: null, fromR: null };
/** Where the reticle was last drawn (direction, radius, time), so the next selection can glide from there. */
let lastReticle: { v: Vec3; r: number; t: number } | null = null;

/** Rounded label with a background, anchored at its bottom-left corner. */
function pill(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, fg: string, bg: string, border: string) {
  ctx.font = "11.5px system-ui, sans-serif";
  ctx.textAlign = "left";
  const tw = ctx.measureText(text).width;
  ctx.fillStyle = bg;
  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x, y - 21, tw + 18, 21, 10.5);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = fg;
  ctx.fillText(text, x + 9, y - 6.5);
}

/** Reticle size for an object: bigger for bright stars and the Sun/Moon discs. */
function reticleRadius(id: string, cat: Prepared | null, b: Basis): number {
  if (id === "sun" || id === "moon") return Math.max(14, (0.26 * b.scale * Math.PI) / 180 * 2 + 7);
  if (id.startsWith("hip:")) {
    const s = findStar(cat, Number(id.slice(4)));
    const mag = s ? s[3] : 6;
    return 9.5 + Math.max(0, Math.min(3.5, 2.5 - mag)) * 1.6;
  }
  if (id.startsWith("planet:") || id.startsWith("dso:")) return 12.5;
  if (id.startsWith("con:")) return 17;
  return 10;
}

/**
 * Selection reticle: a ring of four arcs that settles onto the object with one soft ripple, or glides over from the
 * previous selection; the name tag fades in above it. Returns where it was drawn.
 */
function drawSelection(
  ctx: CanvasRenderingContext2D,
  b: Basis,
  target: Vec3,
  anim: { t0: number; from: Vec3 | null; fromR: number | null },
  R: number,
  label: string | null,
  tMs: number,
  w: number,
): { v: Vec3; r: number; t: number } | null {
  const pose = reticleAt(tMs - anim.t0, R, anim.from ? anim.fromR : null, REDUCED_MOTION);
  const v = anim.from && pose.glide < 1 ? slerp(anim.from, target, pose.glide) : target;
  const p = project(b, v, 0);
  if (!p) return null;
  const [x, y] = p;
  const r = pose.radius;
  ctx.save();
  ctx.lineCap = "round";
  if (pose.ripple > 0) {
    ctx.strokeStyle = `rgba(170,210,255,${pose.rippleAlpha.toFixed(3)})`;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(x, y, pose.ripple, 0, Math.PI * 2);
    ctx.stroke();
  }
  // Four arcs with gaps on the diagonals; a dark under-stroke keeps them readable on a bright (day) sky.
  const gap = 0.32;
  const arcs = () => {
    ctx.beginPath();
    for (let i = 0; i < 4; i++) {
      const c = (i * Math.PI) / 2;
      ctx.moveTo(x + r * Math.cos(c - Math.PI / 4 + gap / 2), y + r * Math.sin(c - Math.PI / 4 + gap / 2));
      ctx.arc(x, y, r, c - Math.PI / 4 + gap / 2, c + Math.PI / 4 - gap / 2);
    }
    ctx.stroke();
  };
  ctx.globalAlpha = pose.alpha;
  ctx.strokeStyle = "rgba(4,8,16,0.35)";
  ctx.lineWidth = 3.4;
  arcs();
  ctx.strokeStyle = "#c4e0ff";
  ctx.lineWidth = 1.5;
  arcs();
  ctx.restore();
  if (label && pose.label > 0.01) {
    ctx.globalAlpha = pose.label;
    ctx.font = "11.5px system-ui, sans-serif";
    // Centred above the ring, clear of the object's own label (drawn to its right); rises into place as it fades in.
    const tw = ctx.measureText(label).width + 18;
    const lx = Math.max(4, Math.min(w - tw - 4, x - tw / 2));
    pill(ctx, label, lx, y - r - 7 + (1 - pose.label) * 4, "#eaf3ff", "rgba(10,18,32,0.8)", "rgba(170,210,255,0.32)");
    ctx.globalAlpha = 1;
  }
  return { v, r, t: tMs };
}

/**
 * The frames of a planned mosaic as the camera would see them now: the top of each picture points to the zenith, as on
 * an alt-az mount, and the edges are straight in the camera's own picture. Unreachable frames are red.
 */
function drawMosaicPreview(ctx: CanvasRenderingContext2D, b: Basis, mosaic: MosaicPreview, m: Mat3, maxJump: number) {
  const zenith = mulMtV(m, [0, 0, 1]); // up, in the J2000 frame
  ctx.save();
  ctx.lineWidth = 1.4;
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "center";
  for (const p of mosaic.panels) {
    const outline = frameOutline(radecToVec(p.ra, p.dec), zenith, mosaic.fov.w, mosaic.fov.h).map((v) => mulMV(m, v));
    ctx.strokeStyle = p.reachable ? "rgba(130,200,255,0.9)" : "rgba(239,109,99,0.85)";
    ctx.fillStyle = p.reachable ? "rgba(130,200,255,0.06)" : "rgba(239,109,99,0.08)";
    const pts = [...outline, outline[0]];
    ctx.beginPath();
    let last: [number, number, number] | null = null;
    let broken = false;
    for (const v of pts) {
      const q = project(b, v, -0.3);
      if (!q) {
        broken = true;
        last = null;
        continue;
      }
      if (last === null || Math.hypot(q[0] - last[0], q[1] - last[1]) > maxJump) {
        if (last !== null) broken = true;
        ctx.moveTo(q[0], q[1]);
      } else ctx.lineTo(q[0], q[1]);
      last = q;
    }
    ctx.stroke();
    if (!broken) ctx.fill();
    const c = project(b, mulMV(m, radecToVec(p.ra, p.dec)), -0.3);
    if (c) {
      ctx.fillStyle = p.reachable ? "rgba(190,225,255,0.95)" : "rgba(255,170,160,0.95)";
      ctx.fillText(String(p.index + 1), c[0], c[1] + 4);
    }
  }
  ctx.restore();
}

/** The previewed route of a GoTo (both axes moving at once), dashed, with an arrowhead where it ends. */
function drawSlewPath(ctx: CanvasRenderingContext2D, b: Basis, path: [number, number][], maxJump: number) {
  if (path.length < 2) return;
  const pts = path.map(([alt, az]) => altazToVec(alt, az));
  ctx.save();
  ctx.strokeStyle = "rgba(150,210,255,0.85)";
  ctx.lineWidth = 1.6;
  ctx.lineCap = "round";
  ctx.setLineDash([2, 6]);
  polyline(ctx, b, pts, maxJump);
  ctx.setLineDash([]);
  const end = project(b, pts[pts.length - 1], -0.3);
  let before: [number, number, number] | null = null;
  for (let i = pts.length - 2; i >= 0 && !before; i--) {
    const q = project(b, pts[i], -0.3);
    if (q && end && Math.hypot(q[0] - end[0], q[1] - end[1]) > 6) before = q;
  }
  if (end && before) {
    const ang = Math.atan2(end[1] - before[1], end[0] - before[0]);
    ctx.beginPath();
    ctx.moveTo(end[0] - 9 * Math.cos(ang - 0.45), end[1] - 9 * Math.sin(ang - 0.45));
    ctx.lineTo(end[0], end[1]);
    ctx.lineTo(end[0] - 9 * Math.cos(ang + 0.45), end[1] - 9 * Math.sin(ang + 0.45));
    ctx.stroke();
  }
  ctx.restore();
}

/** The mount's target: dashes that run while slewing and a steady ring with ticks once tracking. */
function drawTargetMark(ctx: CanvasRenderingContext2D, b: Basis, v: Vec3, tMs: number, mode: string) {
  const p = project(b, v, 0);
  if (!p) return;
  const r = 16;
  ctx.strokeStyle = "#ffb347";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 4]);
  ctx.lineDashOffset = REDUCED_MOTION ? 0 : mode === "slewing" ? -((tMs / 35) % 9) : -((tMs / 600) % 9);
  ctx.beginPath();
  ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.lineDashOffset = 0;
  ctx.beginPath();
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    ctx.moveTo(p[0] + dx * (r + 2), p[1] + dy * (r + 2));
    ctx.lineTo(p[0] + dx * (r + 7), p[1] + dy * (r + 7));
  }
  ctx.stroke();
}

function greatCircle(a: Vec3, c: Vec3, n = 48): Vec3[] {
  const dot = Math.max(-1, Math.min(1, a[0] * c[0] + a[1] * c[1] + a[2] * c[2]));
  const ang = Math.acos(dot);
  if (ang < 1e-6) return [a, c];
  const s = Math.sin(ang);
  const out: Vec3[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const ka = Math.sin((1 - t) * ang) / s;
    const kc = Math.sin(t * ang) / s;
    out.push([ka * a[0] + kc * c[0], ka * a[1] + kc * c[1], ka * a[2] + kc * c[2]]);
  }
  return out;
}

// Recent mount positions (wall-clock seconds), drawn as a fading trail.
const TRAIL_S = 90;
const trail: { alt: number; az: number; t: number }[] = [];
function recordTrail(alt: number, az: number, t: number) {
  const last = trail[trail.length - 1];
  if (last) {
    if (t - last.t < 0.2) return;
    const d = separation(last, { alt, az });
    if (d > 25) trail.length = 0; // a jump (time warp, reconnect): start over
    else if (d < 0.01) return;
  }
  trail.push({ alt, az, t });
  while (trail.length && (t - trail[0].t > TRAIL_S || trail.length > 600)) trail.shift();
}

function drawTrail(ctx: CanvasRenderingContext2D, b: Basis, t: number, maxJump: number) {
  if (trail.length < 2) return;
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  let prev = project(b, altazToVec(trail[0].alt, trail[0].az), 0);
  for (let i = 1; i < trail.length; i++) {
    const q = project(b, altazToVec(trail[i].alt, trail[i].az), 0);
    if (prev && q && Math.hypot(q[0] - prev[0], q[1] - prev[1]) < maxJump) {
      const a = 0.5 * Math.max(0, 1 - (t - trail[i].t) / TRAIL_S);
      ctx.strokeStyle = `rgba(255,91,122,${a.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(prev[0], prev[1]);
      ctx.lineTo(q[0], q[1]);
      ctx.stroke();
    }
    prev = q;
  }
}

function selectionLabel(id: string, cat: Prepared | null, f: Frame): string | null {
  if (id.startsWith("radec:") || id.startsWith("altaz:")) return null;
  const l = hoverLabel(id, cat, f);
  return l.replace(/\s+\(mag [^)]*\)$/, "");
}

function markerVec(id: string, fr: { f: Frame }, cat: Prepared | null, now: number): Vec3 | null {
  const m = advanceMatrix(fr.f.matrix, now - fr.f.time);
  const body = fr.f.bodies.find((x) => x.id === id);
  if (body) return mulMV(m, radecToVec(body.ra_j2000, body.dec_j2000));
  if (id.startsWith("hip:") && cat) {
    const s = findStar(cat, Number(id.slice(4)));
    if (s) return mulMV(m, radecToVec(s[1], s[2]));
  }
  if (id.startsWith("dso:") && cat) {
    const d = cat.dsos.find((x) => "dso:" + x.id === id);
    if (d) return mulMV(m, d.v);
  }
  if (id.startsWith("con:") && cat) {
    const c = cat.lines.find((x) => x.con === id.slice(4));
    if (c) return mulMV(m, c.label);
  }
  if (id.startsWith("radec:")) {
    const [ra, dec] = id.slice(6).split(",").map(Number);
    return mulMV(m, radecToVec(ra, dec));
  }
  if (id.startsWith("altaz:")) {
    const [alt, az] = id.slice(6).split(",").map(Number);
    return altazToVec(alt, az);
  }
  return null;
}

function drawGround(ctx: CanvasRenderingContext2D, b: Basis, w: number, h: number, color: string) {
  // The horizon is a great circle; its stereographic image is a circle (or a line).
  const pts: [number, number][] = [];
  for (let az = 0; az < 360; az += 5) {
    const p = project(b, altazToVec(0, az), -0.95);
    if (p) pts.push([p[0], p[1]]);
  }
  ctx.fillStyle = color;
  const nadir = project(b, [0, 0, -1], -2);
  if (pts.length < 3) {
    if (b.f[2] < 0) ctx.fillRect(0, 0, w, h);
    return;
  }
  const circ = circleFrom(pts[0], pts[Math.floor(pts.length / 3)], pts[Math.floor((2 * pts.length) / 3)]);
  if (!circ || circ.r > 1e6) {
    // Nearly a straight line: fill the half-plane on the nadir side.
    const below: [number, number] = nadir ? [nadir[0], nadir[1]] : [w / 2, h * 10];
    const a = pts[0];
    const c = pts[pts.length - 1];
    const dir = [c[0] - a[0], c[1] - a[1]];
    const nrm = [-dir[1], dir[0]];
    const side = Math.sign((below[0] - a[0]) * nrm[0] + (below[1] - a[1]) * nrm[1]) || 1;
    const L = 1e5;
    const ux = dir[0] / Math.hypot(dir[0], dir[1]);
    const uy = dir[1] / Math.hypot(dir[0], dir[1]);
    const nx = (nrm[0] / Math.hypot(nrm[0], nrm[1])) * side;
    const ny = (nrm[1] / Math.hypot(nrm[0], nrm[1])) * side;
    ctx.beginPath();
    ctx.moveTo(a[0] - ux * L, a[1] - uy * L);
    ctx.lineTo(a[0] + ux * L, a[1] + uy * L);
    ctx.lineTo(a[0] + ux * L + nx * L, a[1] + uy * L + ny * L);
    ctx.lineTo(a[0] - ux * L + nx * L, a[1] - uy * L + ny * L);
    ctx.fill();
    return;
  }
  const nadirInside = nadir ? Math.hypot(nadir[0] - circ.x, nadir[1] - circ.y) < circ.r : b.f[2] < 0;
  ctx.beginPath();
  if (nadirInside) {
    ctx.arc(circ.x, circ.y, circ.r, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.rect(-10, -10, w + 20, h + 20);
    ctx.arc(circ.x, circ.y, circ.r, 0, Math.PI * 2, true);
    ctx.fill("evenodd");
  }
}

/**
 * Fill the band between altitude `bottom` and a skyline `top(az)` as polygons (one per continuous run of projected
 * points). The step follows the zoom so tree crowns stay resolved without wasting work on wide views.
 */
function skylineFill(ctx: CanvasRenderingContext2D, b: Basis, top: (az: number) => number, bottom: number, maxJump: number, fov: number) {
  const step = Math.max(0.04, Math.min(0.5, fov / 700));
  const n = Math.ceil(360 / step);
  let run: [number, number][] = [];
  let base: [number, number][] = [];
  ctx.beginPath();
  const flush = () => {
    if (run.length > 1) {
      ctx.moveTo(run[0][0], run[0][1]);
      for (let i = 1; i < run.length; i++) ctx.lineTo(run[i][0], run[i][1]);
      for (let i = base.length - 1; i >= 0; i--) ctx.lineTo(base[i][0], base[i][1]);
      ctx.closePath();
    }
    run = [];
    base = [];
  };
  for (let i = 0; i <= n; i++) {
    const az = i * step;
    const pt = project(b, altazToVec(top(az), az), -0.95);
    const pb = project(b, altazToVec(bottom, az), -0.95);
    const last = run[run.length - 1];
    if (!pt || !pb || (last && Math.hypot(pt[0] - last[0], pt[1] - last[1]) > maxJump)) {
      flush();
      if (!pt || !pb) continue;
    }
    run.push([pt[0], pt[1]]);
    base.push([pb[0], pb[1]]);
  }
  flush();
  ctx.fill();
}

let terrainCanvas: HTMLCanvasElement | null = null;

/**
 * Ground below the horizon plus, optionally, a decorative skyline (distant ridge and near hills with trees, at most
 * ~3° high). Ground and near skyline are drawn opaque off-screen and composited once, so they blend without seams.
 */
function drawTerrain(ctx: CanvasRenderingContext2D, b: Basis, w: number, h: number, sunAlt: number, landscape: boolean, maxJump: number, fov: number) {
  if (!landscape) {
    ctx.globalAlpha = 0.82;
    drawGround(ctx, b, w, h, groundColor(sunAlt));
    ctx.globalAlpha = 1;
    return;
  }
  const colors = sceneryColors(sunAlt);
  ctx.globalAlpha = 0.72;
  ctx.fillStyle = colors.far;
  skylineFill(ctx, b, ridgeAlt, 0, maxJump, Math.max(fov, 30));
  ctx.globalAlpha = 1;
  const cw = ctx.canvas.width;
  const ch = ctx.canvas.height;
  if (cw < 1 || ch < 1) return;
  if (!terrainCanvas) terrainCanvas = document.createElement("canvas");
  if (terrainCanvas.width !== cw || terrainCanvas.height !== ch) {
    terrainCanvas.width = cw;
    terrainCanvas.height = ch;
  }
  const g = terrainCanvas.getContext("2d")!;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, cw, ch);
  g.setTransform(ctx.getTransform());
  drawGround(g, b, w, h, colors.near);
  g.fillStyle = colors.near;
  skylineFill(g, b, landscapeAlt, -0.5, maxJump, fov);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 0.86;
  ctx.drawImage(terrainCanvas, 0, 0);
  ctx.restore();
}

function circleFrom(a: [number, number], b: [number, number], c: [number, number]) {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
  if (Math.abs(d) < 1e-9) return null;
  const a2 = a[0] ** 2 + a[1] ** 2;
  const b2 = b[0] ** 2 + b[1] ** 2;
  const c2 = c[0] ** 2 + c[1] ** 2;
  const x = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d;
  const y = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d;
  return { x, y, r: Math.hypot(a[0] - x, a[1] - y) };
}
