import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import { useQuery } from "@tanstack/react-query";

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface SubagentMessageRecord {
  thread_id: string;
  run_id: string | null;
  seq: number;
  event_type: string;
  category: string;
  content: Message;
  metadata: {
    task_id?: string;
    subagent_type?: string;
    description?: string;
  };
  created_at: string;
}

export interface SubagentMessagesResponse {
  task_id: string;
  thread_id: string;
  subagent_type: string;
  description: string;
  status: string | null;
  messages: SubagentMessageRecord[];
}

const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "expired",
  "cancelled",
]);

/**
 * Terminal check covering both live subtask statuses (``expired``) and
 * backend mirror statuses (``cancelled``).
 */
export function isTerminalSubagentStatus(
  status: string | null | undefined,
): boolean {
  return status != null && TERMINAL_STATUSES.has(status);
}

/**
 * Merge the live ``task_progress`` snapshot (the latest AIMessage carried on
 * the subtask store) into the persisted history from the endpoint. The
 * snapshot is keyed by message id: an id already present in history is
 * overwritten in place, a new id is appended. Id-less snapshots are dropped
 * (they cannot be deduped, so merging them would repeat on every progress
 * event), and terminal tasks freeze the history as-is. The input array is
 * never mutated; when nothing changes the same reference is returned.
 */
export function mergeSubagentLiveMessage(
  history: Message[],
  liveMessage: AIMessage | null | undefined,
  isTerminal: boolean,
): Message[] {
  if (isTerminal || liveMessage == null) {
    return history;
  }
  const liveId = liveMessage.id;
  if (liveId == null || liveId === "") {
    return history;
  }
  const index = history.findIndex((message) => message.id === liveId);
  if (index === -1) {
    return [...history, liveMessage];
  }
  const merged = [...history];
  merged[index] = liveMessage;
  return merged;
}

export function subagentMessagesQueryKey(threadId?: string, taskId?: string) {
  return ["subagent", "messages", threadId, taskId] as const;
}

/**
 * Delay before the one-shot follow-up invalidation after a terminal-status
 * refetch. The executor persists the final message via a fire-and-forget
 * store.put that can land just after the terminal-triggered refetch
 * completes; a single delayed second invalidate picks it up without a
 * remount.
 */
export const TERMINAL_REFOLLOW_DELAY_MS = 500;

/**
 * Whether a terminal-status flip should trigger a history refetch: exactly
 * once, on the non-terminal → terminal transition, so the fully persisted
 * history replaces the live view after ``task_completed``.
 */
export function shouldRefetchOnTerminalTransition(
  wasTerminal: boolean,
  isTerminal: boolean,
): boolean {
  return isTerminal && !wasTerminal;
}

/**
 * Resolve the messages the conversation view should display. While running
 * (and during the terminal-triggered refetch window) the live snapshot is
 * merged in so the in-flight message the user was seeing does not vanish the
 * moment the task completes; once the refetched history has landed
 * (``refetchPending === false``), the persisted history is shown verbatim —
 * terminal freeze semantics.
 */
export function resolveSubagentView(
  history: Message[],
  liveMessage: AIMessage | null | undefined,
  isTerminal: boolean,
  refetchPending: boolean,
): Message[] {
  if (!isTerminal || refetchPending) {
    return mergeSubagentLiveMessage(history, liveMessage, false);
  }
  return history;
}

export async function fetchSubagentMessages(
  threadId: string,
  taskId: string,
): Promise<SubagentMessagesResponse | null> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/subagents/${encodeURIComponent(taskId)}/messages`,
  );
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(
      `Failed to fetch subagent ${taskId} messages: ${res.status}`,
    );
  }
  return (await res.json()) as SubagentMessagesResponse;
}

export function useSubagentMessages(threadId?: string, taskId?: string) {
  return useQuery<SubagentMessagesResponse | null>({
    queryKey: subagentMessagesQueryKey(threadId, taskId),
    queryFn: () => {
      if (!threadId || !taskId) {
        return null;
      }
      return fetchSubagentMessages(threadId, taskId);
    },
    enabled: Boolean(threadId) && Boolean(taskId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}
