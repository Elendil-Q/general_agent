import { describe, expect, test } from "@rstest/core";

import {
  buildMultiChoiceAnswer,
  isClarificationInterrupt,
} from "@/core/threads/clarification";

describe("isClarificationInterrupt", () => {
  test("matches a clarification_request payload", () => {
    const payload = {
      type: "clarification_request",
      question: "Which env?",
      interaction: "single_choice",
      options: ["dev", "prod"],
      tool_call_id: "call-1",
    };
    expect(isClarificationInterrupt(payload)).toBe(true);
  });

  test("rejects non-clarification interrupt values", () => {
    expect(isClarificationInterrupt(null)).toBe(false);
    expect(isClarificationInterrupt(undefined)).toBe(false);
    expect(isClarificationInterrupt({ type: "something_else" })).toBe(false);
    expect(isClarificationInterrupt({})).toBe(false);
    expect(isClarificationInterrupt("clarification_request")).toBe(false);
  });

  test("accepts payloads without optional fields", () => {
    expect(
      isClarificationInterrupt({
        type: "clarification_request",
        question: "describe the target",
        interaction: "text",
        tool_call_id: "call-2",
      }),
    ).toBe(true);
  });
});

describe("buildMultiChoiceAnswer", () => {
  test("serializes checked options and extra supplement as JSON", () => {
    const answer = buildMultiChoiceAnswer(["py", "ts"], "  also rust  ");
    const parsed = JSON.parse(answer);
    expect(parsed.checked).toEqual(["py", "ts"]);
    expect(parsed.extra).toBe("also rust");
  });

  test("trims extra and allows empty selections", () => {
    const answer = buildMultiChoiceAnswer([], "  ");
    const parsed = JSON.parse(answer);
    expect(parsed.checked).toEqual([]);
    expect(parsed.extra).toBe("");
  });

  test("deduplicates nothing — preserves caller order", () => {
    const answer = buildMultiChoiceAnswer(["a", "b", "a"], "");
    const parsed = JSON.parse(answer);
    expect(parsed.checked).toEqual(["a", "b", "a"]);
  });
});
