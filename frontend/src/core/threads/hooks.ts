import type { AIMessage, Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type QueryClient,
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { useEffectiveEffortsConfig, resolveEffortFlags } from "../effort/hooks";
import { useI18n } from "../i18n/hooks";
import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFiles } from "../uploads";

import { fetchThreadSystemPrompt, fetchThreadTokenUsage } from "./api";
import {
  type ClarificationInterruptRequest,
  isClarificationInterrupt,
} from "./clarification";
import { resumeSubagent } from "./subagent-resume";
import { threadSystemPromptQueryKey } from "./system-prompt";
import {
  buildThreadsSearchQueryOptions,
  DEFAULT_THREAD_SEARCH_PARAMS,
  type ThreadSearchParams,
} from "./thread-search-query";
import { threadTokenUsageQueryKey } from "./token-usage";
import type {
  AgentThread,
  AgentThreadState,
  RunMessage,
  ThreadTokenUsageResponse,
  ThreadSystemPromptResponse,
} from "./types";

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  displayThreadId?: string | null | undefined;
  context: LocalSettings["context"];
  isMock?: boolean;
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
};

type RegeneratePrepareResponse = {
  input: Partial<AgentThreadState>;
  checkpoint: {
    checkpoint_ns: string;
    checkpoint_id: string;
    checkpoint_map: Record<string, unknown> | null;
  };
  metadata: Record<string, unknown>;
  target_run_id: string;
};

const EMPTY_THREAD_VALUES: AgentThreadState = {
  title: "",
  messages: [],
  artifacts: [],
  todos: [],
};

function isNonEmptyString(value: string | undefined): value is string {
  return typeof value === "string" && value.length > 0;
}

const SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS = new Set([
  "SummarizationMiddleware.before_model",
  "DeerFlowSummarizationMiddleware.before_model",
]);

function messageIdentity(message: Message): string | undefined {
  if (
    "tool_call_id" in message &&
    typeof message.tool_call_id === "string" &&
    message.tool_call_id.length > 0
  ) {
    return `tool:${message.tool_call_id}`;
  }
  if (typeof message.id === "string" && message.id.length > 0) {
    return `message:${message.id}`;
  }
  return undefined;
}

function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const lastIndexByIdentity = new Map<string, number>();
  const lastVisibleIndexByIdentity = new Map<string, number>();

  // This is a UI-display dedupe rule, not a general LangChain message-stream
  // contract. Hidden messages that share an identity with a visible message are
  // treated as control messages for this merged view; hidden messages carrying
  // independent tracing/task semantics should use a distinct id or a custom
  // stream/state channel instead of relying on message dedupe preservation.
  const preservedTurnDurations = new Map<string, number>();
  messages.forEach((message, index) => {
    const identity = messageIdentity(message);
    if (identity) {
      lastIndexByIdentity.set(identity, index);
      if (!isHiddenFromUIMessage(message)) {
        lastVisibleIndexByIdentity.set(identity, index);
      }
      if (message.additional_kwargs?.turn_duration !== undefined) {
        preservedTurnDurations.set(
          identity,
          message.additional_kwargs.turn_duration as number,
        );
      }
    }
  });

  return messages
    .filter((message, index) => {
      const identity = messageIdentity(message);
      if (!identity) {
        return true;
      }
      const visibleIndex = lastVisibleIndexByIdentity.get(identity);
      if (visibleIndex !== undefined) {
        return visibleIndex === index;
      }
      return lastIndexByIdentity.get(identity) === index;
    })
    .map((message) => {
      const identity = messageIdentity(message);
      if (
        identity &&
        preservedTurnDurations.has(identity) &&
        message.additional_kwargs?.turn_duration === undefined
      ) {
        return {
          ...message,
          additional_kwargs: {
            ...message.additional_kwargs,
            turn_duration: preservedTurnDurations.get(identity),
          },
        } as Message;
      }
      return message;
    });
}

export function getSupersededRunIds(
  runs: Run[] | undefined,
  pendingSupersededRunIds?: ReadonlySet<string>,
) {
  const ids = new Set(pendingSupersededRunIds ?? []);
  for (const run of runs ?? []) {
    if (run.status !== "success") {
      continue;
    }
    const metadata = run.metadata;
    if (metadata && typeof metadata === "object") {
      const fromRunId = Reflect.get(metadata, "regenerate_from_run_id");
      if (typeof fromRunId === "string" && fromRunId) {
        ids.add(fromRunId);
      }
    }
  }
  return ids;
}

export function removeSetItems<T>(
  values: ReadonlySet<T>,
  itemsToRemove: Iterable<T>,
) {
  const next = new Set(values);
  for (const item of itemsToRemove) {
    next.delete(item);
  }
  return next;
}

export function buildVisibleHistoryMessages(
  messageRows: RunMessage[],
  supersededRunIds: ReadonlySet<string>,
) {
  const visibleRows = messageRows.filter(
    (message) => !supersededRunIds.has(message.run_id),
  );
  return dedupeMessagesByIdentity(
    visibleRows.map((message) => message.content),
  );
}

export function runMessagesPageHasMore(result: ThreadMessagesPage) {
  return result.has_more ?? result.hasMore ?? false;
}

export function getOldestRunMessageSeq(messages: RunMessage[]) {
  let oldestSeq: number | null = null;
  for (const message of messages) {
    if (typeof message.seq !== "number") {
      continue;
    }
    oldestSeq =
      oldestSeq === null ? message.seq : Math.min(oldestSeq, message.seq);
  }
  return oldestSeq;
}

export const THREAD_MESSAGES_PAGE_SIZE = 100;

export function threadMessagesQueryKey(threadId: string) {
  return ["thread", threadId, "messages"] as const;
}

