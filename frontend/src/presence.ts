// Enter/exit animation support for panels that are mounted only while open (settings sheet, account menu).
// The element stays mounted for `exitMs` after `open` turns false, with `closing` set, so CSS can play an exit
// animation (`[data-state="closing"]`). Respects prefers-reduced-motion (no exit delay).
import { useEffect, useState } from "react";

export const REDUCED_MOTION = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export function usePresence(open: boolean, exitMs: number): { mounted: boolean; closing: boolean } {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    if (REDUCED_MOTION) {
      setMounted(false);
      return;
    }
    const t = window.setTimeout(() => setMounted(false), exitMs);
    return () => window.clearTimeout(t);
  }, [open, exitMs]);
  return { mounted: open || mounted, closing: !open && mounted };
}
