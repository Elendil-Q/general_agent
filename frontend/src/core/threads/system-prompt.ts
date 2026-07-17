import type { ThreadSystemPromptResponse } from "./types";

export function threadSystemPromptQueryKey(threadId?: string | null) {
  return ["thread-system-prompt", threadId] as const;
}

export type { ThreadSystemPromptResponse };
