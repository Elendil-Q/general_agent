"use client";

import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import type { ModePreset } from "@/core/modes/types";

import { Tooltip } from "./tooltip";

/**
 * Mode name is config-driven (served by `GET /api/modes/config`), so it is a
 * plain string rather than a closed union. Unknown names fall back to the
 * mode `name` itself for both label and description.
 */
export type AgentMode = string;

/**
 * i18n fallback keys for the four built-in modes. Typed narrowly so the
 * indexed value is always a string (Translations["inputBox"] also contains
 * non-string members like suggestionsCreate, which would widen the type).
 */
type InputBoxStringKey =
  | "flashMode"
  | "reasoningMode"
  | "proMode"
  | "ultraMode";
type InputBoxDescriptionKey =
  | "flashModeDescription"
  | "reasoningModeDescription"
  | "proModeDescription"
  | "ultraModeDescription";

const MODE_I18N_LABEL: Record<string, InputBoxStringKey> = {
  flash: "flashMode",
  thinking: "reasoningMode",
  pro: "proMode",
  ultra: "ultraMode",
};
const MODE_I18N_DESCRIPTION: Record<string, InputBoxDescriptionKey> = {
  flash: "flashModeDescription",
  thinking: "reasoningModeDescription",
  pro: "proModeDescription",
  ultra: "ultraModeDescription",
};

function resolveLabel(
  mode: AgentMode,
  presets: ModePreset[] | undefined,
  t: Translations,
): string {
  const preset = presets?.find((p) => p.name === mode);
  if (preset?.label) {
    return preset.label;
  }
  const key = MODE_I18N_LABEL[mode];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return mode;
}

function resolveDescription(
  mode: AgentMode,
  presets: ModePreset[] | undefined,
  t: Translations,
): string {
  const preset = presets?.find((p) => p.name === mode);
  if (preset?.description) {
    return preset.description;
  }
  const key = MODE_I18N_DESCRIPTION[mode];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return "";
}

export function ModeHoverGuide({
  mode,
  presets,
  children,
  showTitle = true,
}: {
  mode: AgentMode;
  /** Active presets (from `useEffectiveModesConfig`). When omitted, only the
   * i18n fallback for the four built-in modes is used. */
  presets?: ModePreset[];
  children: React.ReactNode;
  /** When true, tooltip shows "ModeName: Description". When false, only description. */
  showTitle?: boolean;
}) {
  const { t } = useI18n();
  const label = resolveLabel(mode, presets, t);
  const description = resolveDescription(mode, presets, t);
  const content = showTitle ? `${label}: ${description}` : description;

  return <Tooltip content={content}>{children}</Tooltip>;
}
