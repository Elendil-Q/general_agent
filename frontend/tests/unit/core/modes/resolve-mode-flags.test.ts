import { expect, test } from "@rstest/core";

import {
  BUILTIN_MODE_DEFAULT,
  BUILTIN_MODE_PRESETS,
  resolveModeFlags,
} from "@/core/modes/hooks";
import type { ModePreset } from "@/core/modes/types";

const preset = (over: Partial<ModePreset>): ModePreset => ({
  name: "x",
  thinking_enabled: true,
  is_plan_mode: false,
  subagent_enabled: false,
  reasoning_effort: null,
  icon: "sparkles",
  label: null,
  description: null,
  ...over,
});

test("builtin presets match the legacy hard-coded mapping", () => {
  const byName = Object.fromEntries(
    BUILTIN_MODE_PRESETS.map((p) => [p.name, p]),
  );
  expect(byName.flash).toMatchObject({
    thinking_enabled: false,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "minimal",
  });
  expect(byName.thinking).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: "low",
  });
  expect(byName.pro).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "medium",
  });
  expect(byName.ultra).toMatchObject({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
  });
  expect(BUILTIN_MODE_DEFAULT).toBe("pro");
});

test("resolves flags from the matching preset", () => {
  const flags = resolveModeFlags("ultra", BUILTIN_MODE_PRESETS, "pro");
  expect(flags).toEqual({
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "high",
  });
});

test("falls back to defaultMode when mode is unknown", () => {
  const flags = resolveModeFlags("does-not-exist", BUILTIN_MODE_PRESETS, "pro");
  // Unknown mode -> defaultMode preset ("pro")
  expect(flags.is_plan_mode).toBe(true);
  expect(flags.subagent_enabled).toBe(false);
});

test("falls back to first preset when both mode and default are unknown", () => {
  const presets = [preset({ name: "only", subagent_enabled: true })];
  const flags = resolveModeFlags("nope", presets, "also-nope");
  expect(flags.subagent_enabled).toBe(true);
});

test("returns thinking-enabled defaults when presets is empty", () => {
  const flags = resolveModeFlags("anything", [], "anything");
  expect(flags).toEqual({
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: false,
    reasoning_effort: undefined,
  });
});

test("maps null reasoning_effort to undefined", () => {
  const presets = [preset({ name: "x", reasoning_effort: null })];
  const flags = resolveModeFlags("x", presets, "x");
  expect(flags.reasoning_effort).toBeUndefined();
});

test("custom presets drive the flags (task-only mode)", () => {
  const presets = [
    preset({
      name: "task-only",
      thinking_enabled: true,
      is_plan_mode: false,
      subagent_enabled: true,
      reasoning_effort: "medium",
    }),
  ];
  const flags = resolveModeFlags("task-only", presets, "task-only");
  expect(flags).toEqual({
    thinking_enabled: true,
    is_plan_mode: false,
    subagent_enabled: true,
    reasoning_effort: "medium",
  });
});
