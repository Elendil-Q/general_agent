"use client";

import { cn } from "@/lib/utils";

/**
 * Lightweight CSS grid background with optional deer mask — replaces
 * FlickeringGrid (Canvas2D + requestAnimationFrame). Pure CSS, zero JS per-frame cost.
 */
export function DeerGridBg({
  className,
  color = "white",
}: {
  className?: string;
  color?: string;
}) {
  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-0 overflow-hidden",
        className,
      )}
      aria-hidden
      style={
        {
          "--grid-color": color,
        } as React.CSSProperties
      }
    >
      <div className="deer-grid-pattern absolute inset-[-4px]" />
    </div>
  );
}
