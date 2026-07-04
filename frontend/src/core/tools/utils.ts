import type { ToolCall } from "@langchain/core/messages";
import type { AIMessage } from "@langchain/langgraph-sdk";

import type { Translations } from "../i18n";
import { hasToolCalls } from "../messages/utils";

export function explainLastToolCall(message: AIMessage, t: Translations) {
  if (hasToolCalls(message)) {
    const lastToolCall = message.tool_calls![message.tool_calls!.length - 1]!;
    return explainToolCall(lastToolCall, t);
  }
  return t.common.thinking;
}

// File-system tools no longer carry a `description` param; render them as
// `tool_name(primary_arg)` so the user sees what is being touched at a glance.
const FILE_PATH_TOOLS = [
  "read_file",
  "write_file",
  "str_replace",
  "ls",
] as const;
const FILE_PATTERN_TOOLS = ["glob", "grep"] as const;

export function explainToolCall(toolCall: ToolCall, t: Translations) {
  const name = toolCall.name;
  const args = (toolCall.args ?? {}) as Record<string, unknown>;
  if (name === "web_search" || name === "image_search") {
    return t.toolCalls.searchFor(args.query as string);
  } else if (name === "web_fetch") {
    return t.toolCalls.viewWebPage;
  } else if (name === "present_files") {
    return t.toolCalls.presentFiles;
  } else if (name === "write_todos") {
    return t.toolCalls.writeTodos;
  } else if (
    FILE_PATH_TOOLS.includes(name as (typeof FILE_PATH_TOOLS)[number])
  ) {
    return `${name}(${(args.path as string) ?? ""})`;
  } else if (
    FILE_PATTERN_TOOLS.includes(name as (typeof FILE_PATTERN_TOOLS)[number])
  ) {
    return `${name}(${(args.pattern as string) ?? ""})`;
  } else if (name === "task") {
    // Surface the delegated subagent type so the user can perceive the process.
    const subagentType = (args.subagent_type as string) ?? "";
    const description = (args.description as string) ?? "";
    return subagentType ? `[${subagentType}] ${description}` : description;
  } else if (args.description) {
    return args.description as string;
  } else {
    return t.toolCalls.useTool(name);
  }
}
