import type { AIMessage } from "@langchain/langgraph-sdk";

import type { Subtask } from "../tasks/types";

export interface CustomEventContext {
  updateSubtask: (task: Partial<Subtask> & { id: string }) => void;
  toast: (message: string) => void;
}

/**
 * Handle custom SSE events dispatched by the backend during streaming.
 * Extracted from useThreadChat.onCustomEvent for testability.
 * Returns true if the event was recognized and handled.
 */
export function handleCustomEvent(
  event: unknown,
  ctx: CustomEventContext,
): boolean {
  if (typeof event !== "object" || event === null || !("type" in event)) {
    return false;
  }

  if (event.type === "task_running") {
    const e = event as {
      type: "task_running";
      task_id: string;
      message: AIMessage;
    };
    ctx.updateSubtask({ id: e.task_id, latestMessage: e.message });
    return true;
  }

  if (
    event.type === "task_idle" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as { type: "task_idle"; task_id: string };
    ctx.updateSubtask({ id: e.task_id, status: "idle" });
    return true;
  }

  if (
    event.type === "task_revived" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as { type: "task_revived"; task_id: string };
    ctx.updateSubtask({ id: e.task_id, status: "in_progress" });
    return true;
  }

  if (
    event.type === "task_expired" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as { type: "task_expired"; task_id: string };
    ctx.updateSubtask({ id: e.task_id, status: "expired" });
    return true;
  }

  if (
    event.type === "task_completed" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as {
      type: "task_completed";
      task_id: string;
      result?: string;
    };
    ctx.updateSubtask({
      id: e.task_id,
      status: "completed",
      ...(e.result !== undefined ? { result: e.result } : {}),
    });
    return true;
  }

  if (
    event.type === "task_failed" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as {
      type: "task_failed";
      task_id: string;
      error?: string;
    };
    ctx.updateSubtask({
      id: e.task_id,
      status: "failed",
      ...(e.error !== undefined ? { error: e.error } : {}),
    });
    return true;
  }

  if (
    event.type === "task_cancelled" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as {
      type: "task_cancelled";
      task_id: string;
      error?: string;
    };
    ctx.updateSubtask({
      id: e.task_id,
      status: "failed",
      error: e.error ?? "cancelled",
    });
    return true;
  }

  if (
    event.type === "task_timed_out" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string"
  ) {
    const e = event as {
      type: "task_timed_out";
      task_id: string;
      error?: string;
    };
    ctx.updateSubtask({
      id: e.task_id,
      status: "failed",
      error: e.error ?? "timed out",
    });
    return true;
  }

  if (
    event.type === "budget_warning" &&
    "message" in event &&
    typeof (event as { message: unknown }).message === "string"
  ) {
    const e = event as { type: "budget_warning"; message: string };
    ctx.toast(e.message);
    return true;
  }

  if (
    event.type === "budget_exceeded" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string" &&
    "message" in event &&
    typeof (event as { message: unknown }).message === "string"
  ) {
    const e = event as {
      type: "budget_exceeded";
      task_id: string;
      message: string;
    };
    ctx.updateSubtask({
      id: e.task_id,
      status: "failed",
      error: e.message,
    });
    return true;
  }

  if (
    event.type === "llm_retry" &&
    "message" in event &&
    typeof (event as { message: unknown }).message === "string" &&
    (event as { message: string }).message.trim()
  ) {
    const e = event as { type: "llm_retry"; message: string };
    ctx.toast(e.message);
    return true;
  }

  if (
    event.type === "task_progress" &&
    "task_id" in event &&
    typeof (event as { task_id: unknown }).task_id === "string" &&
    "message" in event
  ) {
    const e = event as {
      type: "task_progress";
      task_id: string;
      message: AIMessage;
    };
    ctx.updateSubtask({ id: e.task_id, latestMessage: e.message });
    return true;
  }

  return false;
}
