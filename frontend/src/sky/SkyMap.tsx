// Interactive planetarium view (canvas 2D, stereographic projection of the local sky).
import { useEffect, useRef, useState } from "react";
import { getState, select, simNow, useStore } from "../store";
import {
  advanceMatrix,
  altazToVec,
  basis,
  bvColor,
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

interface Catalog {
  stars: [number, number, number, number, number, string][];
  constellations: { id: string; name: string; label_ra: number; label_dec: number; lines: [number, number][][] }[];
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
  lines: { con: string; vecs: Vec3[][]; label: Vec3; name: string }[];
  dsos: (Catalog["dsos"][number] & { v: Vec3 })[];
}

let catalogPromise: Promise<Prepared> | null = null;
export function loadCatalog(): Promise<Prepared> {
  if (!catalogPromise) {
    catalogPromise = fetch("/api/catalog")
      .then((r) => r.json())
      .then((c: Catalog) => {
        const starVec = new Float64Array(c.stars.length * 3);
        c.stars.forEach((s, i) => starVec.set(radecToVec(s[1], s[2]), i * 3));
        return {
          starVec,
          stars: c.stars,
          lines: c.constellations.map((k) => ({
            con: k.id,
            name: k.name,
            label: radecToVec(k.label_ra, k.label_dec),
            vecs: k.lines.map((seg) => seg.map((p) => radecToVec(p[0], p[1]))),
          })),
          dsos: c.dsos.map((d) => ({ ...d, v: radecToVec(d.ra, d.dec) })),
        };
      });
  }
  return catalogPromise;
}

export interface Layers {
  constellations: boolean;
  labels: boolean;
  altaz: boolean;
  equatorial: boolean;
  dsos: boolean;
}

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
  const [hover, setHover] = useState<string | null>(null);
  const observer = useStore((s) => s.config?.config.observer);
  layersRef.current = layers;
  followRef.current = followMount;

  // Catalog (once) and sky frames (periodically and on time jumps / site changes).
  useEffect(() => {
    loadCatalog().then((c) => (catRef.current = c));
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
      // Earth rotation is extrapolated client-side; refresh planets / precise matrix regularly.
      if (!fr || Math.abs(simNow() - fr.fetchedAt) > Math.max(20, rate * 2)) refresh();
    }, 500);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [observer?.latitude, observer?.longitude, observer?.elevation_m]);

  // Render loop.
  useEffect(() => {
    let raf = 0;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const wrap = wrapRef.current;
      if (!wrap) return;
      const dpr = window.devicePixelRatio || 1;
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const view = viewRef.current;
      view.width = w;
      view.height = h;
      const st = getState();
      if (followRef.current && st.mount) {
        view.az = st.mount.position.az;
        view.alt = Math.max(-20, Math.min(90, st.mount.position.alt));
      }
      render(ctx, view, frameRef.current, catRef.current, layersRef.current, hitsRef);
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
      viewRef.current.fov = clampFov(viewRef.current.fov * Math.exp(e.deltaY * 0.001));
    };
    const click = (x: number, y: number) => {
      const hit = nearestHit(hitsRef.current, x, y, 14);
      if (hit) {
        select(hit.id);
        return;
      }
      // Empty sky: select the J2000 position under the cursor.
      const fr = frameRef.current;
      if (!fr) return;
      const v = unproject(basis(viewRef.current), x, y);
      const [alt] = vecToAltaz(v);
      const m = advanceMatrix(fr.f.matrix, simNow() - fr.f.time);
      const [ra, dec] = vecToRadec(mulMtV(m, v));
      select(alt > -90 ? `radec:${ra.toFixed(4)},${dec.toFixed(4)}` : null);
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => {
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("wheel", wheel);
    };
  }, []);

  const zoom = (f: number) => (viewRef.current.fov = clampFov(viewRef.current.fov * f));
  const face = (az: number) => {
    viewRef.current.az = az;
    viewRef.current.alt = 30;
  };

  return (
    <div className="sky-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="sky-canvas" aria-label="Sky map. Drag to pan, scroll or pinch to zoom, click an object to select it." />
      <div className="sky-tools">
        <button onClick={() => zoom(0.7)} aria-label="Zoom in">＋</button>
        <button onClick={() => zoom(1.4)} aria-label="Zoom out">－</button>
        {(["N", "E", "S", "W"] as const).map((d, i) => (
          <button key={d} onClick={() => face(i * 90)} aria-label={`Face ${d}`}>{d}</button>
        ))}
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
    const s = cat.stars.find((x) => x[0] === hip);
    return s ? `${s[5] || "HIP " + hip}  (mag ${s[3].toFixed(1)})` : id;
  }
  if (id.startsWith("dso:") && cat) {
    const d = cat.dsos.find((x) => "dso:" + x.id === id);
    return d ? `${d.id} ${d.name}` : id;
  }
  const b = f?.bodies.find((x) => x.id === id);
  return b ? b.name : id;
}

