import { useQuery } from "@tanstack/react-query";

import { loadModesConfig } from "./api";
import type { ModePreset, ModesConfigResponse } from "./types";

export { loadModesConfig };

/**
 * Built-in presets, matching the backend's `ModesConfig` defaults.
 * Used as a fallback when the backend is older (404) or before the fetch
 * resolves, so the UI never renders an empty mode selector.
 */
export const BUILTIN_MODE_PRESETS: ModePreset[] = [
  {
    name: "flash",
    thinking_enabled: false,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "minimal",
    icon: "zap",
    label: null,
    description: null,
  },
  {
    name: "thinking",
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "low",
    icon: "lightbulb",
    label: null,
    description: null,
  },
  {
    name: "pro",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "medium",
    icon: "graduation-cap",
    label: null,
    description: null,
  },
  {
    name: "ultra",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
    icon: "rocket",
    label: null,
    description: null,
  },
];

export const BUILTIN_MODE_DEFAULT = "pro";

export interface ResolvedModeFlags {
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort: "minimal" | "low" | "medium" | "high" | undefined;
}

/**
 * Resolve the runtime flags for a mode from the active presets.
 *
 * If `mode` is not found in `presets` (e.g. a persisted localStorage value
 * from a removed custom mode), falls back to `defaultMode`'s preset, then to
 * the first preset. This preserves the old behavior where an unknown mode
 * never crashed the run — it just produced the default flag bundle.
 */
export function resolveModeFlags(
  mode: string | undefined,
  presets: ModePreset[],
  defaultMode: string,
): ResolvedModeFlags {
  const preset =
    presets.find((p) => p.name === mode) ??
    presets.find((p) => p.name === defaultMode) ??
    presets[0];
  if (!preset) {
    return {
      thinking_enabled: true,
      is_plan_mode: false,
      subagent_enabled: false,
      reasoning_effort: undefined,
    };
  }
  return {
    thinking_enabled: preset.thinking_enabled,
    is_plan_mode: preset.is_plan_mode,
    subagent_enabled: preset.subagent_enabled,
    reasoning_effort: preset.reasoning_effort ?? undefined,
  };
}

export function useModesConfig() {
  return useQuery({
    queryKey: ["modesConfig"],
    queryFn: loadModesConfig,
    staleTime: Infinity,
  });
}

/**
 * Effective presets + default to render against. Falls back to the built-in
 * four when the backend is older (404) or the fetch has not resolved yet.
 */
export function useEffectiveModesConfig(): {
  presets: ModePreset[];
  defaultMode: string;
  data: ModesConfigResponse | null | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useModesConfig();
  if (data) {
    return {
      presets: data.presets,
      defaultMode: data.default,
      data,
      isLoading,
    };
  }
  return {
    presets: BUILTIN_MODE_PRESETS,
    defaultMode: BUILTIN_MODE_DEFAULT,
    data,
    isLoading,
  };
}
