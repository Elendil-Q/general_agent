import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import {
  accumulateUsage,
  formatTokenCount,
  selectCurrentContextUsage,
  selectHeaderTokenUsage,
} from "@/core/messages/usage";
import {
  getAssistantTurnUsageMessages,
  getMessageGroups,
} from "@/core/messages/utils";

test("accumulates each AI message usage only once by message id", () => {
  const aiMessage = {
    id: "ai-1",
    type: "ai",
    content: "Answer",
    usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
  } as Message;

  expect(accumulateUsage([aiMessage, aiMessage])).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("counts later usage-bearing snapshots for the same AI message id", () => {
  const earlySnapshot = {
    id: "ai-1",
    type: "ai",
    content: "Streaming...",
  } as Message;
  const completedSnapshot = {
    id: "ai-1",
    type: "ai",
    content: "Complete answer",
    usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
  } as Message;

  expect(accumulateUsage([earlySnapshot, completedSnapshot])).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("keeps header and per-turn aggregation consistent for duplicated UI groups", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Explain this",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "<think>checking context</think>Final answer",
      usage_metadata: { input_tokens: 20, output_tokens: 7, total_tokens: 27 },
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const usageMessagesByGroupIndex = getAssistantTurnUsageMessages(groups);
  const turnUsageMessages = usageMessagesByGroupIndex.at(-1);

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
  ]);
  expect(turnUsageMessages?.map((message) => message.id)).toEqual([
    "ai-1",
    "ai-1",
  ]);
  expect(accumulateUsage(messages)).toEqual(
    accumulateUsage(turnUsageMessages!),
  );
  expect(accumulateUsage(turnUsageMessages!)).toEqual({
    inputTokens: 20,
    outputTokens: 7,
    totalTokens: 27,
  });
});

test("prefers backend thread usage for header totals", () => {
  const messages = [
    {
      id: "ai-visible",
      type: "ai",
      content: "Visible answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 100, outputTokens: 50, totalTokens: 150 },
      messages,
    }),
  ).toEqual({
    inputTokens: 100,
    outputTokens: 50,
    totalTokens: 150,
  });
});

test("adds current in-flight message usage to backend header totals", () => {
  const completedMessages = [
    {
      id: "ai-completed",
      type: "ai",
      content: "Completed answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
    {
      id: "ai-pending",
      type: "ai",
      content: "Streaming answer",
      usage_metadata: { input_tokens: 4, output_tokens: 6, total_tokens: 10 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 100, outputTokens: 50, totalTokens: 150 },
      messages: completedMessages,
      pendingMessages: [completedMessages[1]!],
    }),
  ).toEqual({
    inputTokens: 104,
    outputTokens: 56,
    totalTokens: 160,
  });
});

test("falls back to visible messages when backend usage is unavailable or zero", () => {
  const messages = [
    {
      id: "ai-visible",
      type: "ai",
      content: "Visible answer",
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
  ] as Message[];

  expect(
    selectHeaderTokenUsage({
      backendUsage: null,
      messages,
    }),
  ).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
  expect(
    selectHeaderTokenUsage({
      backendUsage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
      messages,
    }),
  ).toEqual({
    inputTokens: 10,
    outputTokens: 5,
    totalTokens: 15,
  });
});

test("returns the last usage-bearing AI message as current context usage", () => {
  const messages = [
    {
      id: "ai-1",
      type: "ai",
      content: "First answer",
      usage_metadata: {
        input_tokens: 100,
        output_tokens: 20,
        total_tokens: 120,
      },
    },
    {
      id: "human-1",
      type: "human",
      content: "Follow-up",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Second answer",
      usage_metadata: {
        input_tokens: 150,
        output_tokens: 30,
        total_tokens: 180,
      },
    },
  ] as Message[];

  expect(selectCurrentContextUsage(messages)).toBe(180);
});

test("prefers pending in-flight usage snapshots for current context usage", () => {
  const messages = [
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
      usage_metadata: {
        input_tokens: 100,
        output_tokens: 20,
        total_tokens: 120,
      },
    },
  ] as Message[];
  const pendingMessages = [
    {
      id: "ai-2",
      type: "ai",
      content: "Streaming answer",
      usage_metadata: {
        input_tokens: 200,
        output_tokens: 5,
        total_tokens: 205,
      },
    },
  ] as Message[];

  expect(selectCurrentContextUsage(messages, pendingMessages)).toBe(205);
});

test("returns null current context usage when no usage metadata exists", () => {
  const messages = [
    { id: "human-1", type: "human", content: "Hi" },
    { id: "ai-1", type: "ai", content: "Hello" },
  ] as Message[];

  expect(selectCurrentContextUsage(messages)).toBeNull();
});

test("formats token counts compactly", () => {
  expect(formatTokenCount(999)).toBe("999");
  expect(formatTokenCount(1234)).toBe("1,234");
  expect(formatTokenCount(12345)).toBe("12.3K");
  expect(formatTokenCount(200000)).toBe("200K");
  expect(formatTokenCount(128000)).toBe("128K");
});
