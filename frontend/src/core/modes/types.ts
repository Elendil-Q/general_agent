/**
 * Mode preset types — mirror of the backend `ModesConfigResponse`
 * served by `GET /api/modes/config`.
 *
 * Modes are a frontend-only concept: the backend never receives a mode name,
 * only the derived runtime flags (`thinking_enabled`, `is_plan_mode`,
 * `subagent_enabled`, `reasoning_effort`) the frontend computes from the
 * selected preset before sending them in `config.context`.
 */
export interface ModePreset {
  /** Unique mode identifier (e.g. "flash", "thinking", "pro", "ultra"). */
  name: string;
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  /** Whether the mode enables the `task` (subagent delegation) tool. */
  subagent_enabled: boolean;
  /** Default reasoning effort for this mode: "minimal"/"low"/"medium"/"high" (or null to leave unset). */
  reasoning_effort: "minimal" | "low" | "medium" | "high" | null;
  /** Icon name for rendering; unknown names fall back to a default icon. */
  icon: string;
  /** Optional display label overriding the i18n fallback. */
  label: string | null;
  /** Optional tooltip description overriding the i18n fallback. */
  description: string | null;
}

export interface ModesConfigResponse {
  presets: ModePreset[];
  /** Default mode name when the user has not selected one. */
  default: string;
}
