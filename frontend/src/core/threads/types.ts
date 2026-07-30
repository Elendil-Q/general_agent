import type { Message, Thread } from "@langchain/langgraph-sdk";

import type { Todo } from "../todos";

/**
 * One entry of the backend ``ThreadState.subagents`` channel — the persisted
 * mirror of the in-memory subagent registry, written by
 * ``SubagentContextMiddleware.abefore_model`` on every lead model call.
 * ``idle_expires_at`` / ``updated_at`` are epoch seconds.
 */
export interface SubagentMirrorEntry {
  task_id: string;
  subagent_type: string;
  status:
    | "pending"
    | "running"
    | "idle"
    | "interrupted"
    | "completed"
    | "failed"
    | "cancelled";
  idle_expires_at?: number | null;
  updated_at: number;
}

export interface AgentThreadState extends Record<string, unknown> {
  title: string;
  messages: Message[];
  artifacts?: string[];
  todos?: Todo[];
  subagents?: Record<string, SubagentMirrorEntry> | null;
}

export interface AgentThreadContext extends Record<string, unknown> {
  thread_id: string;
  model_name: string | undefined;
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
  agent_name?: string;
}

export interface AgentThread extends Thread<AgentThreadState> {
  context?: AgentThreadContext;
}

export interface RunMessage {
  run_id: string;
  seq?: number;
  content: Message;
  metadata: {
    caller: string;
    [key: string]: unknown;
  };
  created_at: string;
}

export interface ThreadTokenUsageResponse {
  thread_id: string;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_runs: number;
  by_model: Record<string, { tokens: number; runs: number }>;
  by_caller: {
    lead_agent: number;
    subagent: number;
    middleware: number;
  };
}

export interface ThreadSystemPromptResponse {
  thread_id: string;
  system_prompt: string | null;
  caller: string | null;
  model_name: string | null;
  captured_at: string | null;
  run_id: string | null;
  llm_call_index: number | null;
}
