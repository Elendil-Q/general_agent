import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { createContext, useContext } from "react";

import type { AgentThreadState } from "@/core/threads";
import type { ClarificationInterruptRequest } from "@/core/threads/clarification";

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  isMock?: boolean;
  /** Latched structured clarification interrupt (null when none / dismissed). */
  clarificationInterrupt?: ClarificationInterruptRequest | null;
  /** Submit an answer to the active clarification interrupt (Command(resume)). */
  resumeClarification?: (answer: string) => void | Promise<void>;
  /** Hide the clarification form without answering; run stays paused. */
  dismissClarification?: () => void;
  /**
   * Active subagent clarification (a subagent paused on ask_clarification
   * while the lead ``task`` tool stays open). Null when none / dismissed. The
   * lead run is not interrupted — this resumes via the per-subagent endpoint.
   */
  subagentClarification?: {
    request: ClarificationInterruptRequest;
    taskId: string;
  } | null;
  /** Submit an answer to the active subagent clarification (per-subagent resume). */
  resumeSubagentClarification?: (
    taskId: string,
    answer: string,
  ) => void | Promise<void>;
  /** Hide the active subagent clarification form; the subagent stays paused. */
  dismissSubagentClarification?: (taskId: string) => void;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);

export function useThread() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThread must be used within a ThreadContext");
  }
  return context;
}