export type ThreadMessagesPage = {
  data: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

export function buildThreadMessagesUrl(
  baseUrl: string,
  threadId: string,
  beforeSeq?: number,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/messages`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  url.searchParams.set("limit", String(THREAD_MESSAGES_PAGE_SIZE));
  if (beforeSeq !== undefined) {
    url.searchParams.set("before_seq", String(beforeSeq));
  }
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
}

export function getThreadMessagesPreviousPageParam(
  firstPage: ThreadMessagesPage,
): number | undefined {
  if (!runMessagesPageHasMore(firstPage)) {
    return undefined;
  }
  return getOldestRunMessageSeq(firstPage.data) ?? undefined;
}

export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  // Only visible live messages should trim overlapping history. Hidden messages
  // are UI control messages in this path, not observability records; any hidden
  // message that must survive as task/tracing data should use custom events or a
  // separate state channel instead of participating in this overlap heuristic.

  const savedTurnDurations = new Map<string, number>();
  for (const msg of historyMessages) {
    const identity = messageIdentity(msg);
    if (identity && msg.additional_kwargs?.turn_duration !== undefined) {
      savedTurnDurations.set(
        identity,
        msg.additional_kwargs.turn_duration as number,
      );
    }
  }

  const threadMessageIds = new Set(
    threadMessages
      .filter((message) => !isHiddenFromUIMessage(message))
      .map(messageIdentity)
      .filter(isNonEmptyString),
  );

  // The overlap is a contiguous suffix of historyMessages (newest history == oldest thread).
  // Scan from the end: shrink cutoff while messages are already in thread, stop as soon as
  // we hit one that isn't — everything before that point is non-overlapping.
  let cutoff = historyMessages.length;
  for (let i = historyMessages.length - 1; i >= 0; i--) {
    const msg = historyMessages[i];
    if (!msg) {
      continue;
    }
    const identity = messageIdentity(msg);
    if (identity && threadMessageIds.has(identity)) {
      cutoff = i;
    } else {
      break;
    }
  }

  const merged = dedupeMessagesByIdentity([
    ...historyMessages.slice(0, cutoff),
    ...threadMessages,
    ...optimisticMessages,
  ]);

  return merged.map((message) => {
    const identity = messageIdentity(message);
    if (
      identity &&
      savedTurnDurations.has(identity) &&
      message.additional_kwargs?.turn_duration === undefined
    ) {
      return {
        ...message,
        additional_kwargs: {
          ...message.additional_kwargs,
          turn_duration: savedTurnDurations.get(identity),
        },
      } as Message;
    }
    return message;
  });
}

function getMessagesAfterBaseline(
  messages: Message[],
  baselineMessageIds: ReadonlySet<string>,
): Message[] {
  return messages.filter((message) => {
    const id = messageIdentity(message);
    return !id || !baselineMessageIds.has(id);
  });
}

export function getVisibleOptimisticMessages(
  optimisticMessages: Message[],
  previousHumanMessageCount: number,
  currentHumanMessageCount: number,
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    currentHumanMessageCount > previousHumanMessageCount
  ) {
    return [];
  }
  return optimisticMessages;
}

export function getSummarizationMiddlewareMessages(
  data: unknown,
): Message[] | undefined {
  if (typeof data !== "object" || data === null) {
    return undefined;
  }

  for (const [key, update] of Object.entries(data)) {
    if (!SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS.has(key)) {
      continue;
    }
    if (typeof update !== "object" || update === null) {
      continue;
    }

    const messages = Reflect.get(update, "messages");
    if (Array.isArray(messages)) {
      return [...messages] as Message[];
    }
  }

  return undefined;
}

export function upsertThreadInSearchCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: Array<AgentThread> | undefined) => {
      if (!oldData) {
        return [thread];
      }

      const existingIndex = oldData.findIndex(
        (t) => t.thread_id === thread.thread_id,
      );
      if (existingIndex === -1) {
        return [thread, ...oldData];
      }

      return oldData.map((t, index) => {
        if (index !== existingIndex) {
          return t;
        }
        return {
          ...thread,
          ...t,
          metadata: {
            ...(thread.metadata ?? {}),
            ...(t.metadata ?? {}),
          },
          values: {
            ...thread.values,
            ...t.values,
          },
        };
      });
    },
  );
}

export function upsertThreadInInfiniteCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  queryClient.setQueriesData(
    {
      queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      exact: false,
    },
    (oldData: InfiniteData<AgentThread[]> | undefined) => {
      if (!oldData) {
        return oldData;
      }

      const merged = oldData.pages.map((page) =>
        page.map((t) =>
          t.thread_id === thread.thread_id
            ? {
                ...thread,
                ...t,
                metadata: {
                  ...(thread.metadata ?? {}),
                  ...(t.metadata ?? {}),
                },
                values: {
                  ...thread.values,
                  ...t.values,
                },
              }
            : t,
        ),
      );

      const exists = merged.some((page) =>
        page.some((t) => t.thread_id === thread.thread_id),
      );
      if (exists) {
        return { ...oldData, pages: merged };
      }

      const firstPage = merged[0] ?? [];
      const restPages = merged.slice(1);
      return {
        ...oldData,
        pages: [[thread, ...firstPage], ...restPages],
      };
    },
  );
}

function getStreamErrorMessage(error: unknown): string {
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string" && message.trim()) {
      return message;
    }
    const nestedError = Reflect.get(error, "error");
    if (nestedError instanceof Error && nestedError.message.trim()) {
      return nestedError.message;
    }
    if (typeof nestedError === "string" && nestedError.trim()) {
      return nestedError;
    }
  }
  return "Request failed.";
}

async function readResponseErrorMessage(
  response: Response,
  fallback = "Request failed.",
) {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string" && data.detail.trim()) {
      return data.detail;
    }
  } catch {
    // Use the fallback below when the response body is not JSON.
  }
  return response.statusText || fallback;
}

function getHttpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) {
    return undefined;
  }

  const status = Reflect.get(error, "status");
  if (typeof status === "number") {
    return status;
  }

  const response = Reflect.get(error, "response");
  if (typeof response === "object" && response !== null) {
    const responseStatus = Reflect.get(response, "status");
    if (typeof responseStatus === "number") {
      return responseStatus;
    }
  }

  return undefined;
}

function isThreadMissingError(error: unknown): boolean {
  const status = getHttpStatus(error);
  // Treat 403 like 404 here to avoid disclosing whether an inaccessible thread
  // exists; callers redirect stale/inaccessible URLs back to a blank chat.
  return status === 403 || status === 404;
}

export function useThreadStream({
  threadId,
  displayThreadId,
  context,
  isMock,
  onSend,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  // Effort presets drive the effort -> runtime-flags mapping (thinking_enabled,
  // is_plan_mode, subagent_enabled, reasoning_effort). Falls back to the
  // built-in four when the backend is older (404) or before the fetch resolves.
  const { efforts } = useEffectiveEffortsConfig();
  const currentViewThreadId = displayThreadId ?? threadId ?? null;
  const currentViewThreadIdRef = useRef(currentViewThreadId);
  currentViewThreadIdRef.current = currentViewThreadId;
  // Optimistic messages shown before the server stream responds.
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [optimisticThreadId, setOptimisticThreadId] = useState<string | null>(
    null,
  );
  const [liveMessagesThreadId, setLiveMessagesThreadId] = useState<
    string | null
  >(null);
  const [pendingSupersededRunIds, setPendingSupersededRunIds] = useState<
    ReadonlySet<string>
  >(() => new Set());
  const [pendingSupersededMessageIds, setPendingSupersededMessageIds] =
    useState<ReadonlySet<string>>(() => new Set());
  const [isUploading, setIsUploading] = useState(false);
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  // Ref to track current thread ID across async callbacks without causing re-renders,
  // and to allow access to the current thread id in onUpdateEvent
  const threadIdRef = useRef<string | null>(threadId ?? null);
  const startedRef = useRef(false);
  const pendingUsageBaselineMessageIdsRef = useRef<Set<string>>(new Set());
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
  });

  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
  } = useThreadHistory(onStreamThreadId ?? "", {
    enabled: !isMock,
    pendingSupersededRunIds,
  });

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = { onSend, onStart, onFinish, onToolEnd };
  }, [onSend, onStart, onFinish, onToolEnd]);

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      startedRef.current = false;
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    threadIdRef.current = normalizedThreadId;
  }, [threadId]);

  const handleStreamStart = useCallback((_threadId: string, _runId: string) => {
    threadIdRef.current = _threadId;
    setOptimisticThreadId((currentOptimisticThreadId) => {
      const currentView = currentViewThreadIdRef.current;
      if (
        currentOptimisticThreadId &&
        (currentOptimisticThreadId === currentView ||
          currentOptimisticThreadId === _threadId)
      ) {
        return _threadId;
      }
      return currentOptimisticThreadId;
    });
    setLiveMessagesThreadId((currentLiveMessagesThreadId) => {
      const currentView = currentViewThreadIdRef.current;
      if (
        currentLiveMessagesThreadId &&
        (currentLiveMessagesThreadId === currentView ||
          currentLiveMessagesThreadId === _threadId)
      ) {
        return _threadId;
      }
      return currentLiveMessagesThreadId;
    });
    if (!startedRef.current) {
      listeners.current.onStart?.(_threadId, _runId);
      startedRef.current = true;
    }
    setOnStreamThreadId(_threadId);
  }, []);

  const queryClient = useQueryClient();
  const updateSubtask = useUpdateSubtask();

  // --- Clarification interrupt latch -------------------------------------
  // The SDK's `thread.interrupt` getter is unreliable for driving a modal:
  // `fetchStateHistory` returns a `historyValues` snapshot WITHOUT
  // `__interrupt__` (interrupts are a live-stream construct), so on
  // thread-switch / page-refresh the getter is briefly undefined until
  // `reconnectOnMount` + `joinStream` re-establishes the stream. Latching the
  // interrupt locally from `thread.values.__interrupt__` and clearing it only
  // on explicit lifecycle events (submit / dismiss / thread switch / run
  // finish) keeps the modal stable instead of flickering closed.
  const [clarificationInterruptValue, setClarificationInterruptValue] =
    useState<ClarificationInterruptRequest | null>(null);
  const [clarificationDismissed, setClarificationDismissed] = useState(false);
  // tool_call_id of the interrupt we most recently submitted a resume for, so
  // a reconnected stream replaying the same paused interrupt doesn't re-open
  // the modal after the user already answered.
  const submittedInterruptIdRef = useRef<string | null>(null);

  // --- Subagent clarification queue (Route 甲) -------------------------------
  // When a subagent pauses on ask_clarification, the lead ``task`` tool emits a
  // ``task_interrupted`` custom event whose interrupts[].value is a
  // ClarificationInterruptRequest. Unlike the lead's own clarification (which
  // resumes via Command(resume) on the thread), a subagent resumes via the
  // per-subagent endpoint POST /api/threads/{id}/subagents/{task_id}/resume.
  // The lead run stays open while the subagent is paused, so these forms are
  // presented serially: one at a time, head of queue first. ``taskId`` is the
  // dispatching ``task`` tool_call_id (the resume handle), distinct from the
  // subagent's ask_clarification tool_call_id carried inside the request.
  const [subagentClarificationQueue, setSubagentClarificationQueue] = useState<
    Array<{ request: ClarificationInterruptRequest; taskId: string }>
  >([]);
  // task_ids the user dismissed without answering, so a reconnect replaying
  // the same task_interrupted event does not re-open the form.
  const dismissedSubagentIdsRef = useRef<Set<string>>(new Set());

  const thread = useStream<AgentThreadState>({
    client: getAPIClient(isMock),
    assistantId: "lead_agent",
    threadId: onStreamThreadId,
    reconnectOnMount: true,
    fetchStateHistory: { limit: 1 },
    onCreated(meta) {
      handleStreamStart(meta.thread_id, meta.run_id);
      const now = new Date().toISOString();
      upsertThreadInSearchCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      upsertThreadInInfiniteCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      if (context.agent_name && !isMock) {
        void getAPIClient()
          .threads.update(meta.thread_id, {
            metadata: { agent_name: context.agent_name },
          })
          .catch(() => ({}));
      }
    },
    onLangChainEvent(event) {
      if (event.event === "on_tool_end") {
        listeners.current.onToolEnd?.({
          name: event.name,
          data: event.data,
        });
      }
    },
    onUpdateEvent(data) {
      const _messages = getSummarizationMiddlewareMessages(data);
      if (_messages && _messages.length >= 2) {
        // Summarization rewrote the live list (RemoveMessage(ALL) + summary +
        // retained tail). The removed turns remain in the server-side event
        // store, so refetch the server-ordered history instead of rescuing
        // them into React state (supersedes the #3825 archive buffer).
        messagesRef.current = [];
        if (threadIdRef.current && !isMock) {
          void queryClient.invalidateQueries({
            queryKey: threadMessagesQueryKey(threadIdRef.current),
          });
        }
      }

      const updates: Array<Partial<AgentThreadState> | null> = Object.values(
        data || {},
      );
      for (const update of updates) {
        if (update && "title" in update && update.title) {
          void queryClient.setQueriesData(
            {
              queryKey: ["threads", "search"],
              exact: false,
            },
            (oldData: Array<AgentThread> | undefined) => {
              return oldData?.map((t) => {
                if (t.thread_id === threadIdRef.current) {
                  return {
                    ...t,
                    values: {
                      ...t.values,
                      title: update.title,
                    },
                  };
                }
                return t;
              });
            },
          );
          const nextTitle: string = update.title;
          void queryClient.setQueriesData(
            {
              queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
              exact: false,
            },
            (oldData: InfiniteData<AgentThread[]> | undefined) =>
              mapInfiniteThreadsCache(
                oldData,
                (t): AgentThread =>
                  t.thread_id === threadIdRef.current
                    ? {
                        ...t,
                        values: {
                          ...t.values,
                          title: nextTitle,
                        },
                      }
                    : t,
              ),
          );
        }
      }
    },
    onCustomEvent(event: unknown) {
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "task_running"
      ) {
        const e = event as {
          type: "task_running";
          task_id: string;
          message: AIMessage;
        };
        updateSubtask({ id: e.task_id, latestMessage: e.message });
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "task_interrupted" &&
        "task_id" in event &&
        typeof (event as { task_id: unknown }).task_id === "string" &&
        "interrupts" in event &&
        Array.isArray((event as { interrupts: unknown }).interrupts)
      ) {
        const e = event as {
          type: "task_interrupted";
          task_id: string;
          interrupts: Array<{ value: unknown; id: string }>;
        };
        // A subagent paused on ask_clarification: its interrupt value is a
        // ClarificationInterruptRequest. Latch it as a serial form. Ignore
        // non-clarification interrupts (handled elsewhere) and any task_id the
        // user already dismissed, so a reconnect replay does not re-open it.
        if (dismissedSubagentIdsRef.current.has(e.task_id)) return;
        for (const intr of e.interrupts) {
          if (isClarificationInterrupt(intr.value)) {
            // Capture the narrowed value into a const so the type guard survives
            // the setState closure (property-access narrowing does not).
            const request = intr.value;
            setSubagentClarificationQueue((q) => {
              if (q.some((item) => item.taskId === e.task_id)) return q;
              return [...q, { request, taskId: e.task_id }];
            });
            break;
          }
        }
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "llm_retry" &&
        "message" in event &&
        typeof event.message === "string" &&
        event.message.trim()
      ) {
        const e = event as { type: "llm_retry"; message: string };
        toast(e.message);
      }
    },
    onError(error) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
      setLiveMessagesThreadId(null);
      setPendingSupersededRunIds(new Set());
      setPendingSupersededMessageIds(new Set());
      toast.error(getStreamErrorMessage(error));
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      if (threadIdRef.current && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadIdRef.current),
        });
        void queryClient.invalidateQueries({
          queryKey: threadSystemPromptQueryKey(threadIdRef.current),
        });
      }
    },
    onFinish(state) {
      listeners.current.onFinish?.(state.values);
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      // Do NOT clear the clarification latch here. The SDK fires onFinish
      // whenever the SSE stream closes — including when the run PAUSES on a
      // clarification interrupt. `state` is the refetched history head
      // (submit's onSuccess awaits history.mutate then calls onFinish), whose
      // `.values` omits `__interrupt__` (interrupts live in tasks[].interrupts,
      // not channel_values — see the SDK `interrupt` getter fallback). So a
      // pause is indistinguishable from a terminal state here, and clearing
      // would dismiss the form the instant it appears ("一闪而过"). After
      // onFinish the SDK also calls setStreamValues(null) (submit's onSuccess
      // returns null), which makes thread.values fall back to the history
      // snapshot — also without `__interrupt__`. The latch effect below
      // early-returns on empty `rawInterrupt`, so it correctly PRESERVES the
      // latch across both transitions; a stale latch is cleared by that
      // effect's isLoading-gated else branch, never here.
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
      if (threadIdRef.current && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: ["thread", threadIdRef.current],
        });
        void queryClient.invalidateQueries({
          queryKey: threadMessagesQueryKey(threadIdRef.current),
        });
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadIdRef.current),
        });
        void queryClient.invalidateQueries({
          queryKey: threadSystemPromptQueryKey(threadIdRef.current),
        });
      }
    },
  });

  const hasVisibleStreamState =
    Boolean(threadId) || liveMessagesThreadId === currentViewThreadId;
  const persistedMessages = useMemo(
    () =>
      hasVisibleStreamState
        ? thread.messages.filter(
            (message) =>
              !message.id || !pendingSupersededMessageIds.has(message.id),
          )
        : [],
    [hasVisibleStreamState, pendingSupersededMessageIds, thread.messages],
  );
  const visibleHistory = useMemo(
    () => (threadId ? history : []),
    [history, threadId],
  );
  const humanMessageCount = persistedMessages.filter(
    (m) => m.type === "human",
  ).length;
  const latestMessageCountsRef = useRef({ humanMessageCount });
  const sendInFlightRef = useRef(false);
  const messagesRef = useRef<Message[]>([]);
  // Track human message count before sending to prevent clearing optimistic
  // messages before the server's human message arrives (e.g. when AI messages
  // from "messages-tuple" events arrive before the input human message from
  // "values" events).
  const prevHumanMsgCountRef = useRef(humanMessageCount);

  latestMessageCountsRef.current = { humanMessageCount };

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    sendInFlightRef.current = false;
    messagesRef.current = [];
    pendingUsageBaselineMessageIdsRef.current = new Set();
    setPendingSupersededRunIds(new Set());
    setPendingSupersededMessageIds(new Set());
    prevHumanMsgCountRef.current =
      latestMessageCountsRef.current.humanMessageCount;
  }, [threadId]);

  useEffect(() => {
    if (optimisticThreadId && optimisticThreadId !== currentViewThreadId) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
    }
    if (liveMessagesThreadId && liveMessagesThreadId !== currentViewThreadId) {
      setLiveMessagesThreadId(null);
    }
  }, [currentViewThreadId, liveMessagesThreadId, optimisticThreadId]);

  // When streaming starts without a baseline (e.g. reconnection, run started
  // from another client, or page reload mid-stream), snapshot the current
  // messages so only *new* messages are treated as "pending" for token usage.
  useEffect(() => {
    if (
      thread.isLoading &&
      pendingUsageBaselineMessageIdsRef.current.size === 0
    ) {
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
    }
  }, [persistedMessages, thread.isLoading]);

  // Clear optimistic when server messages arrive.
  // For messages with a human optimistic message, wait until the server's
  // human message has arrived to avoid clearing before the input message
  // appears in the stream (the input message may arrive via "values" events
  // after individual "messages-tuple" events for AI messages).
  const optimisticMessageCount = optimisticMessages.length;
  const hasHumanOptimistic = optimisticMessages.some((m) => m.type === "human");
  useEffect(() => {
    if (optimisticMessageCount === 0) return;

    const newHumanMsgArrived = humanMessageCount > prevHumanMsgCountRef.current;

    if (!hasHumanOptimistic || newHumanMsgArrived) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
    }
  }, [hasHumanOptimistic, humanMessageCount, optimisticMessageCount]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;

      // A new user turn abandons any pending clarification; the latch is
      // SET-only w.r.t. `__interrupt__` (won't self-clear from stream
      // lifecycle — see the latch effect below), so clear it here so a stale
      // inline form doesn't linger over the new run.
      setClarificationInterruptValue(null);
      setClarificationDismissed(false);
      submittedInterruptIdRef.current = null;

      const text = message.text.trim();

      // Capture the current human message count before showing optimistic
      // messages so we can wait for the server's copy of the user input.
      prevHumanMsgCountRef.current = humanMessageCount;
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );

      // Build optimistic files list with uploading status
      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
      const optimisticAdditionalKwargs = {
        ...options?.additionalKwargs,
        ...(optimisticFiles.length > 0 ? { files: optimisticFiles } : {}),
      };

      const newOptimistic: Message[] = [];
      if (!hideFromUI) {
        newOptimistic.push({
          type: "human",
          id: `opt-human-${Date.now()}`,
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: optimisticAdditionalKwargs,
        });
      }

      if (optimisticFiles.length > 0 && !hideFromUI) {
        // Mock AI message while files are being uploaded
        newOptimistic.push({
          type: "ai",
          id: `opt-ai-${Date.now()}`,
          content: t.uploads.uploadingFiles,
          additional_kwargs: { element: "task" },
        });
      }
      setOptimisticThreadId(threadId);
      setLiveMessagesThreadId(threadId);
      setOptimisticMessages(newOptimistic);

      listeners.current.onSend?.(threadId);

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart),
            );

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFiles(threadId, files);
              uploadedFileInfo = uploadResponse.files;

              // Update optimistic human message with uploaded status + paths
              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                if (messages.length > 1 && messages[0]) {
                  const humanMessage: Message = messages[0];
                  return [
                    {
                      ...humanMessage,
                      additional_kwargs: { files: uploadedFiles },
                    },
                    ...messages.slice(1),
                  ];
                }
                return messages;
              });
            }
          } catch (error) {
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            setOptimisticThreadId(null);
            setLiveMessagesThreadId(null);
            throw error;
          } finally {
            setIsUploading(false);
          }
        }

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs: {
                  ...options?.additionalKwargs,
                  ...(filesForSubmit.length > 0
                    ? { files: filesForSubmit }
                    : {}),
                },
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            config: {
              recursion_limit: 1000,
            },
            context: (() => {
              // Derive runtime flags from the selected effort's preset (config-
              // driven). A user-selected reasoning_effort still wins over the
              // preset default.
              const flags = resolveEffortFlags(
                context.effort ?? "pro",
                efforts,
              );
              return {
                ...extraContext,
                ...context,
                thinking_enabled: flags.thinking_enabled,
                is_plan_mode: flags.is_plan_mode,
                subagent_enabled: flags.subagent_enabled,
                reasoning_effort:
                  context.reasoning_effort ?? flags.reasoning_effort,
                // Enable structured clarification interrupts (single/multi/text form)
                // on the web. IM channels don't set this and fall back to goto=END.
                clarification_interrupt_enabled: true,
                thread_id: threadId,
              };
            })(),
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
      } catch (error) {
        setOptimisticMessages([]);
        setOptimisticThreadId(null);
        setLiveMessagesThreadId(null);
        setIsUploading(false);
        throw error;
      } finally {
        sendInFlightRef.current = false;
      }
    },
    [
      thread,
      t.uploads.uploadingFiles,
      context,
      queryClient,
      humanMessageCount,
      persistedMessages,
      efforts,
    ],
  );

  const regenerateMessage = useCallback(
    async (
      threadId: string,
      messageId: string,
      supersededMessageIds: string[] = [messageId],
    ) => {
      if (sendInFlightRef.current || !threadId || !messageId) {
        return;
      }
      sendInFlightRef.current = true;
      // Regenerating abandons any pending clarification, like sendMessage.
      setClarificationInterruptValue(null);
      setClarificationDismissed(false);
      submittedInterruptIdRef.current = null;
      prevHumanMsgCountRef.current = humanMessageCount;
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      setLiveMessagesThreadId(threadId);
      listeners.current.onSend?.(threadId);
      let preparedSupersededRunId: string | null = null;
      let preparedSupersededMessageIds: string[] = [];

      try {
        const response = await fetch(
          `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
            threadId,
          )}/runs/regenerate/prepare`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            credentials: "include",
            body: JSON.stringify({ message_id: messageId }),
          },
        );
        if (!response.ok) {
          throw new Error(await readResponseErrorMessage(response));
        }
        const prepared = (await response.json()) as RegeneratePrepareResponse;
        preparedSupersededRunId = prepared.target_run_id;
        preparedSupersededMessageIds = supersededMessageIds;
        setPendingSupersededRunIds((current) => {
          const next = new Set(current);
          next.add(prepared.target_run_id);
          return next;
        });
        setPendingSupersededMessageIds((current) => {
          const next = new Set(current);
          for (const id of supersededMessageIds) {
            next.add(id);
          }
          return next;
        });

        await thread.submit(prepared.input, {
          threadId,
          checkpoint: prepared.checkpoint,
          metadata: prepared.metadata,
          streamSubgraphs: true,
          streamResumable: true,
          config: {
            recursion_limit: 1000,
          },
          context: (() => {
            const flags = resolveEffortFlags(context.effort ?? "pro", efforts);
            return {
              ...context,
              thinking_enabled: flags.thinking_enabled,
              is_plan_mode: flags.is_plan_mode,
              subagent_enabled: flags.subagent_enabled,
              reasoning_effort:
                context.reasoning_effort ?? flags.reasoning_effort,
              thread_id: threadId,
            };
          })(),
        });
        void queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadId),
        });
        void queryClient.invalidateQueries({
          queryKey: threadSystemPromptQueryKey(threadId),
        });
      } catch (error) {
        setLiveMessagesThreadId(null);
        if (preparedSupersededRunId) {
          const supersededRunId = preparedSupersededRunId;
          setPendingSupersededRunIds((current) =>
            removeSetItems(current, [supersededRunId]),
          );
          setPendingSupersededMessageIds((current) =>
            removeSetItems(current, preparedSupersededMessageIds),
          );
        }
        toast.error(getStreamErrorMessage(error));
      } finally {
        sendInFlightRef.current = false;
      }
    },
    [
      context,
      humanMessageCount,
      persistedMessages,
      queryClient,
      thread,
      efforts,
    ],
  );

  // Cache the latest thread messages in a ref to compare against incoming history messages for deduplication,
  // and to allow access to the full message list in onUpdateEvent without causing re-renders.
  if (persistedMessages.length >= messagesRef.current.length) {
    messagesRef.current = persistedMessages;
  }

  const visibleOptimisticMessages = getVisibleOptimisticMessages(
    optimisticThreadId === currentViewThreadId ? optimisticMessages : [],
    prevHumanMsgCountRef.current,
    humanMessageCount,
  );

  const mergedMessages = mergeMessages(
    visibleHistory,
    persistedMessages,
    visibleOptimisticMessages,
  );
  const pendingUsageMessages = thread.isLoading
    ? getMessagesAfterBaseline(
        persistedMessages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    values: hasVisibleStreamState ? thread.values : EMPTY_THREAD_VALUES,
    messages: mergedMessages,
  } as typeof thread;

  // Stabilize the active clarification interrupt in local state.
  // The SDK's `thread.interrupt` getter is undefined until `reconnectOnMount`
  // re-streams `__interrupt__` (history snapshots don't carry it). Latch from
  // `thread.values.__interrupt__`. This effect is SET-only: it never clears
  // the latch when `rawInterrupt` is empty. The SDK drops `__interrupt__` from
  // `thread.values` at two points during a paused run — (a) a later `values`
  // chunk without it REPLACES stream.values (manager.js
  // `else this.setStreamValues(data)`), and (b) after the stream ends
  // `onSuccess` returns null → `setStreamValues(null)` → `thread.values`
  // falls back to the history snapshot (no `__interrupt__`). In both cases
  // `rawInterrupt` empties while the run is still paused, so clearing here
  // would dismiss the form the instant it appears ("一闪而过"). The latch is
  // cleared explicitly on user action instead: resume (`resumeClarification`),
  // dismiss, a new/regenerate submit (`sendMessage` / `regenerateMessage`),
  // and thread switch (the effect below). We do NOT clear on run finish: see
  // `onFinish` above for why a pause is indistinguishable from a terminal
  // state there.
  const rawInterrupt = (thread.values as { __interrupt__?: unknown })
    .__interrupt__;
  useEffect(() => {
    if (!Array.isArray(rawInterrupt) || rawInterrupt.length === 0) return;
    const last = rawInterrupt[rawInterrupt.length - 1] as
      | { value?: unknown }
      | undefined;
    const value = last?.value;
    if (
      value &&
      typeof value === "object" &&
      (value as { type?: string }).type === "clarification_request"
    ) {
      const req = value as ClarificationInterruptRequest;
      // Don't re-open the modal for an interrupt we already answered — the
      // resumed run may replay the paused checkpoint before producing its
      // first value chunk.
      if (req.tool_call_id === submittedInterruptIdRef.current) return;
      setClarificationInterruptValue(req);
      setClarificationDismissed(false);
    }
  }, [rawInterrupt]);

  // Clear latch + dismissed when switching threads so no modal leaks across
  // threads. The server-side paused run is NOT cancelled (on_disconnect:
  // "continue"); switching back reconnects via joinStream and re-latches.
  useEffect(() => {
    setClarificationInterruptValue(null);
    setClarificationDismissed(false);
    submittedInterruptIdRef.current = null;
    setSubagentClarificationQueue([]);
    dismissedSubagentIdsRef.current = new Set();
  }, [threadId]);

  const clarificationInterrupt =
    clarificationInterruptValue && !clarificationDismissed
      ? clarificationInterruptValue
      : null;

  // User dismissed the modal without answering (ESC / overlay / cancel). Keep
  // the run paused and resumable — only hide locally. A reconnect that
  // re-streams the same interrupt is ignored because tool_call_id matches
  // nothing here; to re-show after dismiss the user must switch away and back
  // (threadId effect clears dismissed).
  const dismissClarification = useCallback(() => {
    setClarificationDismissed(true);
  }, []);

  // Resume a paused clarification interrupt with the user's answer. Sends
  // Command(resume=answer) on the same thread so the agent continues the turn.
  const resumeClarification = useCallback(
    async (answer: string) => {
      if (!threadId) return;
      const interruptId = clarificationInterruptValue?.tool_call_id ?? null;
      // Mark this interrupt as answered so a replayed stream chunk for the
      // same paused checkpoint doesn't re-open the modal.
      submittedInterruptIdRef.current = interruptId;
      // Clear the latch immediately so the modal closes on submit rather than
      // waiting for the resumed run's first value chunk.
      setClarificationInterruptValue(null);
      setClarificationDismissed(false);
      await thread.submit(null, {
        threadId: threadId,
        streamSubgraphs: true,
        streamResumable: true,
        config: {
          recursion_limit: 1000,
        },
        context: {
          clarification_interrupt_enabled: true,
          thread_id: threadId,
        },
        command: {
          resume: answer,
        },
      });
    },
    [thread, threadId, clarificationInterruptValue],
  );

  // The head of the subagent clarification queue — the one form shown next
  // (serial presentation). null when the queue is empty.
  const subagentClarification = subagentClarificationQueue[0] ?? null;

  // Submit an answer to the active subagent clarification. Unlike the lead's
  // resumeClarification (Command(resume) on the thread), a subagent resumes via
  // the per-subagent endpoint. The lead run is still open and the ``task`` tool
  // will return the result once the subagent completes, so the lead model
  // continues in the same turn — we only need to kick the subagent here. The
  // form closes optimistically on submit (matching the lead behaviour); if the
  // subagent pauses again with the same task_id, task_interrupted re-enqueues.
  const resumeSubagentClarification = useCallback(
    async (taskId: string, answer: string) => {
      if (!threadId) return;
      setSubagentClarificationQueue((q) =>
        q.filter((item) => item.taskId !== taskId),
      );
      await resumeSubagent(threadId, taskId, answer);
    },
    [threadId],
  );

  // Hide the active subagent clarification without answering; the subagent
  // stays paused and resumable server-side. Record dismissal so a reconnect
  // replaying the same task_interrupted does not re-open it.
  const dismissSubagentClarification = useCallback((taskId: string) => {
    dismissedSubagentIdsRef.current.add(taskId);
    setSubagentClarificationQueue((q) =>
      q.filter((item) => item.taskId !== taskId),
    );
  }, []);

  return {
    thread: mergedThread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    resumeClarification,
    dismissClarification,
    clarificationInterrupt,
    subagentClarification,
    resumeSubagentClarification,
    dismissSubagentClarification,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } as const;
}

type ThreadHistoryOptions = {
  enabled?: boolean;
  pendingSupersededRunIds?: ReadonlySet<string>;
};

export function useThreadHistory(
  threadId: string,
  { enabled = true, pendingSupersededRunIds }: ThreadHistoryOptions = {},
) {
  const runs = useThreadRuns(threadId, { enabled });
  const query = useInfiniteQuery({
    queryKey: threadMessagesQueryKey(threadId),
    queryFn: async ({ pageParam }) => {
      const url = buildThreadMessagesUrl(
        getBackendBaseURL(),
        threadId,
        pageParam,
      );
      const result: ThreadMessagesPage = await fetch(url, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      }).then((res) => {
        if (!res.ok) {
          throw new Error(`Failed to load thread messages: ${res.status}`);
        }
        return res.json();
      });
      return result;
    },
    initialPageParam: undefined as number | undefined,
    getPreviousPageParam: getThreadMessagesPreviousPageParam,
    // History only paginates backwards (older pages); there is never a newer
    // page to fetch, but TanStack Query requires the option.
    getNextPageParam: () => undefined,
    enabled: enabled && Boolean(threadId),
    refetchOnWindowFocus: false,
  });

  const supersededRunIds = useMemo(() => {
    return getSupersededRunIds(runs.data, pendingSupersededRunIds);
  }, [pendingSupersededRunIds, runs.data]);

  const messages = useMemo(() => {
    const rows = (query.data?.pages ?? []).flatMap((page) =>
      page.data.filter((m) => !m.metadata?.caller?.startsWith("middleware:")),
    );
    return buildVisibleHistoryMessages(rows, supersededRunIds);
  }, [query.data, supersededRunIds]);

  // Surface load failures to the user (the per-run loader used to toast on
  // failure). `isError` is stable across re-renders, so the effect only fires
  // when the error state actually transitions.
  useEffect(() => {
    if (query.isError) {
      toast.error("Failed to load thread history.");
    }
  }, [query.isError]);

  const hasThreadId = Boolean(threadId);
  const isRunsLoading =
    enabled &&
    hasThreadId &&
    (runs.isLoading || (runs.isFetching && !runs.data));
  const isRunsUnresolved =
    enabled && hasThreadId && !runs.data && !runs.isError;

  return {
    runs: runs.data,
    messages,
    loading:
      query.isLoading ||
      query.isFetchingPreviousPage ||
      isRunsLoading ||
      isRunsUnresolved,
    hasMore: enabled && hasThreadId && Boolean(query.hasPreviousPage),
    loadMore: () => {
      void query.fetchPreviousPage();
    },
  };
}

export function useThreads(
  params: ThreadSearchParams = DEFAULT_THREAD_SEARCH_PARAMS,
) {
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    ...buildThreadsSearchQueryOptions(apiClient, params),
  });
}

export const INFINITE_THREADS_PAGE_SIZE = 50;

export const INFINITE_THREADS_QUERY_KEY_PREFIX = [
  "threads",
  "searchInfinite",
] as const;

type InfiniteThreadsParams = Omit<
  Parameters<ThreadsClient["search"]>[0],
  "limit" | "offset"
>;

export function getInfiniteThreadsNextPageParam(
  lastPage: AgentThread[],
  allPages: AgentThread[][],
  pageSize: number = INFINITE_THREADS_PAGE_SIZE,
): number | undefined {
  if (lastPage.length < pageSize) {
    return undefined;
  }
  return allPages.reduce((sum, page) => sum + page.length, 0);
}

export function mapInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  mapper: (thread: AgentThread) => AgentThread,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.map(mapper)),
  };
}

export function filterInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  predicate: (thread: AgentThread) => boolean,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.filter(predicate)),
  };
}

export function useInfiniteThreads(
  params: InfiniteThreadsParams = {
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
  },
) {
  const apiClient = getAPIClient();
  return useInfiniteQuery<
    AgentThread[],
    Error,
    InfiniteData<AgentThread[]>,
    readonly unknown[],
    number
  >({
    queryKey: [...INFINITE_THREADS_QUERY_KEY_PREFIX, params],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = (await apiClient.threads.search<AgentThreadState>({
        ...params,
        limit: INFINITE_THREADS_PAGE_SIZE,
        offset: pageParam,
      })) as AgentThread[];
      return response;
    },
    getNextPageParam: (lastPage, allPages) =>
      getInfiniteThreadsNextPageParam(lastPage, allPages),
    refetchOnWindowFocus: false,
  });
}

export function useThreadRuns(
  threadId?: string,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const apiClient = getAPIClient();
  return useQuery<Run[]>({
    queryKey: ["thread", threadId],
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const response = await apiClient.runs.list(threadId);
      return response;
    },
    enabled: enabled && Boolean(threadId),
    refetchOnWindowFocus: false,
  });
}

export function useThreadMetadata(
  threadId?: string | null,
  {
    enabled = true,
    isMock = false,
  }: { enabled?: boolean; isMock?: boolean } = {},
) {
  const apiClient = getAPIClient(isMock);
  return useQuery<AgentThread | null>({
    queryKey: ["thread", "metadata", threadId, isMock],
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      try {
        const response = await apiClient.threads.get(threadId);
        return response as AgentThread;
      } catch (error) {
        if (isThreadMissingError(error)) {
          return null;
        }
        throw error;
      }
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useThreadTokenUsage(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadTokenUsageResponse | null>({
    queryKey: threadTokenUsageQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadTokenUsage(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useThreadSystemPrompt(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadSystemPromptResponse | null>({
    queryKey: threadSystemPromptQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadSystemPrompt(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useRunDetail(threadId: string, runId: string) {
  const apiClient = getAPIClient();
  return useQuery<Run>({
    queryKey: ["thread", threadId, "run", runId],
    queryFn: async () => {
      const response = await apiClient.runs.get(threadId, runId);
      return response;
    },
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      onRemoteDeleted,
    }: {
      threadId: string;
      onRemoteDeleted?: () => void;
    }) => {
      await apiClient.threads.delete(threadId);
      onRemoteDeleted?.();

      const response = await fetch(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
        {
          method: "DELETE",
        },
      );

      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Failed to delete local thread data." }));
        throw new Error(error.detail ?? "Failed to delete local thread data.");
      }
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          filterInfiniteThreadsCache(oldData, (t) => t.thread_id !== threadId),
      );
    },

    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
    },
  });
}

export function useBulkDeleteThreads() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({ threadIds }: { threadIds: string[] }) => {
      const results = await Promise.allSettled(
        threadIds.map(async (threadId) => {
          await apiClient.threads.delete(threadId);
          const response = await fetch(
            `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
            { method: "DELETE" },
          );
          if (!response.ok) {
            const error = await response
              .json()
              .catch(() => ({ detail: "Failed to delete local thread data." }));
            throw new Error(
              error.detail ?? "Failed to delete local thread data.",
            );
          }
          return threadId;
        }),
      );

      const succeeded = results
        .filter(
          (r): r is PromiseFulfilledResult<string> => r.status === "fulfilled",
        )
        .map((r) => r.value);
      const failed = results.filter(
        (r): r is PromiseRejectedResult => r.status === "rejected",
      );

      return { succeeded, failed, total: threadIds.length };
    },
    onSuccess({ succeeded }) {
      const deletedSet = new Set(succeeded);
      queryClient.setQueriesData(
        { queryKey: ["threads", "search"], exact: false },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => !deletedSet.has(t.thread_id));
        },
      );
      queryClient.setQueriesData(
        { queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX, exact: false },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          filterInfiniteThreadsCache(
            oldData,
            (t) => !deletedSet.has(t.thread_id),
          ),
      );
    },
    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          mapInfiniteThreadsCache(oldData, (t) =>
            t.thread_id === threadId
              ? {
                  ...t,
                  values: {
                    ...t.values,
                    title,
                  },
                }
              : t,
          ),
      );
    },
  });
}
