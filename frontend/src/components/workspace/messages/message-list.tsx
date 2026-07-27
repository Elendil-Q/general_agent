import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, Loader2Icon, RefreshCcwIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import {
  Reasoning,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildTokenDebugSteps,
  type TokenUsageInlineMode,
} from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  findToolCallResult,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroups,
  getStreamingMessageLookup,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  isAssistantMessageGroupStreaming,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import type { Subtask } from "@/core/tasks";
import { useUpdateSubtask } from "@/core/tasks/context";
import {
  derivePendingSubtaskStatus,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { ClarificationInlineForm } from "../clarification-inline-form";
import { CopyButton } from "../copy-button";
import { StreamingIndicator } from "../streaming-indicator";
import { Tooltip } from "../tooltip";

import { useThread } from "./context";
import { FollowUpCard } from "./follow-up-card";
import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";
import { WaitForTasksCard } from "./wait-for-tasks-card";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasMore) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          throttledLoadMore();
        }
      },
      {
        rootMargin: "120px 0px 0px 0px",
      },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [hasMore, throttledLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div ref={sentinelRef} className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  tokenUsageInlineMode = "off",
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
  onRegenerateMessage,
  canRegenerate = false,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  tokenUsageInlineMode?: TokenUsageInlineMode;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
  onRegenerateMessage?: (
    messageId: string,
    supersededMessageIds: string[],
  ) => void | Promise<void>;
  canRegenerate?: boolean;
}) {
  const { t } = useI18n();
  const {
    clarificationInterrupt,
    resumeClarification,
    dismissClarification,
    subagentClarification,
    resumeSubagentClarification,
    dismissSubagentClarification,
  } = useThread();
  const [turnStartTime, setTurnStartTime] = useState<number | null>(null);
  const prevIsLoading = useRef(thread.isLoading);

  useEffect(() => {
    if (thread.isLoading && !prevIsLoading.current) {
      setTurnStartTime(Date.now());
    }
    prevIsLoading.current = thread.isLoading;
  }, [thread.isLoading]);
  const messages = thread.messages;
  const groupedMessages = getMessageGroups(messages);
  const [regeneratingMessageId, setRegeneratingMessageId] = useState<
    string | null
  >(null);
  const hasActiveAssistantText = useMemo(() => {
    let lastHumanIndex = -1;
    for (let i = groupedMessages.length - 1; i >= 0; i--) {
      if (groupedMessages[i]?.type === "human") {
        lastHumanIndex = i;
        break;
      }
    }
    if (lastHumanIndex === -1) return false;
    return groupedMessages
      .slice(lastHumanIndex)
      .some((g) => g.type === "assistant");
  }, [groupedMessages]);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();
  const lastGroupIndex = groupedMessages.length - 1;
  const turnUsageMessagesByGroupIndex =
    getAssistantTurnUsageMessages(groupedMessages);
  const tokenDebugSteps = useMemo(
    () => buildTokenDebugSteps(messages, t),
    [messages, t],
  );
  const streamingMessages = useMemo(
    () =>
      getStreamingMessageLookup(
        messages,
        thread.isLoading,
        thread.getMessagesMetadata,
      ),
    [messages, thread.getMessagesMetadata, thread.isLoading],
  );

  const latestAssistantGroupId = useMemo(() => {
    if (thread.isLoading) {
      return null;
    }
    for (let i = groupedMessages.length - 1; i >= 0; i -= 1) {
      const group = groupedMessages[i];
      if (group?.type === "assistant") {
        return group.id;
      }
    }
    return null;
  }, [groupedMessages, thread.isLoading]);

  const renderAssistantActions = useCallback(
    (
      messages: Message[],
      isStreaming: boolean,
      enableRegenerateForTurn: boolean,
    ) => {
      const clipboardData = getAssistantTurnCopyData(messages, { isStreaming });
      const regenerateTarget = [...messages]
        .reverse()
        .find((message) => message.type === "ai" && message.id);
      const supersededMessageIds = messages
        .filter((message) => message.type === "ai" && message.id)
        .map((message) => message.id)
        .filter((id): id is string => typeof id === "string");

      if (!clipboardData && !regenerateTarget) {
        return null;
      }

      return (
        <div className="mt-2 flex justify-start gap-1 opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100">
          {clipboardData && <CopyButton clipboardData={clipboardData} />}
          {enableRegenerateForTurn &&
            regenerateTarget?.id &&
            onRegenerateMessage && (
              <Tooltip content={t.common.regenerate}>
                <Button
                  aria-label={t.common.regenerate}
                  size="icon-sm"
                  type="button"
                  variant="ghost"
                  disabled={
                    !canRegenerate ||
                    regeneratingMessageId === regenerateTarget.id
                  }
                  onClick={() => {
                    const targetId = regenerateTarget.id;
                    if (!targetId) {
                      return;
                    }
                    setRegeneratingMessageId(targetId);
                    void Promise.resolve(
                      onRegenerateMessage?.(targetId, supersededMessageIds),
                    ).finally(() => {
                      setRegeneratingMessageId(null);
                    });
                  }}
                >
                  <RefreshCcwIcon
                    className={cn(
                      "size-3",
                      regeneratingMessageId === regenerateTarget.id &&
                        "animate-spin",
                    )}
                  />
                </Button>
              </Tooltip>
            )}
        </div>
      );
    },
    [
      canRegenerate,
      onRegenerateMessage,
      regeneratingMessageId,
      t.common.regenerate,
    ],
  );

  const renderTokenUsage = useCallback(
    ({
      messages,
      turnUsageMessages,
      inlineDebug = true,
      debugMessageIds,
    }: {
      messages: Message[];
      turnUsageMessages?: Message[] | null;
      inlineDebug?: boolean;
      debugMessageIds?: string[];
    }) => {
      if (tokenUsageInlineMode === "per_turn") {
        return (
          <MessageTokenUsageList
            enabled={true}
            isLoading={thread.isLoading}
            messages={turnUsageMessages ?? []}
          />
        );
      }

      if (tokenUsageInlineMode === "step_debug" && inlineDebug) {
        const messageIds = new Set(
          debugMessageIds ??
            messages
              .filter((message) => message.type === "ai")
              .map((message) => message.id)
              .filter((id): id is string => typeof id === "string"),
        );
        return (
          <MessageTokenUsageDebugList
            enabled={true}
            isLoading={thread.isLoading}
            steps={tokenDebugSteps.filter((step) =>
              messageIds.has(step.messageId),
            )}
          />
        );
      }

      return null;
    },
    [thread.isLoading, tokenDebugSteps, tokenUsageInlineMode],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {groupedMessages.map((group, groupIndex) => {
          const turnUsageMessages = turnUsageMessagesByGroupIndex[groupIndex];
          const groupIsLoading =
            thread.isLoading && groupIndex === lastGroupIndex;

          if (group.type === "human" || group.type === "assistant") {
            return (
              <div
                key={group.id}
                className={cn(
                  "w-full",
                  group.type === "assistant" && "group/assistant-turn",
                )}
              >
                {group.messages.map((msg) => {
                  return (
                    <MessageListItem
                      key={`${group.id}/${msg.id}`}
                      message={msg}
                      isLoading={
                        thread.isLoading &&
                        groupIndex === groupedMessages.length - 1
                      }
                      threadId={threadId}
                      showCopyButton={group.type !== "assistant"}
                      turnStartTime={
                        groupIndex === groupedMessages.length - 1
                          ? turnStartTime
                          : null
                      }
                    />
                  );
                })}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
                {group.type === "assistant" &&
                  renderAssistantActions(
                    group.messages,
                    isAssistantMessageGroupStreaming(
                      group.messages,
                      streamingMessages,
                    ),
                    group.id === latestAssistantGroupId,
                  )}
              </div>
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              return (
                <div key={group.id} className="w-full">
                  <MarkdownContent
                    content={extractContentFromMessage(message)}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                  />
                  {renderTokenUsage({
                    messages: group.messages,
                    turnUsageMessages,
                  })}
                </div>
              );
            }
            return null;
          } else if (group.type === "assistant:present-files") {
            const files: string[] = [];
            for (const message of group.messages) {
              if (hasPresentFiles(message)) {
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={group.id}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList files={files} threadId={threadId} />
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            const tasks = new Set<Subtask>();
            for (const message of group.messages) {
              if (message.type === "ai") {
                for (const toolCall of message.tool_calls ?? []) {
                  if (toolCall.name === "task") {
                    const taskId = toolCall.id;
                    if (!taskId) {
                      continue;
                    }
                    const status = derivePendingSubtaskStatus(
                      taskId,
                      group.messages,
                      groupIsLoading,
                    );
                    const task: Subtask = {
                      id: taskId,
                      subagent_type: toolCall.args.subagent_type,
                      description: toolCall.args.description,
                      prompt: toolCall.args.prompt,
                      status,
                      ...(status === "failed"
                        ? { error: t.subtasks.failed }
                        : {}),
                    };
                    updateSubtask(task);
                    tasks.add(task);
                  }
                }
              } else if (message.type === "tool") {
                const taskId = message.tool_call_id;
                if (taskId) {
                  const parsed = parseSubtaskResult(
                    extractTextFromMessage(message),
                    message.additional_kwargs,
                  );
                  updateSubtask({ id: taskId, ...parsed });
                }
              }
            }

            const results: React.ReactNode[] = [];
            const subagentDebugMessageIds: string[] = [];
            if (tasks.size > 0) {
              results.push(
                <div
                  key="subtask-count"
                  className="text-muted-foreground pt-2 text-sm font-normal"
                >
                  {t.subtasks.executing(tasks.size)}
                </div>,
              );
            }
            for (const message of group.messages.filter(
              (message) => message.type === "ai",
            )) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={"thinking-group-" + message.id}
                    messages={[message]}
                    isLoading={groupIsLoading}
                    tokenDebugSteps={tokenDebugSteps.filter(
                      (step) => step.messageId === message.id,
                    )}
                    showTokenDebugSummaries={
                      tokenUsageInlineMode === "step_debug"
                    }
                  />,
                );
              } else if (message.id) {
                subagentDebugMessageIds.push(message.id);
              }
              const taskIds = message.tool_calls?.flatMap((toolCall) =>
                toolCall.name === "task" && toolCall.id ? [toolCall.id] : [],
              );
              for (const taskId of taskIds ?? []) {
                results.push(
                  <SubtaskCard
                    key={"task-group-" + taskId}
                    taskId={taskId}
                    isLoading={groupIsLoading}
                  />,
                );
              }
              // Render wait_for_tasks tool calls
              const waitCalls =
                message.tool_calls?.filter(
                  (tc) => tc.name === "wait_for_tasks",
                ) ?? [];
              for (const waitCall of waitCalls) {
                if (!waitCall.id) continue;
                const toolResult = findToolCallResult(
                  waitCall.id,
                  group.messages,
                );
                let parsedResult:
                  | Record<
                      string,
                      { status: string; result?: unknown; error?: string }
                    >
                  | undefined;
                if (toolResult) {
                  try {
                    parsedResult = JSON.parse(toolResult) as Record<
                      string,
                      { status: string; result?: unknown; error?: string }
                    >;
                  } catch {
                    // not JSON, leave undefined
                  }
                }
                results.push(
                  <WaitForTasksCard
                    key={"wait-group-" + waitCall.id}
                    taskIds={
                      (waitCall.args.task_ids as string[] | undefined) ?? []
                    }
                    isLoading={groupIsLoading && !parsedResult}
                    result={parsedResult}
                  />,
                );
              }
              // Render follow_up tool calls
              const followUpCalls =
                message.tool_calls?.filter((tc) => tc.name === "follow_up") ??
                [];
              for (const followUpCall of followUpCalls) {
                if (!followUpCall.id) continue;
                const toolResult = findToolCallResult(
                  followUpCall.id,
                  group.messages,
                );
                let resultStatus: "completed" | "failed" | "in_progress" =
                  "in_progress";
                let resultText: string | undefined;
                if (toolResult) {
                  if (toolResult.startsWith("Follow-up completed")) {
                    resultStatus = "completed";
                    resultText = toolResult;
                  } else if (
                    toolResult.startsWith("Follow-up failed") ||
                    toolResult.startsWith("Error:")
                  ) {
                    resultStatus = "failed";
                    resultText = toolResult;
                  } else {
                    resultStatus = "completed";
                    resultText = toolResult;
                  }
                }
                results.push(
                  <FollowUpCard
                    key={"followup-group-" + followUpCall.id}
                    taskId={
                      (followUpCall.args.task_id as string | undefined) ?? ""
                    }
                    prompt={
                      (followUpCall.args.prompt as string | undefined) ?? ""
                    }
                    isLoading={groupIsLoading && !toolResult}
                    resultText={resultText}
                    resultStatus={toolResult ? resultStatus : undefined}
                  />,
                );
              }
            }
            return (
              <div
                key={"subtask-group-" + group.id}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  debugMessageIds: subagentDebugMessageIds,
                })}
              </div>
            );
          }
          return (
            <div key={"group-" + group.id} className="w-full">
              <MessageGroup
                messages={group.messages}
                isLoading={thread.isLoading}
                tokenDebugSteps={tokenDebugSteps.filter((step) =>
                  group.messages.some(
                    (message) => message.id === step.messageId,
                  ),
                )}
                showTokenDebugSummaries={tokenUsageInlineMode === "step_debug"}
              />
              {renderTokenUsage({
                messages: group.messages,
                turnUsageMessages,
                inlineDebug: false,
              })}
            </div>
          );
        })}
        {subagentClarification &&
          resumeSubagentClarification &&
          dismissSubagentClarification && (
            <div className="w-full">
              <ClarificationInlineForm
                interrupt={subagentClarification.request}
                onSubmit={(answer) => {
                  void resumeSubagentClarification(
                    subagentClarification.taskId,
                    answer,
                  );
                }}
                onDismiss={() => {
                  dismissSubagentClarification(subagentClarification.taskId);
                }}
                // The lead run is NOT paused here (Route 甲: it is blocked in
                // the task tool awaiting this answer), so thread.isLoading
                // stays true and must NOT gate the form — the user must be able
                // to answer. The form unmounts on submit (dequeued), which
                // already prevents double-submit.
                disabled={false}
              />
            </div>
          )}
        {clarificationInterrupt &&
          resumeClarification &&
          dismissClarification && (
            <div className="w-full">
              <ClarificationInlineForm
                interrupt={clarificationInterrupt}
                onSubmit={(answer) => {
                  void resumeClarification(answer);
                }}
                onDismiss={dismissClarification}
                disabled={thread.isLoading}
              />
            </div>
          )}
        {thread.isLoading && !hasActiveAssistantText && (
          <div className="w-full">
            <Reasoning isStreaming={true} startTimeProp={turnStartTime}>
              <ReasoningTrigger hasContent={false} />
            </Reasoning>
          </div>
        )}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
    </Conversation>
  );
}
