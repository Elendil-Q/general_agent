import type { Message, Run } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import {
  buildThreadMessagesUrl,
  buildVisibleHistoryMessages,
  getOldestRunMessageSeq,
  getSupersededRunIds,
  getSummarizationMiddlewareMessages,
  getThreadMessagesPreviousPageParam,
  getVisibleOptimisticMessages,
  mergeMessages,
  removeSetItems,
  runMessagesPageHasMore,
  THREAD_MESSAGES_PAGE_SIZE,
  threadMessagesQueryKey,
} from "@/core/threads/hooks";
import type { RunMessage } from "@/core/threads/types";

function runMessage(seq?: number): RunMessage {
  return {
    run_id: "run-1",
    ...(seq === undefined ? {} : { seq }),
    content: {} as Message,
    metadata: { caller: "" },
    created_at: "2026-05-22T00:00:00Z",
  };
}

test("mergeMessages removes duplicate messages already present in history", () => {
  const human = {
    id: "human-1",
    type: "human",
    content: "Design an agent",
  } as Message;
  const ai = {
    id: "ai-1",
    type: "ai",
    content: "Let's design it.",
  } as Message;

  expect(mergeMessages([human, ai, human, ai], [], [])).toEqual([human, ai]);
});

test("mergeMessages lets live thread messages replace overlapping history", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "old",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const oldAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
  } as Message;

  expect(mergeMessages([oldHuman, oldAi], [liveHuman, liveAi], [])).toEqual([
    liveHuman,
    liveAi,
  ]);
});

test("mergeMessages deduplicates tool messages by tool_call_id", () => {
  const oldTool = {
    id: "tool-message-old",
    type: "tool",
    tool_call_id: "call-1",
    content: "old",
  } as Message;
  const liveTool = {
    id: "tool-message-live",
    type: "tool",
    tool_call_id: "call-1",
    content: "live",
  } as Message;

  expect(mergeMessages([oldTool], [liveTool], [])).toEqual([liveTool]);
});

test("mergeMessages keeps a visible history message when a hidden live message reuses its id", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const hiddenReminder = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live answer",
  } as Message;

  expect(mergeMessages([historyHuman], [hiddenReminder, liveAi], [])).toEqual([
    historyHuman,
    liveAi,
  ]);
});

test("mergeMessages lets a visible live message replace overlapping hidden history", () => {
  const hiddenHistoryHuman = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;

  expect(mergeMessages([hiddenHistoryHuman], [liveHuman], [])).toEqual([
    liveHuman,
  ]);
});

test("getSummarizationMiddlewareMessages matches DeerFlow summarization update keys", () => {
  const removeAll = {
    id: "__remove_all__",
    type: "remove",
    content: "",
  } as Message;
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "DeerFlowSummarizationMiddleware.before_model": {
        messages: [removeAll, summary],
      },
    }),
  ).toEqual([removeAll, summary]);
});

test("getSummarizationMiddlewareMessages matches base LangChain summarization update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "SummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toEqual([summary]);
});

test("getSummarizationMiddlewareMessages ignores unrelated suffix-sharing update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "OtherSummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toBeUndefined();
});

test("getVisibleOptimisticMessages hides optimistic user input after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 1)).toEqual([]);
});

test("mergeMessages shows server human instead of optimistic duplicate after first response", () => {
  const serverHuman = {
    id: "server-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const visibleOptimistic = getVisibleOptimisticMessages(
    [optimisticHuman],
    0,
    1,
  );

  expect(mergeMessages([], [serverHuman], visibleOptimistic)).toEqual([
    serverHuman,
  ]);
});

test("getVisibleOptimisticMessages keeps optimistic user input until server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 0)).toEqual([
    optimisticHuman,
  ]);
});

test("getVisibleOptimisticMessages keeps non-human optimistic status messages", () => {
  const optimisticAi = {
    id: "opt-ai-1",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticAi], 0, 1)).toEqual([
    optimisticAi,
  ]);
});

test("getVisibleOptimisticMessages hides the upload optimistic pair after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "upload this",
  } as Message;
  const optimisticUploadingAi = {
    id: "opt-ai-uploading",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(
    getVisibleOptimisticMessages(
      [optimisticHuman, optimisticUploadingAi],
      0,
      1,
    ),
  ).toEqual([]);
});

test("getVisibleOptimisticMessages hides optimistic user input after later server turns", () => {
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "follow up",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 4)).toEqual([]);
  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 3)).toEqual([
    optimisticHuman,
  ]);
});

test("runMessagesPageHasMore reads backend snake_case pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], has_more: true })).toBe(true);
  expect(runMessagesPageHasMore({ data: [], has_more: false })).toBe(false);
});

