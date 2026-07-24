"use client";

import * as React from "react";
import { Check, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

function Checkbox({
  className,
  checked = false,
  indeterminate = false,
  onCheckedChange,
  ...props
}: Omit<React.ComponentProps<"button">, "onChange" | "checked"> & {
  checked?: boolean;
  indeterminate?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}) {
  const state = indeterminate
    ? "indeterminate"
    : checked
      ? "checked"
      : "unchecked";

  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      data-slot="checkbox"
      data-state={state}
      className={cn(
        "peer border-input focus-visible:ring-ring/50 size-4 shrink-0 rounded-[4px] border shadow-xs transition-colors outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
        "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
        "data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground",
        className,
      )}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onCheckedChange?.(!checked);
      }}
      {...props}
    >
      {state === "indeterminate" ? (
        <Minus className="pointer-events-none size-3.5" />
      ) : state === "checked" ? (
        <Check className="pointer-events-none size-3.5" />
      ) : null}
    </button>
  );
}

export { Checkbox };
