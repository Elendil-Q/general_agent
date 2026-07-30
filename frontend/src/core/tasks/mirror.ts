import type { SubagentMirrorEntry } from "@/core/threads/types";

import type { Subtask } from "./types";

/**
 * Pure helpers for the ``ThreadState.subagents`` mirror channel.
 *
 * The backend mirrors its in-memory subagent registry into graph state on
 * every lead model call, so the mirror survives restarts / cold opens where
 * SSE events and message archaeology cannot tell the true last-known status.
 * The mirror is the baseline; live SSE ``custom`` events remain the
 * real-time increments on top of it.
 */

export type SubagentMirror = Record<string, SubagentMirrorEntry>;

/**
 * Map a mirror status to the card {@link Subtask} vocabulary. ``pending`` /
 * ``running`` collapse to ``in_progress``; ``cancelled`` collapses to
 * ``failed`` (same as the existing structured-status mapping in
 * ``subtask-result.ts``).
 */
export function mapMirrorStatusToSubtask(
  status: SubagentMirrorEntry["status"],
): Subtask["status"] {
  switch (status) {
    case "pending":
    case "running":
      return "in_progress";
    case "idle":
      return "idle";
    case "interrupted":
      return "interrupted";
    case "completed":
      return "completed";
    case "failed":
    case "cancelled":
      return "failed";
  }
}

/**
 * Guard against out-of-order mirror snapshots: apply an entry only when it
 * is at least as new as the last mirror update already applied to the task.
 */
export function shouldApplyMirrorUpdate(
  existing: Subtask | undefined,
  entry: SubagentMirrorEntry,
): boolean {
  if (existing?.mirrorUpdatedAt == null) {
    return true;
  }
  return entry.updated_at >= existing.mirrorUpdatedAt;
}

/**
 * Whether the thread state carries the ``subagents`` channel at all. Older
 * threads (backend before the mirror shipped) have no channel; the legacy
 * cold-open folding in ``mapWaitForTasksStatus`` must stay for those.
 */
export function hasSubagentMirror(
  mirror: SubagentMirror | null | undefined,
): mirror is SubagentMirror {
  return mirror != null;
}

/**
 * Ids of IDLE tasks whose server-side TTL already expired by ``now``
 * (epoch milliseconds). Only tasks that are still shown as ``idle`` and
 * carry a known ``idleExpiresAt`` qualify.
 */
export function findExpiredIdleTasks(
  tasks: Record<string, Subtask>,
  now: number,
): string[] {
  return Object.values(tasks)
    .filter(
      (task) =>
        task.status === "idle" &&
        task.idleExpiresAt != null &&
        task.idleExpiresAt <= now,
    )
    .map((task) => task.id);
}

/**
 * Earliest future ``idleExpiresAt`` (epoch milliseconds) among IDLE tasks,
 * or ``null`` when nothing is scheduled to expire.
 */
export function findNextIdleExpiry(
  tasks: Record<string, Subtask>,
  now: number,
): number | null {
  let next: number | null = null;
  for (const task of Object.values(tasks)) {
    if (
      task.status !== "idle" ||
      task.idleExpiresAt == null ||
      task.idleExpiresAt <= now
    ) {
      continue;
    }
    if (next == null || task.idleExpiresAt < next) {
      next = task.idleExpiresAt;
    }
  }
  return next;
}
