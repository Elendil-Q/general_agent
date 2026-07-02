import { useQuery } from "@tanstack/react-query";

import { loadEffortsConfig } from "./api";
import type { EffortFlags, EffortsConfigResponse } from "./types";

export { loadEffortsConfig };

/**
 * Built-in effort presets, matching the backend's `EffortsConfig` defaults.
 * Used as a fallback when the backend is older (404) or before the fetch
 * resolves, so the UI never renders an empty effort selector.
 */
export const BUILTIN_EFFORTS: Record<
  "flash" | "thinking" | "pro" | "ultra",
  EffortFlags
> = {
  flash: {
    thinking_enabled: false,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "minimal",
  },
  thinking: {
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "low",
  },
  pro: {
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "medium",
  },
  ultra: {
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
  },
};

export const BUILTIN_EFFORT_DEFAULT: "flash" | "thinking" | "pro" | "ultra" =
  "pro";

export const EFFORT_ORDER: ("flash" | "thinking" | "pro" | "ultra")[] = [
  "flash",
  "thinking",
  "pro",
  "ultra",
];

export interface ResolvedEffortFlags {
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort: "minimal" | "low" | "medium" | "high" | undefined;
}

/**
 * Resolve the runtime flags for an effort from the active configuration.
 *
 * Since effort names are a closed set (flash/thinking/pro/ultra), unknown
 * names cannot occur at runtime. The function looks up the effort directly
 * in the keyed record and returns its flags.
 */
export function resolveEffortFlags(
  effort: "flash" | "thinking" | "pro" | "ultra",
  efforts: Record<"flash" | "thinking" | "pro" | "ultra", EffortFlags>,
): ResolvedEffortFlags {
  const flags = efforts[effort];
  return {
    thinking_enabled: flags.thinking_enabled,
    is_plan_mode: flags.is_plan_mode,
    subagent_enabled: flags.subagent_enabled,
    reasoning_effort: flags.reasoning_effort ?? undefined,
  };
}

export function useEffortsConfig() {
  return useQuery({
    queryKey: ["effortsConfig"],
    queryFn: loadEffortsConfig,
    staleTime: Infinity,
  });
}

/**
 * Effective efforts configuration to render against. Falls back to the built-in
 * four when the backend is older (404) or the fetch has not resolved yet.
 */
export function useEffectiveEffortsConfig(): {
  efforts: Record<"flash" | "thinking" | "pro" | "ultra", EffortFlags>;
  data: EffortsConfigResponse | null | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useEffortsConfig();
  if (data) {
    return {
      efforts: {
        flash: data.flash,
        thinking: data.thinking,
        pro: data.pro,
        ultra: data.ultra,
      },
      data,
      isLoading,
    };
  }
  return {
    efforts: BUILTIN_EFFORTS,
    data,
    isLoading,
  };
}
