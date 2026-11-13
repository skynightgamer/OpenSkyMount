/** Convert a wheel delta to a responsive, bounded multiplicative change in field of view. */
export function wheelZoomFactor(deltaY: number, deltaMode: number, pageHeight: number): number {
  const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? pageHeight : 1);
  return Math.exp(Math.max(-240, Math.min(240, pixels)) * 0.003);
}