// ------------------------------------------------------------------ drawing
function skyColors(sunAlt: number): [string, string, string] {
  if (sunAlt > 0) return ["#2a5d9a", "#5b8fc9", "#1f3346"];
  if (sunAlt > -6) return ["#16243f", "#3a4f78", "#141b24"];
  if (sunAlt > -12) return ["#0a1226", "#1a2747", "#0f141b"];
  return ["#03050b", "#0a1020", "#0c1015"];
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

function render(
  ctx: CanvasRenderingContext2D,
  view: View,
  fr: { f: Frame; fetchedAt: number } | null,
  cat: Prepared | null,
  layers: Layers,
  hitsRef: { current: Hit[] },
) {
  const { width: w, height: h } = view;
  const b = basis(view);
  const st = getState();
  const now = simNow();
  const sunAlt = fr?.f.sun_alt ?? -30;
  const [c0, c1, ground] = skyColors(sunAlt);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, c0);
  grad.addColorStop(1, c1);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  const hits: Hit[] = [];
  const maxJump = Math.max(w, h) * 0.5;

  if (fr && cat) {
    const m = advanceMatrix(fr.f.matrix, now - fr.f.time);
    const limitMag = sunAlt > 0 ? 1.0 : sunAlt > -6 ? 2.5 : view.fov > 120 ? 5.0 : view.fov > 60 ? 5.5 : 6.5;

    if (layers.equatorial) {
      ctx.strokeStyle = "rgba(90,160,255,0.22)";
      ctx.lineWidth = 1;
      for (let ra = 0; ra < 360; ra += 30) {
        const pts: Vec3[] = [];
        for (let dec = -90; dec <= 90; dec += 3) pts.push(mulMV(m, radecToVec(ra, dec)));
        polyline(ctx, b, pts, maxJump);
      }
      for (let dec = -75; dec <= 75; dec += 15) {
        const pts: Vec3[] = [];
        for (let ra = 0; ra <= 360; ra += 3) pts.push(mulMV(m, radecToVec(ra, dec)));
        ctx.strokeStyle = dec === 0 ? "rgba(90,160,255,0.45)" : "rgba(90,160,255,0.22)";
        polyline(ctx, b, pts, maxJump);
      }
    }

    if (layers.constellations) {
      ctx.strokeStyle = "rgba(120,170,230,0.35)";
      ctx.lineWidth = 1;
      for (const c of cat.lines) for (const seg of c.vecs) polyline(ctx, b, seg.map((v) => mulMV(m, v)), maxJump);
      if (layers.labels && view.fov < 150) {
        ctx.fillStyle = "rgba(130,170,220,0.55)";
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "center";
        for (const c of cat.lines) {
          const p = project(b, mulMV(m, c.label), 0);
          if (p) ctx.fillText(c.name.toUpperCase(), p[0], p[1]);
        }
      }
    }

    if (layers.dsos && view.fov < 120) {
      ctx.strokeStyle = "rgba(160,220,170,0.75)";
      ctx.fillStyle = "rgba(160,220,170,0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "left";
      for (const d of cat.dsos) {
        if ((d.mag ?? 9) > limitMag + 3) continue;
        const p = project(b, mulMV(m, d.v), 0);
        if (!p || p[0] < -10 || p[0] > w + 10 || p[1] < -10 || p[1] > h + 10) continue;
        ctx.beginPath();
        ctx.ellipse(p[0], p[1], 4, 2.5, 0, 0, Math.PI * 2);
        ctx.stroke();
        if (layers.labels && view.fov < 70) ctx.fillText(d.id, p[0] + 6, p[1] + 3);
        hits.push({ id: "dso:" + d.id, x: p[0], y: p[1] });
      }
    }

    // Stars.
    const sv = cat.starVec;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left";
    const zoomBoost = Math.max(0, Math.log2(100 / view.fov)) * 0.4;
    for (let i = 0; i < cat.stars.length; i++) {
      const s = cat.stars[i];
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
      if (!p || p[0] < -5 || p[0] > w + 5 || p[1] < -5 || p[1] > h + 5) continue;
      const r = Math.max(0.6, (6.8 - s[3]) * 0.42 + zoomBoost);
      ctx.fillStyle = bvColor(s[4]);
      ctx.globalAlpha = v[2] < 0 ? 0.35 : 1;
      ctx.beginPath();
      ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
      ctx.fill();
      if (layers.labels && s[5] && (s[3] < 1.6 || (view.fov < 60 && s[3] < 3.5))) {
        ctx.fillStyle = "rgba(220,230,255,0.75)";
        ctx.fillText(s[5], p[0] + r + 3, p[1] + 3);
      }
      if (s[3] < 5.5 || view.fov < 40) hits.push({ id: "hip:" + s[0], x: p[0], y: p[1] });
    }
    ctx.globalAlpha = 1;

    // Sun, Moon, planets.
    for (const body of fr.f.bodies) {
      const p = project(b, mulMV(m, radecToVec(body.ra_j2000, body.dec_j2000)), 0);
      if (!p) continue;
      const big = body.kind === "sun" || body.kind === "moon";
      const r = big ? Math.max(5, (0.26 * b.scale * Math.PI) / 180 * 2) : 3.5;
      ctx.fillStyle = BODY_COLORS[body.name] ?? "#fff";
      ctx.beginPath();
      ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
      ctx.fill();
      if (body.kind === "moon") {
        // crude phase shading: darken by the unilluminated fraction
        ctx.fillStyle = `rgba(5,7,13,${(1 - fr.f.moon.illuminated) * 0.85})`;
        ctx.beginPath();
        ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
        ctx.fill();
      }
      if (layers.labels) {
        ctx.fillStyle = "#ffe6b0";
        ctx.font = "12px system-ui, sans-serif";
        ctx.fillText(body.name, p[0] + r + 4, p[1] - 4);
      }
      hits.push({ id: body.id, x: p[0], y: p[1] });
    }
  }

  // Ground (alt < 0): translucent so objects below the horizon stay visible.
  drawGround(ctx, b, w, h, ground);

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
  // Horizon line and compass points.
  ctx.strokeStyle = "rgba(255,190,110,0.8)";
  ctx.lineWidth = 1.5;
  const hz: Vec3[] = [];
  for (let az = 0; az <= 360; az += 2) hz.push(altazToVec(0, az));
  polyline(ctx, b, hz, maxJump);
  ctx.fillStyle = "#ffcf8a";
  ctx.font = "bold 13px system-ui, sans-serif";
  ctx.textAlign = "center";
  ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].forEach((label, i) => {
    const p = project(b, altazToVec(-2.5, i * 45), 0);
    if (p) ctx.fillText(label, p[0], p[1] + 10);
  });

  // Selection, target and mount markers.
  const mount = st.mount;
  if (fr && st.selectedId) {
    const sel = markerVec(st.selectedId, fr, cat, now);
    if (sel) drawReticle(ctx, b, sel, "#7cf0ff", 11, false);
  }
  if (mount?.target) drawReticle(ctx, b, altazToVec(mount.target.alt, mount.target.az), "#ffb347", 15, true);
  if (mount) {
    const p = project(b, altazToVec(mount.position.alt, mount.position.az), 0);
    if (p) {
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
  hitsRef.current = hits;

  // Scale / info overlay.
  ctx.fillStyle = "rgba(200,210,230,0.6)";
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(`FOV ${view.fov.toFixed(0)}° · view az ${view.az.toFixed(0)}° alt ${view.alt.toFixed(0)}° · ${fr?.f.sky_state ?? "loading…"}`, 10, h - 10);
}

function markerVec(id: string, fr: { f: Frame }, cat: Prepared | null, now: number): Vec3 | null {
  const m = advanceMatrix(fr.f.matrix, now - fr.f.time);
  const body = fr.f.bodies.find((x) => x.id === id);
  if (body) return mulMV(m, radecToVec(body.ra_j2000, body.dec_j2000));
  if (id.startsWith("hip:") && cat) {
    const hip = Number(id.slice(4));
    const i = cat.stars.findIndex((s) => s[0] === hip);
    if (i >= 0) return mulMV(m, radecToVec(cat.stars[i][1], cat.stars[i][2]));
  }
  if (id.startsWith("dso:") && cat) {
    const d = cat.dsos.find((x) => "dso:" + x.id === id);
    if (d) return mulMV(m, d.v);
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

function drawReticle(ctx: CanvasRenderingContext2D, b: Basis, v: Vec3, color: string, r: number, dashed: boolean) {
  const p = project(b, v, 0);
  if (!p) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash(dashed ? [4, 3] : []);
  ctx.beginPath();
  ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawGround(ctx: CanvasRenderingContext2D, b: Basis, w: number, h: number, color: string) {
  // The horizon is a great circle; its stereographic image is a circle (or a line).
  const pts: [number, number][] = [];
  for (let az = 0; az < 360; az += 5) {
    const p = project(b, altazToVec(0, az), -0.95);
    if (p) pts.push([p[0], p[1]]);
  }
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.82;
  const nadir = project(b, [0, 0, -1], -2);
  if (pts.length < 3) {
    if (b.f[2] < 0) ctx.fillRect(0, 0, w, h);
    ctx.globalAlpha = 1;
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
    ctx.globalAlpha = 1;
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
  ctx.globalAlpha = 1;
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
