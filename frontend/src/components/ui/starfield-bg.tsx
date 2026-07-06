"use client";

import { cn } from "@/lib/utils";

/**
 * Lightweight CSS starfield background — replaces the WebGL-based Galaxy
 * component. Uses layered radial gradients and a subtle CSS twinkle
 * animation, no GPU shaders or requestAnimationFrame.
 */
export function StarfieldBg({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-0 overflow-hidden",
        className,
      )}
      aria-hidden
    >
      <div className="starfield-layer absolute inset-0" />
      <div className="starfield-layer-2 absolute inset-0" />
      <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-black/40 to-black/70" />
    </div>
  );
}
