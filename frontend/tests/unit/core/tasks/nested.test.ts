import type { AIMessage } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import { getNestedDelegations, isLeadSubtask } from "@/core/tasks/nested";
import type { Subtask } from "@/core/tasks/types";

function makeSubtask(overrides: Partial<Subtask> = {}): Subtask {
  return {
    id: "task-1",
    status: "in_progress",
    subagent_type: "general-purpose",
    description: "do something",
    prompt: "prompt",
    ...overrides,
  };
}

function makeMessage(toolCalls: AIMessage["tool_calls"]): AIMessage {
  return {
    id: "msg-1",
    type: "ai",
    content: "",
    tool_calls: toolCalls,
  } as unknown as AIMessage;
}

describe("isLeadSubtask", () => {
  it("returns true when parent_task_id is undefined", () => {
    expect(isLeadSubtask(makeSubtask())).toBe(true);
  });

  it("returns true when parent_task_id is null", () => {
    expect(isLeadSubtask(makeSubtask({ parent_task_id: null }))).toBe(true);
  });

  it("returns false when parent_task_id is set", () => {
    expect(isLeadSubtask(makeSubtask({ parent_task_id: "parent-1" }))).toBe(
      false,
    );
  });
});

describe("getNestedDelegations", () => {
  it("returns empty array for undefined message", () => {
    expect(getNestedDelegations(undefined)).toEqual([]);
  });

  it("returns empty array when there are no tool calls", () => {
    expect(getNestedDelegations(makeMessage(undefined))).toEqual([]);
    expect(getNestedDelegations(makeMessage([]))).toEqual([]);
  });

  it("extracts task tool calls with subagent_type and description", () => {
    const message = makeMessage([
      {
        id: "call-1",
        name: "task",
        args: { subagent_type: "researcher", description: "dig deeper" },
      } as never,
    ]);
    expect(getNestedDelegations(message)).toEqual([
      { subagent_type: "researcher", description: "dig deeper" },
    ]);
  });

  it("ignores non-task tool calls", () => {
    const message = makeMessage([
      {
        id: "call-1",
        name: "web_search",
        args: { query: "foo" },
      } as never,
    ]);
    expect(getNestedDelegations(message)).toEqual([]);
  });

  it("keeps only task calls among mixed tool calls", () => {
    const message = makeMessage([
      { id: "c1", name: "web_search", args: { query: "x" } } as never,
      {
        id: "c2",
        name: "task",
        args: { subagent_type: "coder", description: "write code" },
      } as never,
      {
        id: "c3",
        name: "task",
        args: { subagent_type: "researcher", description: "check facts" },
      } as never,
    ]);
    expect(getNestedDelegations(message)).toEqual([
      { subagent_type: "coder", description: "write code" },
      { subagent_type: "researcher", description: "check facts" },
    ]);
  });

  it("tolerates missing args fields", () => {
    const message = makeMessage([
      { id: "c1", name: "task", args: {} } as never,
    ]);
    expect(getNestedDelegations(message)).toEqual([
      { subagent_type: "", description: "" },
    ]);
  });
});
