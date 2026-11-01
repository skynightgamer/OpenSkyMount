// Exposure planning for an alt-az mount: field-rotation limit (tracked) and star-trailing limit (fixed).

export interface Camera {
  sensorW: number; // mm
  sensorH: number; // mm
  pixel: number; // µm
  focal: number; // mm
}

export const CAMERA_PRESETS: Record<string, { label: string; cam: Camera }> = {
  phone: { label: "Smartphone main camera (1/1.3″, 7 mm)", cam: { sensorW: 9.8, sensorH: 7.4, pixel: 1.2, focal: 6.9 } },
  compact: { label: "Compact, 1″ sensor, 10 mm", cam: { sensorW: 13.2, sensorH: 8.8, pixel: 2.4, focal: 10 } },
  apsc50: { label: "APS-C + 50 mm lens", cam: { sensorW: 23.5, sensorH: 15.6, pixel: 3.9, focal: 50 } },
  ff200: { label: "Full frame + 200 mm telephoto", cam: { sensorW: 36, sensorH: 24, pixel: 5.9, focal: 200 } },
  scope480: { label: "APS-C on 80 mm f/6 refractor (480 mm)", cam: { sensorW: 23.5, sensorH: 15.6, pixel: 3.9, focal: 480 } },
};

export const PAYLOAD_PRESET: Record<string, string> = {
  smartphone: "phone",
  compact: "compact",
  dslr: "apsc50",
  telephoto: "ff200",
  telescope: "scope480",
};

const SIDEREAL_ARCSEC_PER_S = 15.041;

/** Image scale in arcseconds per pixel. */
export function pixelScale(cam: Camera): number {
  return (206.265 * cam.pixel) / cam.focal;
}

/** Field of view (deg) across a sensor dimension. */
export function fieldOfView(sizeMm: number, focal: number): number {
  return (2 * Math.atan(sizeMm / 2 / focal) * 180) / Math.PI;
}

/**
 * Longest exposure (s) before field rotation smears stars in the frame corner by `blurPx`.
 * The field turns about the image centre, so the corner (half the diagonal) moves fastest.
 */
export function maxExposureFieldRotation(rateDegPerS: number, cam: Camera, blurPx: number): number {
  const rate = Math.abs((rateDegPerS * Math.PI) / 180);
  if (rate < 1e-12) return Infinity;
  const cornerPx = Math.hypot(cam.sensorW, cam.sensorH) / 2 / (cam.pixel / 1000);
  return blurPx / cornerPx / rate;
}

/** Longest exposure (s) on a fixed (untracked) tripod before stars trail by `blurPx`. */
export function maxExposureUntracked(decDeg: number, cam: Camera, blurPx: number): number {
  const drift = SIDEREAL_ARCSEC_PER_S * Math.cos((decDeg * Math.PI) / 180);
  if (drift < 1e-9) return Infinity;
  return (blurPx * pixelScale(cam)) / drift;
}

export function formatSeconds(s: number): string {
  if (!Number.isFinite(s)) return "no limit";
  if (s < 1) return `${s.toFixed(2)} s`;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60).toString().padStart(2, "0")} s`;
  return `${(s / 3600).toFixed(1)} h`;
}
