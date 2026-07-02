"use client";

import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";

import { Tooltip } from "./tooltip";

/**
 * The four built-in efforts are a closed set.
 */
export type AgentEffort = "flash" | "thinking" | "pro" | "ultra";

/**
 * i18n keys for the four built-in efforts. Typed narrowly so the indexed
 * value is always a string key (Translations["inputBox"] also contains
 * non-string members which would widen the type).
 */
type EffortLabelKey =
  | "flashEffort"
  | "thinkingEffort"
  | "proEffort"
  | "ultraEffort";
type EffortDescriptionKey =
  | "flashEffortDescription"
  | "thinkingEffortDescription"
  | "proEffortDescription"
  | "ultraEffortDescription";

const EFFORT_I18N_LABEL: Record<AgentEffort, EffortLabelKey> = {
  flash: "flashEffort",
  thinking: "thinkingEffort",
  pro: "proEffort",
  ultra: "ultraEffort",
};
const EFFORT_I18N_DESCRIPTION: Record<AgentEffort, EffortDescriptionKey> = {
  flash: "flashEffortDescription",
  thinking: "thinkingEffortDescription",
  pro: "proEffortDescription",
  ultra: "ultraEffortDescription",
};

function resolveLabel(effort: AgentEffort, t: Translations): string {
  const key = EFFORT_I18N_LABEL[effort];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return effort;
}

function resolveDescription(effort: AgentEffort, t: Translations): string {
  const key = EFFORT_I18N_DESCRIPTION[effort];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return "";
}

export function EffortHoverGuide({
  effort,
  children,
  showTitle = true,
}: {
  effort: AgentEffort;
  children: React.ReactNode;
  /** When true, tooltip shows "EffortName: Description". When false, only description. */
  showTitle?: boolean;
}) {
  const { t } = useI18n();
  const label = resolveLabel(effort, t);
  const description = resolveDescription(effort, t);
  const content = showTitle ? `${label}: ${description}` : description;

  return <Tooltip content={content}>{children}</Tooltip>;
}
