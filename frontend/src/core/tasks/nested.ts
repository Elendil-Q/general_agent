import type { AIMessage } from "@langchain/langgraph-sdk";

import type { Subtask } from "./types";

export interface NestedDelegation {
  subagent_type: string;
  description: string;
}

/**
 * Whether a subtask is a lead-thread (depth 1) task. Nested subagents
 * (depth 2, spawned by another subagent) carry ``parent_task_id`` and are
 * hidden from the activity panel — their progress surfaces only as a
 * transient hint on the parent's SubtaskCard.
 */
export function isLeadSubtask(task: Subtask): boolean {
  return task.parent_task_id == null;
}

/**
 * ``task`` tool calls present in a message — the subagent's own nested
 * delegations, used for the transient progress hint on SubtaskCard.
 */
export function getNestedDelegations(
  message: AIMessage | undefined,
): NestedDelegation[] {
  if (!message?.tool_calls?.length) {
    return [];
  }
  return message.tool_calls
    .filter((call) => call.name === "task")
    .map((call) => {
      const args = (call.args ?? {}) as Record<string, unknown>;
      return {
        subagent_type: (args.subagent_type as string) ?? "",
        description: (args.description as string) ?? "",
      };
    });
}
