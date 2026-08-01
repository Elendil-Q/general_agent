import type { AIMessage } from "@langchain/langgraph-sdk";

export interface Subtask {
  id: string;
  status:
    | "in_progress"
    | "completed"
    | "failed"
    | "idle"
    | "interrupted"
    | "expired";
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  prompt: string;
  result?: string;
  error?: string;
  detached?: boolean;
  ttlExpired?: boolean;
  /**
   * Epoch milliseconds at which an IDLE subagent's server-side TTL expires,
   * derived from the ``subagents`` state-channel mirror
   * (``idle_expires_at``). A local timer flips the task to
   * ``completed`` + ``ttlExpired`` when it fires — the backend
   * ``task_expired`` SSE event is unreliable once the lead run's stream has
   * closed.
   */
  idleExpiresAt?: number;
  /**
   * Set for nested subagents (spawned by another subagent); null/absent for
   * lead-thread tasks. Nested tasks are hidden from the activity panel.
   */
  parent_task_id?: string | null;
  /**
   * ``updated_at`` (epoch seconds) of the last applied mirror entry.
   * Out-of-order / older mirror snapshots are skipped so they cannot
   * regress a fresher status.
   */
  mirrorUpdatedAt?: number;
  // Set when a live SSE event for this task was received in the current
  // session. In-memory only: resets on page reload / thread switch, which is
  // what lets the wait_for_tasks JSON mapping distinguish "turn just ended in
  // a live session" (keep idle/interrupted as-is) from "cold-opened history"
  // (fold idle/interrupted to completed, see commit 8b7f6953).
  live?: boolean;
}
