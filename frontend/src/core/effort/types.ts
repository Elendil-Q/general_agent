/**
 * Effort flag types — mirror of the backend `EffortsConfigResponse`
 * served by `GET /api/efforts/config`.
 *
 * Effort is a frontend concept: the backend never receives an effort name,
 * only the derived runtime flags (`thinking_enabled`, `is_plan_mode`,
 * `subagent_enabled`, `reasoning_effort`) the frontend computes from the
 * selected effort before sending them in `config.context`.
 */
export interface EffortFlags {
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  /** Whether the effort enables the `task` (subagent delegation) tool. */
  subagent_enabled: boolean;
  /** Default reasoning effort for this effort: "minimal"/"low"/"medium"/"high" (or null to leave unset). */
  reasoning_effort: "minimal" | "low" | "medium" | "high" | null;
}

export interface EffortsConfigResponse {
  flash: EffortFlags;
  thinking: EffortFlags;
  pro: EffortFlags;
  ultra: EffortFlags;
}
