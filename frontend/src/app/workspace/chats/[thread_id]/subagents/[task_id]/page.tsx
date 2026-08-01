"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { SubagentMessageList } from "@/components/workspace/messages/subagent-message-list";
import { useI18n } from "@/core/i18n/hooks";
import { useSubtask } from "@/core/tasks/context";
import {
  isTerminalSubagentStatus,
  resolveSubagentView,
  shouldRefetchOnTerminalTransition,
  subagentMessagesQueryKey,
  TERMINAL_REFOLLOW_DELAY_MS,
  useSubagentMessages,
  type SubagentMessagesResponse,
} from "@/core/tasks/subagent-messages";
import { useThreadMetadata } from "@/core/threads/hooks";
import { useResolvedThreadId } from "@/core/threads/thread-id";

export default function SubagentConversationPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { thread_id: threadIdParam, task_id: taskId } = useParams<{
    thread_id: string;
    task_id: string;
  }>();
  // Route params can be stale ("new") after the chat page swaps the URL via
  // the native History API; the resolved id follows the real browser path.
  const threadId = useResolvedThreadId() ?? threadIdParam;

  const subtask = useSubtask(taskId);
  const query = useSubagentMessages(threadId, taskId);
  const threadMetadata = useThreadMetadata(threadId);

  const metadataTitle = threadMetadata.data?.values?.title ?? "";
  const threadTitle = metadataTitle === "" ? t.pages.untitled : metadataTitle;
  const dataDescription = query.data?.description ?? "";
  const description =
    dataDescription === "" ? (subtask?.description ?? "") : dataDescription;

  const isTerminal =
    isTerminalSubagentStatus(subtask?.status) ||
    isTerminalSubagentStatus(query.data?.status);

  const history = useMemo<Message[]>(
    () => (query.data?.messages ?? []).map((record) => record.content),
    [query.data],
  );

  // One-shot terminal transition: the persisted history is only complete once
  // the final messages have been flushed, so invalidate on the flip and
  // refetch. `pendingData` marks the snapshot that was current at the flip;
  // while it still matches `query.data` the refetch is in flight and the
  // live-merged view stays on screen (no flash back to mount-time history).
  const queryClient = useQueryClient();
  const wasTerminalRef = useRef(false);
  const refollowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pendingData, setPendingData] = useState<
    SubagentMessagesResponse | null | undefined
  >(undefined);
  useEffect(() => {
    const shouldRefetch = shouldRefetchOnTerminalTransition(
      wasTerminalRef.current,
      isTerminal,
    );
    wasTerminalRef.current = isTerminal;
    if (shouldRefetch) {
      setPendingData(query.data);
      void queryClient
        .invalidateQueries({
          queryKey: subagentMessagesQueryKey(threadId, taskId),
        })
        .then(() => {
          // The terminal refetch can land before the final message's
          // fire-and-forget store.put; schedule ONE delayed follow-up
          // invalidate so the last message is picked up without a remount.
          refollowTimerRef.current = setTimeout(() => {
            refollowTimerRef.current = null;
            void queryClient.invalidateQueries({
              queryKey: subagentMessagesQueryKey(threadId, taskId),
            });
          }, TERMINAL_REFOLLOW_DELAY_MS);
        });
    }
  }, [isTerminal, query.data, queryClient, threadId, taskId]);

  // Clean up the pending follow-up timer on unmount only — the effect above
  // intentionally does not clear it on re-run (the refetch landing changes
  // `query.data`, which would otherwise cancel the timer it just scheduled).
  useEffect(() => {
    return () => {
      if (refollowTimerRef.current != null) {
        clearTimeout(refollowTimerRef.current);
        refollowTimerRef.current = null;
      }
    };
  }, []);

  const messages = resolveSubagentView(
    history,
    subtask?.latestMessage,
    isTerminal,
    pendingData === query.data,
  );

  return (
    <div className="relative flex size-full min-h-0 flex-col">
      <header className="bg-background/80 absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 shadow-xs backdrop-blur sm:px-4">
        <SidebarTrigger className="md:hidden" />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t.common.back}
          onClick={() =>
            router.push(`/workspace/chats/${encodeURIComponent(threadId)}`)
          }
        >
          <ArrowLeftIcon />
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 text-sm font-medium">
          <span className="truncate">{threadTitle}</span>
          {description && (
            <>
              <span className="text-muted-foreground shrink-0">/</span>
              <span className="text-muted-foreground truncate">
                {description}
              </span>
            </>
          )}
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">
        {query.isLoading ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t.subagentConversation.loading}
          </div>
        ) : query.isError ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t.subagentConversation.loadFailed}
          </div>
        ) : query.data === null ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t.subagentConversation.notFound}
          </div>
        ) : messages.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            {t.subagentConversation.empty}
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-(--container-width-md) flex-col gap-8 px-3 pt-16 pb-10 sm:px-4">
            <SubagentMessageList messages={messages} threadId={threadId} />
          </div>
        )}
      </main>
    </div>
  );
}
