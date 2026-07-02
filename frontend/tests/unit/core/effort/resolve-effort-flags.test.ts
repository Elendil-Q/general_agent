import { expect, test } from "@rstest/core";

import {
  BUILTIN_EFFORT_DEFAULT,
  BUILTIN_EFFORTS,
  resolveEffortFlags,
} from "@/core/effort/hooks";

test("builtin efforts match the legacy hard-coded mapping", () => {
  expect(BUILTIN_EFFORTS.flash).toMatchObject({
    thinking_enabled: false,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "minimal",
  });
  expect(BUILTIN_EFFORTS.thinking).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "low",
  });
  expect(BUILTIN_EFFORTS.pro).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "medium",
  });
  expect(BUILTIN_EFFORTS.ultra).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
  });
  expect(BUILTIN_EFFORT_DEFAULT).toBe("pro");
});

test("resolves flags from each built-in effort", () => {
  expect(resolveEffortFlags("ultra", BUILTIN_EFFORTS)).toEqual({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
  });
  expect(resolveEffortFlags("flash", BUILTIN_EFFORTS)).toEqual({
    thinking_enabled: false,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "minimal",
  });
});

test("maps null reasoning_effort to undefined", () => {
  const customEfforts = {
    ...BUILTIN_EFFORTS,
    pro: {
      ...BUILTIN_EFFORTS.pro,
      reasoning_effort: null as "minimal" | "low" | "medium" | "high" | null,
    },
  };
  const flags = resolveEffortFlags("pro", customEfforts);
  expect(flags.reasoning_effort).toBeUndefined();
});

test("custom effort flags override work", () => {
  const customEfforts: typeof BUILTIN_EFFORTS = {
    ...BUILTIN_EFFORTS,
    pro: {
      ...BUILTIN_EFFORTS.pro,
      subagent_enabled: true,
      reasoning_effort: "medium",
    },
  };
  const flags = resolveEffortFlags("pro", customEfforts);
  expect(flags.subagent_enabled).toBe(true);
  expect(flags.is_plan_mode).toBe(true);
});
