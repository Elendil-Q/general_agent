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
        { id: "task-1", status: "idle", live: true },
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
        { id: "task-2", status: "in_progress", live: true },
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
    it("sets subtask status to completed with ttlExpired flag", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_expired", task_id: "task-3" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-3", status: "completed", ttlExpired: true },
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
        { id: "task-5", latestMessage: msg, live: true },
      ]);
    });

    it("carries the full serialized AIMessage with tool_calls", () => {
      const ctx = makeCtx();
      const msg = {
        id: "msg-2",
        type: "ai" as const,
        content: "Second response",
        tool_calls: [
          {
            name: "bash",
            args: { command: "ls" },
            id: "tc-1",
            type: "tool_call" as const,
          },
        ],
      };
      const result = handleCustomEvent(
        { type: "task_progress", task_id: "task-5", message: msg },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-5", latestMessage: msg, live: true },
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
        { id: "task-6", latestMessage: msg, live: true },
      ]);
    });

    it("does not overwrite latestMessage when message is absent", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_running", task_id: "task-6" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([{ id: "task-6", live: true }]);
    });
  });

  describe("task_completed", () => {
    it("sets subtask status to completed with result", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        {
          type: "task_completed",
          task_id: "task-7",
          result: "Task output here",
        },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-7", status: "completed", result: "Task output here" },
      ]);
    });

    it("sets subtask status to completed without result", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_completed", task_id: "task-7" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-7", status: "completed" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_completed" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });
  });

  describe("task_failed", () => {
    it("sets subtask status to failed with error", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_failed", task_id: "task-8", error: "Something broke" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-8", status: "failed", error: "Something broke" },
      ]);
    });

    it("sets subtask status to failed without error", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_failed", task_id: "task-8" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-8", status: "failed" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_failed" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });
  });

  describe("task_cancelled", () => {
    it("sets subtask status to failed with error message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_cancelled", task_id: "task-9", error: "User cancelled" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-9", status: "failed", error: "User cancelled" },
      ]);
    });

    it("uses default error when none provided", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_cancelled", task_id: "task-9" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-9", status: "failed", error: "cancelled" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_cancelled" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });
  });

  describe("task_timed_out", () => {
    it("sets subtask status to failed with error message", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        {
          type: "task_timed_out",
          task_id: "task-10",
          error: "Timed out after 300s",
        },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-10", status: "failed", error: "Timed out after 300s" },
      ]);
    });

    it("uses default error when none provided", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_timed_out", task_id: "task-10" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-10", status: "failed", error: "timed out" },
      ]);
    });

    it("ignores event without task_id", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent({ type: "task_timed_out" }, ctx);
      expect(result).toBe(false);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
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

  describe("nested task events (parent_task_id set)", () => {
    it("swallows task_running without touching the store", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        {
          type: "task_running",
          task_id: "nested-1",
          parent_task_id: "parent-1",
          message: { id: "m1", type: "ai", content: "hi" },
        },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });

    it("swallows task_completed without touching the store", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        {
          type: "task_completed",
          task_id: "nested-1",
          parent_task_id: "parent-1",
          result: "done",
        },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });

    it("swallows task_idle without touching the store", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_idle", task_id: "nested-1", parent_task_id: "p" },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toHaveLength(0);
    });

    it("still processes lead events where parent_task_id is null", () => {
      const ctx = makeCtx();
      const result = handleCustomEvent(
        { type: "task_idle", task_id: "task-1", parent_task_id: null },
        ctx,
      );
      expect(result).toBe(true);
      expect(ctx.updateSubtaskCalls).toEqual([
        { id: "task-1", status: "idle", live: true },
      ]);
    });
  });
});
