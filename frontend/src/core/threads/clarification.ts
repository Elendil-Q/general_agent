/**
 * Pure helpers and types for clarification interrupts (kept out of the React
 * component so they are unit-testable without a DOM render environment).
 */

export type ClarificationInteraction =
  | "single_choice"
  | "multi_choice"
  | "text"
  | "free";

export interface ClarificationInterruptRequest {
  type: "clarification_request";
  question: string;
  interaction: ClarificationInteraction;
  options?: string[];
  tool_call_id: string;
}

/**
 * Type guard: does the SDK interrupt value carry a clarification request?
 * The backend serializes ClarificationInterruptRequest as the interrupt value.
 */
export function isClarificationInterrupt(
  value: unknown,
): value is ClarificationInterruptRequest {
  return (
    value != null &&
    typeof value === "object" &&
    (value as { type?: unknown }).type === "clarification_request"
  );
}

/**
 * Build the resume answer string for a multi-choice submission.
 * Mirrors the backend's expected JSON shape: {"checked":[...],"extra":"..."}.
 */
export function buildMultiChoiceAnswer(
  checked: Iterable<string>,
  extra: string,
): string {
  return JSON.stringify({
    checked: Array.from(checked),
    extra: extra.trim(),
  });
}
