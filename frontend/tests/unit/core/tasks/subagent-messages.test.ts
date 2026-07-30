import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import {
  isTerminalSubagentStatus,
  mergeSubagentLiveMessage,
  resolveSubagentView,
  shouldRefetchOnTerminalTransition,
  subagentMessagesQueryKey,
  TERMINAL_REFOLLOW_DELAY_MS,
} from "@/core/tasks/subagent-messages";

function aiMessage(id: string | undefined, content: string): AIMessage {
  return {
    ...(id === undefined ? {} : { id }),
    type: "ai",
    content,
  } as AIMessage;
}

function historyOf(...ids: string[]): Message[] {
  return ids.map((id) => aiMessage(id, `history-${id}`) as Message);
}

describe("isTerminalSubagentStatus", () => {
  it("treats completed/failed/expired/cancelled as terminal", () => {
    for (const status of ["completed", "failed", "expired", "cancelled"]) {
      expect(isTerminalSubagentStatus(status)).toBe(true);
    }
  });

  it("treats live/paused/empty statuses as non-terminal", () => {
    for (const status of [
      "pending",
      "running",
      "in_progress",
      "idle",
      "interrupted",
      null,
      undefined,
    ]) {
      expect(isTerminalSubagentStatus(status)).toBe(false);
    }
  });
});

describe("mergeSubagentLiveMessage", () => {
  it("appends the live message when its id is not in history", () => {
    const history = historyOf("a", "b");
    const live = aiMessage("c", "live-c");

    const merged = mergeSubagentLiveMessage(history, live, false);

    expect(merged).toHaveLength(3);
    expect(merged[2]).toBe(live);
  });

  it("replaces the history entry when the live message has the same id", () => {
    const history = historyOf("a", "b");
    const live = aiMessage("b", "live-b-updated");

    const merged = mergeSubagentLiveMessage(history, live, false);

    expect(merged).toHaveLength(2);
    expect(merged[0]).toBe(history[0]);
    expect(merged[1]).toBe(live);
  });

  it("returns history unchanged once the task is terminal", () => {
    const history = historyOf("a");
    const live = aiMessage("b", "live-b");

    expect(mergeSubagentLiveMessage(history, live, true)).toBe(history);
  });

  it("does not merge an id-less live message", () => {
    const history = historyOf("a");
    const live = aiMessage(undefined, "live-no-id");

    expect(mergeSubagentLiveMessage(history, live, false)).toBe(history);
  });

  it("returns history unchanged when there is no live message", () => {
    const history = historyOf("a");

    expect(mergeSubagentLiveMessage(history, null, false)).toBe(history);
    expect(mergeSubagentLiveMessage(history, undefined, false)).toBe(history);
  });

  it("does not mutate the history array", () => {
    const history = historyOf("a");
    const snapshot = [...history];

    mergeSubagentLiveMessage(history, aiMessage("a", "live-a"), false);
    mergeSubagentLiveMessage(history, aiMessage("b", "live-b"), false);

    expect(history).toEqual(snapshot);
  });
});

describe("subagentMessagesQueryKey", () => {
  it("builds the query key used by useSubagentMessages", () => {
    expect(subagentMessagesQueryKey("thread-1", "task-1")).toEqual([
      "subagent",
      "messages",
      "thread-1",
      "task-1",
    ]);
  });
});

describe("shouldRefetchOnTerminalTransition", () => {
  it("fires only on the non-terminal → terminal flip", () => {
    expect(shouldRefetchOnTerminalTransition(false, true)).toBe(true);
    expect(shouldRefetchOnTerminalTransition(true, true)).toBe(false);
    expect(shouldRefetchOnTerminalTransition(false, false)).toBe(false);
    expect(shouldRefetchOnTerminalTransition(true, false)).toBe(false);
  });
});

describe("TERMINAL_REFOLLOW_DELAY_MS", () => {
  it("is a short positive delay for the post-terminal follow-up refetch", () => {
    // The timer itself is scheduled inside the page component's effect,
    // which the node-based unit env (no jsdom) cannot honestly exercise.
    expect(typeof TERMINAL_REFOLLOW_DELAY_MS).toBe("number");
    expect(TERMINAL_REFOLLOW_DELAY_MS).toBeGreaterThan(0);
    expect(TERMINAL_REFOLLOW_DELAY_MS).toBeLessThanOrEqual(1000);
  });
});

describe("resolveSubagentView", () => {
  it("merges the live snapshot while the task is running", () => {
    const history = historyOf("a");
    const live = aiMessage("b", "live-b");

    const view = resolveSubagentView(history, live, false, false);

    expect(view).toHaveLength(2);
    expect(view[1]).toBe(live);
  });

  it("keeps the live-merged view during the terminal refetch window", () => {
    const history = historyOf("a");
    const live = aiMessage("b", "live-b");

    const view = resolveSubagentView(history, live, true, true);

    expect(view).toHaveLength(2);
    expect(view[1]).toBe(live);
  });

  it("still overwrites by id during the terminal refetch window", () => {
    const history = historyOf("a");
    const live = aiMessage("a", "live-a-updated");

    const view = resolveSubagentView(history, live, true, true);

    expect(view).toHaveLength(1);
    expect(view[0]).toBe(live);
  });

  it("shows persisted history verbatim once the refetch has landed", () => {
    const history = historyOf("a", "b");
    const live = aiMessage("c", "live-c");

    const view = resolveSubagentView(history, live, true, false);

    expect(view).toBe(history);
  });
});