test("runMessagesPageHasMore keeps compatibility with camelCase pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], hasMore: true })).toBe(true);
});

test("getOldestRunMessageSeq returns the cursor for the next older run page", () => {
  expect(
    getOldestRunMessageSeq([runMessage(8), runMessage(9), runMessage(10)]),
  ).toBe(8);
});

test("getOldestRunMessageSeq ignores rows without seq", () => {
  expect(getOldestRunMessageSeq([runMessage()])).toBeNull();
});

test("getSupersededRunIds combines completed regenerate metadata with pending ids", () => {
  const runs = [
    {
      run_id: "run-new",
      status: "success",
      metadata: { regenerate_from_run_id: "run-old" },
    },
    {
      run_id: "run-normal",
      status: "success",
      metadata: {},
    },
  ] as unknown as Run[];

  expect(getSupersededRunIds(runs, new Set(["run-pending"]))).toEqual(
    new Set(["run-old", "run-pending"]),
  );
});

test("getSupersededRunIds ignores failed regenerate runs but keeps pending ids", () => {
  const runs = [
    {
      run_id: "run-error",
      status: "error",
      metadata: { regenerate_from_run_id: "run-old" },
    },
    {
      run_id: "run-interrupted",
      status: "interrupted",
      metadata: { regenerate_from_run_id: "run-older" },
    },
  ] as unknown as Run[];

  expect(getSupersededRunIds(runs, new Set(["run-pending"]))).toEqual(
    new Set(["run-pending"]),
  );
});

test("removeSetItems removes pending superseded ids after submit failure", () => {
  expect(
    removeSetItems(new Set(["run-old", "run-other"]), ["run-old"]),
  ).toEqual(new Set(["run-other"]));
});

test("buildVisibleHistoryMessages filters superseded runs but keeps regenerated run", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "question",
  } as Message;
  const oldAi = {
    id: "ai-old",
    type: "ai",
    content: "old answer",
  } as Message;
  const newHuman = {
    id: "human-1",
    type: "human",
    content: "question",
  } as Message;
  const newAi = {
    id: "ai-new",
    type: "ai",
    content: "new answer",
  } as Message;
  const rows: RunMessage[] = [
    {
      run_id: "run-old",
      content: oldHuman,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:00Z",
    },
    {
      run_id: "run-old",
      content: oldAi,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-new",
      content: newHuman,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
    {
      run_id: "run-new",
      content: newAi,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:03Z",
    },
  ];

  expect(buildVisibleHistoryMessages(rows, new Set(["run-old"]))).toEqual([
    newHuman,
    newAi,
  ]);
});

test("buildThreadMessagesUrl encodes thread id and optional before_seq", () => {
  const url = buildThreadMessagesUrl(
    "http://localhost:8001/",
    "thread/a b",
    41,
  );
  expect(url).toBe(
    "http://localhost:8001/api/threads/thread%2Fa%20b/messages?limit=100&before_seq=41",
  );
});

test("buildThreadMessagesUrl omits before_seq when loading the latest page", () => {
  expect(buildThreadMessagesUrl("", "t1")).toBe(
    "/api/threads/t1/messages?limit=100",
  );
});

test("getThreadMessagesPreviousPageParam returns oldest seq when more pages exist", () => {
  expect(
    getThreadMessagesPreviousPageParam({
      data: [runMessage(30), runMessage(31)],
      has_more: true,
    }),
  ).toBe(30);
});

test("getThreadMessagesPreviousPageParam stops when no more pages exist", () => {
  expect(
    getThreadMessagesPreviousPageParam({
      data: [runMessage(1)],
      has_more: false,
    }),
  ).toBeUndefined();
});

test("getThreadMessagesPreviousPageParam stops when has_more lacks seq values", () => {
  expect(
    getThreadMessagesPreviousPageParam({
      data: [runMessage()],
      has_more: true,
    }),
  ).toBeUndefined();
});

test("threadMessagesQueryKey scopes messages under the thread key", () => {
  expect(threadMessagesQueryKey("t1")).toEqual(["thread", "t1", "messages"]);
});

test("thread messages endpoint uses a fixed page size", () => {
  expect(THREAD_MESSAGES_PAGE_SIZE).toBe(100);
});

test("thread messages pages flattened oldest-page-first stay in global seq order", () => {
  // useInfiniteQuery fetchPreviousPage prepends older pages: pages[0] is the
  // oldest loaded page; flatMap preserves ascending global seq (#3825 follow-up).
  const pages = [
    { data: [runMessage(1), runMessage(2)] },
    { data: [runMessage(3), runMessage(4)] },
  ];
  const rows = pages.flatMap((p) => p.data);
  expect(rows.map((r) => r.seq)).toEqual([1, 2, 3, 4]);
});
