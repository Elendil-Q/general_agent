import type { Message } from "@langchain/langgraph-sdk";

import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  getMessageGroups,
  hasContent,
  hasPresentFiles,
} from "@/core/messages/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";

// Read-only message list for the subagent conversation view. Mirrors the main
// chat's grouping pipeline (tool messages folded into processing groups and
// rendered as Chain-of-Thought tool steps), but fully static: nothing here
// streams, so isLoading stays false everywhere.
export function SubagentMessageList({
  messages,
  threadId,
}: {
  messages: Message[];
  threadId: string;
}) {
  const groups = getMessageGroups(messages);
  return (
    <>
      {groups.map((group) => {
        if (group.type === "human" || group.type === "assistant") {
          return group.messages.map((message) => (
            <MessageListItem
              key={message.id}
              message={message}
              threadId={threadId}
            />
          ));
        }
        if (group.type === "assistant:clarification") {
          const message = group.messages[0];
          if (!message || !hasContent(message)) {
            return null;
          }
          return (
            <MarkdownContent
              key={group.id}
              content={extractContentFromMessage(message)}
              isLoading={false}
            />
          );
        }
        if (group.type === "assistant:present-files") {
          const files: string[] = [];
          for (const message of group.messages) {
            if (hasPresentFiles(message)) {
              files.push(...extractPresentFilesFromMessage(message));
            }
          }
          const first = group.messages[0];
          return (
            <div key={group.id} className="w-full">
              {first && hasContent(first) && (
                <MarkdownContent
                  content={extractContentFromMessage(first)}
                  isLoading={false}
                  className="mb-4"
                />
              )}
              <ArtifactFileList files={files} threadId={threadId} />
            </div>
          );
        }
        // assistant:processing, and assistant:subagent as a simplification —
        // nested SubtaskCards are out of scope for this read-only view, so a
        // subagent's own task tool calls render as plain tool steps.
        return (
          <MessageGroup
            key={group.id}
            messages={group.messages}
            isLoading={false}
          />
        );
      })}
    </>
  );
}
