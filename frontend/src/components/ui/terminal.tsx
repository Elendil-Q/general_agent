"use client";

import { cn } from "@/lib/utils";
import { useEffect, useState, type ReactNode } from "react";

/**
 * Lightweight static terminal — replaces the motion-based version.
 * Terminal renders a styled terminal window. TypingAnimation and
 * AnimatedSpan show their content statically (no JS per-frame animation),
 * with optional CSS fade-in via the "delay" prop controlling transition delay.
 */

export function Terminal({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "w-full overflow-hidden rounded-xl border border-neutral-700 bg-neutral-950",
        className,
      )}
    >
      <div className="flex items-center gap-1.5 border-b border-neutral-700 bg-neutral-900 px-4 py-2.5">
        <div className="h-3 w-3 rounded-full bg-red-500/80" />
        <div className="h-3 w-3 rounded-full bg-yellow-500/80" />
        <div className="h-3 w-3 rounded-full bg-green-500/80" />
      </div>
      <pre className="flex flex-col gap-0.5 p-4 font-mono text-sm leading-relaxed text-zinc-100">
        {children}
      </pre>
    </div>
  );
}

export function TypingAnimation({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const [visible, setVisible] = useState(delay === 0);

  useEffect(() => {
    if (delay > 0) {
      const timer = setTimeout(() => setVisible(true), delay);
      return () => clearTimeout(timer);
    }
  }, [delay]);

  return (
    <span
      className={cn("terminal-line", className)}
      style={{
        opacity: visible ? 1 : 0,
        transition: "opacity 0.3s ease-in",
      }}
    >
      {children}
    </span>
  );
}

export function AnimatedSpan({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const [visible, setVisible] = useState(delay === 0);

  useEffect(() => {
    if (delay > 0) {
      const timer = setTimeout(() => setVisible(true), delay);
      return () => clearTimeout(timer);
    }
  }, [delay]);

  return (
    <span
      className={cn("terminal-line", className)}
      style={{
        opacity: visible ? 1 : 0,
        transition: "opacity 0.3s ease-in",
      }}
    >
      {children}
    </span>
  );
}
