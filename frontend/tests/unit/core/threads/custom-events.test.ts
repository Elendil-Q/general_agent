import { describe, expect, it } from "@rstest/core";

import {
  handleCustomEvent,
  type CustomEventContext,
} from "@/core/threads/custom-events";

function makeCtx(): CustomEventContext & {
  updateSubtaskCalls: Array<Record<string, unknown>>;
  toastCalls: string[];
} {
  const updateSubtaskCalls: Array<Record<string, unknown>> = [];
  const toastCalls: string[] = [];
  return {
    updateSubtaskCalls,
    toastCalls,
    updateSubtask(task) {
      updateSubtaskCalls.push({ ...task });
    },
    toast(message) {
      toastCalls.push(message);
    },
  };
}

describe("handleCustomEvent", () => {
  it("returns false for non-object events", () => {
    const ctx = makeCtx();
    expect(handleCustomEvent(null, ctx)).toBe(false);
    expect(handleCustomEvent(undefined, ctx)).toBe(false);
    expect(handleCustomEvent("string", ctx)).toBe(false);
    expect(handleCustomEvent(42, ctx)).toBe(false);
  });

  it("returns false for unknown event type", () => {
    const ctx = makeCtx();
    expect(handleCustomEvent({ type: "unknown_event" }, ctx)).toBe(false);
  });

  describe("task_idle", () => {
    it("sets subtask status to idle", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_idle", task_id: "task-1" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-1", status: "idle" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_idle" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });
  });

  describe("task_revived", () => {
    it("sets subtask status back to in_progress", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_revived", task_id: "task-2" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-2", status: "in_progress" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_revived" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });
  });

  describe("task_expired", () => {
    it("sets subtask status to expired", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_expired", task_id: "task-3" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-3", status: "expired" },
      ]);
    });
  });

  describe("budget_warning", () => {
    it("shows a toast with the warning message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "budget_warning", message: "Budget at 80%" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.toastCalls).toEqual(["Budget at 80%"]);
    });

    it("ignores event without message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "budget_warning" }, ctx);
      expect(result).toBe(false);
      expect(ctx.toastCalls).toHaveLength(0);
    });
  });

  describe("budget_exceeded", () => {
    it("marks subtask as failed with budget message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        {
          type: "budget_exceeded",
          task_id: "task-4",
          message: "Budget limit exceeded",
        },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        {
          id: "task-4",
          status: "failed",
          error: "Budget limit exceeded",
        },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "budget_exceeded", message: "Budget limit exceeded" },
        ctx,
      );
      expect(result).toBe(false);
    });

    it("ignores event without message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "budget_exceeded", task_id: "task-4" },
        ctx,
      );
      expect(result).toBe(false);
    });
  });

  describe("task_progress", () => {
    it("updates subtask latestMessage", () => {
      const ctx = makeCtx();
      const msg = { type: "ai" as const, content: "working..." };
      const result = handleCustomEvent(
        { type: "task_progress", task_id: "task-5", message: msg },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-5", latestMessage: msg },
      ]);
    });
  });

  describe("task_running", () => {
    it("updates subtask latestMessage", () => {
      const ctx = makeCtx();
      const msg = { type: "ai" as const, content: "thinking..." };
      const result = handleCustomEvent(
        { type: "task_running", task_id: "task-6", message: msg },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-6", latestMessage: msg },
      ]);
    });
  });

  describe("llm_retry", () => {
    it("toasts the retry message when non-empty", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "llm_retry", message: "Retrying..." },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.toastCalls).toEqual(["Retrying..."]);
    });

    it("ignores empty message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "llm_retry", message: "   " },
        ctx,
      );
      expect(result).toBe(false);
      expect(ctx.toastCalls).toHaveLength(0);
    });
  });
});
