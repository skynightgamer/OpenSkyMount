// Mosaic and timelapse planning (GET /api/mosaic, /api/timelapse): response shapes and the pure geometry used to draw
// the frames on the sky map and to pre-fill the form.
import { CAMERA_PRESETS, fieldOfView, PAYLOAD_PRESET } from "./sky/exposure";
import { DEG, type Vec3 } from "./sky/projection";

export interface MosaicPanel {
  index: number;
  row: number;
  col: number;
  ra: number;
  dec: number;
  target: string;
  alt: number;
  az: number;
  reachable: boolean;
  problems: string[];
}

export interface MosaicPlan {
  centre: { id: string; name: string; ra: number; dec: number };
  cols: number;
  rows: number;
  fov: { w: number; h: number };
  panels: MosaicPanel[];
  coverage: { w: number; h: number };
  total_s: number;
  unreachable: number;
  items: { target: string; duration_s: number; label: string }[];
  estimate: string;
}

export interface TimelapsePlan {
  target: { id: string; name: string };
  minutes: number;
  interval_s: number;
  frames: number;
  playback_s: number;
  held_for_s: number | null;
  sets_in_s: number | null;
  alt: { start: number; end: number; min: number; max: number };
  az: { start: number; end: number; swept: number };
  field_rotation_deg: number;
  field_rotation_per_frame_deg: number;
  star_motion_per_frame_arcsec: number;
  cable_limit_in_s: number | null;
  sun_alt: { start: number; end: number; max: number };
  dark: boolean;
  moon: { illuminated: number; separation_deg: number | null };
  estimate: string;
}

/** Frames of the previewed mosaic, drawn on the sky map. */
export interface MosaicPreview {
  fov: { w: number; h: number };
  panels: { index: number; ra: number; dec: number; reachable: boolean }[];
}

/** Field of view (width, height in degrees) of the payload's camera in landscape, from the exposure planner's presets. */
export function payloadFov(payload: string | undefined): { w: number; h: number } {
  const preset = CAMERA_PRESETS[PAYLOAD_PRESET[payload ?? ""] ?? "phone"].cam;
  return { w: fieldOfView(preset.sensorW, preset.focal), h: fieldOfView(preset.sensorH, preset.focal) };
}

const norm = (v: Vec3): Vec3 => {
  const n = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / n, v[1] / n, v[2] / n];
};
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * Outline of a camera frame centred on `centre` (unit vector) with `up` pointing to the top of the picture (any
 * vector; its part along the centre is removed): `perEdge` points along each edge, going round the frame. The edges
 * are straight lines of the camera's own (gnomonic) picture, so wide frames bow outwards on the sky map.
 */
export function frameOutline(centre: Vec3, up: Vec3, widthDeg: number, heightDeg: number, perEdge = 8): Vec3[] {
  const f = norm(centre);
  const k = dot(up, f);
  let u: Vec3 = [up[0] - k * f[0], up[1] - k * f[1], up[2] - k * f[2]];
  if (Math.hypot(...u) < 1e-9) u = Math.abs(f[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  u = norm(cross(cross(f, u), f)); // perpendicular to f, still along `up`
  const r = norm(cross(u, f)); // picture right
  const hw = Math.tan((Math.min(widthDeg, 170) / 2) * DEG);
  const hh = Math.tan((Math.min(heightDeg, 170) / 2) * DEG);
  const corners: [number, number][] = [[-1, 1], [1, 1], [1, -1], [-1, -1]];
  const out: Vec3[] = [];
  for (let c = 0; c < 4; c++) {
    const [ax, ay] = corners[c];
    const [bx, by] = corners[(c + 1) % 4];
    for (let i = 0; i < perEdge; i++) {
      const t = i / perEdge;
      const x = (ax + (bx - ax) * t) * hw;
      const y = (ay + (by - ay) * t) * hh;
      out.push(norm([f[0] + x * r[0] + y * u[0], f[1] + x * r[1] + y * u[1], f[2] + x * r[2] + y * u[2]]));
    }
  }
  return out;
}

/** Signed degrees with a typographic minus: "−64°", "3.1°", "0.011°" (small per-frame values keep their digits). */
export function rotationText(deg: number): string {
  const a = Math.abs(deg);
  const s = a === 0 ? "0" : a < 0.1 ? a.toFixed(3) : a < 10 ? a.toFixed(1) : a.toFixed(0);
  return `${deg < 0 ? "−" : ""}${s}°`;
}
