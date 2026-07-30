import type { Message } from "@langchain/langgraph-sdk";

import type { Subtask } from "./types";

export type SubtaskStatus = Subtask["status"];

export interface SubtaskResultUpdate {
  status: SubtaskStatus;
  result?: string;
  error?: string;
}

/**
 * Structured-status keys the backend stamps onto
 * ``ToolMessage.additional_kwargs`` for every ``task`` tool result.
 *
 * The values mirror the Python contract in
 * ``backend/packages/harness/deerflow/subagents/status_contract.py``
 * (``SUBAGENT_STATUS_KEY`` / ``SUBAGENT_ERROR_KEY``). The cross-language
 * fixture at ``contracts/subagent_status_contract.json`` pins both sides
 * to the same values.
 */
export const SUBAGENT_STATUS_KEY = "subagent_status";
export const SUBAGENT_ERROR_KEY = "subagent_error";

/**
 * Map from the backend ``subagent_status`` value to the frontend
 * {@link SubtaskStatus} enum. The frontend collapses ``cancelled`` /
 * ``timed_out`` / ``polling_timed_out`` into ``failed``; the richer backend
 * vocabulary still survives on ``error`` for tooling that wants the
 * detail. ``idle`` / ``interrupted`` map to their own non-terminal card
 * states.
 */
const STRUCTURED_STATUS_TO_SUBTASK: Record<string, SubtaskStatus> = {
  completed: "completed",
  failed: "failed",
  cancelled: "failed",
  timed_out: "failed",
  polling_timed_out: "failed",
  idle: "idle",
  interrupted: "interrupted",
};

/**
 * Prefix strings the backend `task` tool writes into its result `content`.
 *
 * These values are not user-facing copy — they are part of the
 * backend↔frontend contract defined in
 * `backend/packages/harness/deerflow/tools/builtins/task_tool.py` (returned
 * from the tool body) and in
 * `backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py`
 * (wrapper for tool exceptions). Any change here must be paired with the
 * matching backend change. Exported so a future structured-status migration
 * can reference the same values from one place.
 *
 * `task_tool.py` also emits three `Error:` strings for pre-execution failures
 * — unknown subagent type, host-bash disabled, and "task disappeared from
 * background tasks". They are handled by {@link ERROR_WRAPPER_PATTERN}
 * rather than dedicated prefixes because the wrapper already produces
 * exactly the right `terminal failed` shape.
 */
export const SUCCESS_PREFIX = "Task Succeeded. Result:";
export const FAILURE_PREFIX = "Task failed.";
export const TIMEOUT_PREFIX = "Task timed out";
export const CANCELLED_PREFIX = "Task cancelled by user.";
export const POLLING_TIMEOUT_PREFIX = "Task polling timed out";
export const ERROR_WRAPPER_PATTERN = /^Error\b/i;

/**
 * Map a `task` tool result to a {@link SubtaskStatus}.
 *
 * Bytedance/deer-flow issue #3146: prefers the structured
 * ``additional_kwargs.subagent_status`` field the backend now stamps via
 * ``ToolErrorHandlingMiddleware``. Falls back to the legacy prefix
 * matching for messages that pre-date the stamping commit (historical
 * threads, third-party clients, or any tool path that bypasses the
 * middleware). Both shapes converge on the same {@link SubtaskStatus}
 * vocabulary the card UI renders.
 *
 * When the structured field is present, the prefix parser is still run
 * so the success `result` body and the wrapped-error message can be
 * back-filled from `content`. Today the backend only stamps the
 * `subagent_status` enum value — the human-facing payload still lives
 * in `content`, so dropping the prefix parse would regress the subtask
 * card display. Structured fields win on conflict: if `subagent_status`
 * and the text disagree, the text-derived `result`/`error` are
 * discarded so a malformed wrapper can't sneak through.
 *
 * Returning `in_progress` is the **deliberate** fallback for content that
 * matches none of the known prefixes and carries no structured stamp.
 * LangChain only ever emits a `ToolMessage` once the tool itself has
 * returned (success or wrapped exception), so an unknown shape means
 * "the contract changed underneath us" — surfacing it as still-running
 * prompts the operator to investigate, where eagerly marking it
 * terminal-failed would mask the drift.
 */
export function parseSubtaskResult(
  text: string,
  additionalKwargs?: Record<string, unknown> | null,
): SubtaskResultUpdate {
  const fromText = parseFromText(text.trim());
  const structured = readStructuredStatus(additionalKwargs);
  if (!structured) {
    return fromText;
  }

  const update: SubtaskResultUpdate = { status: structured.status };
  // Structured `subagent_error` wins; otherwise inherit the text-derived
  // error only when both sides agree on the status (so a "Task Succeeded"
  // body can't bleed into a `failed` structured stamp and vice versa).
  if (structured.error) {
    update.error = structured.error;
  } else if (
    fromText.status === structured.status &&
    fromText.error !== undefined
  ) {
    update.error = fromText.error;
  }
  // Result body only matters for `completed`; require text agreement so
  // a lying success prefix under a `failed` stamp is dropped.
  if (
    structured.status === "completed" &&
    fromText.status === "completed" &&
    fromText.result !== undefined
  ) {
    update.result = fromText.result;
  }
  return update;
}

export function hasSubtaskToolResult(
  toolCallId: string | undefined,
  messages: Message[],
) {
  if (!toolCallId) {
    return false;
  }
  return messages.some(
    (message) => message.type === "tool" && message.tool_call_id === toolCallId,
  );
}

/**
 * Determine whether a status from a ``wait_for_tasks`` JSON result is
 * transient (i.e. the subagent is still running or parked as IDLE).
 *
 * When the turn is not loading, transient statuses from stale
 * ``wait_for_tasks`` JSON should be skipped so they don't override the
 * terminal status already parsed from the ``task`` tool result.
 */
export function isTransientWaitForTasksStatus(status: string): boolean {
  return (
    status === "idle" ||
    status === "interrupted" ||
    status === "pending" ||
    status === "running"
  );
}

/**
 * Map a ``wait_for_tasks`` JSON status to a {@link SubtaskResultUpdate}.
 *
 * Returns ``null`` when the entry should be skipped entirely.
 *
 * When the turn is **not loading** (turn ended, restart, conversation
 * switch to a completed thread) the behaviour depends on ``hasMirror`` and
 * ``seenLive``:
 * - ``hasMirror`` true (the thread state carries the ``subagents`` mirror
 *   channel): all transient statuses are **skipped** (``null``). The mirror
 *   owns the last-known transient truth — it is written on every model call
 *   and survives restarts — so stale ``wait_for_tasks`` JSON must neither
 *   fold ``idle`` / ``interrupted`` to ``completed`` nor regress a
 *   mirror-set status.
 * - ``hasMirror`` falsy and ``seenLive`` falsy (legacy cold-opened history;
 *   the task never received a live SSE event this session): ``idle`` /
 *   ``interrupted`` are folded to ``completed``. The parked subagent has
 *   almost certainly been reaped by the server-side TTL, so "dormant" would
 *   be a lie — this is the fix from commit 8b7f6953 and also prevents
 *   detached tasks (whose ``task`` tool result is ``"Task spawned."`` →
 *   ``in_progress``) from showing as a spinning loader after reload.
 * - ``seenLive`` true (the task went IDLE/INTERRUPTED in this live
 *   session): the status is kept as-is. The subagent may still be
 *   resident and follow-up-able; the ``task_expired`` SSE event flips
 *   it to ``completed`` when the TTL fires.
 * - ``running`` / ``pending`` → **skipped** (``null``). A detached task
 *   may genuinely still be running, and we have no way to confirm
 *   completion from stale JSON alone.
 *
 * When the turn **is loading** (active streaming), statuses are applied
 * as-is so the UI reflects the subagent's real-time state.
 */
export function mapWaitForTasksStatus(
  status: string,
  isCurrentTurnLoading: boolean,
  result?: string,
  error?: string,
  seenLive?: boolean,
  hasMirror?: boolean,
): SubtaskResultUpdate | null {
  if (
    !isCurrentTurnLoading &&
    hasMirror &&
    isTransientWaitForTasksStatus(status)
  ) {
    return null;
  }
  if (!isCurrentTurnLoading && !seenLive) {
    if (status === "idle" || status === "interrupted") {
      return {
        status: "completed",
        ...(result ? { result } : {}),
        ...(error ? { error } : {}),
      };
    }
    if (isTransientWaitForTasksStatus(status)) {
      return null;
    }
  }
  const mappedStatus: SubtaskStatus =
    status === "completed"
      ? "completed"
      : status === "idle"
        ? "idle"
        : status === "interrupted"
          ? "interrupted"
          : status === "pending" || status === "running"
            ? "in_progress"
            : "failed";
  return {
    status: mappedStatus,
    ...(result ? { result } : {}),
    ...(error ? { error } : {}),
  };
}

export function derivePendingSubtaskStatus(
  toolCallId: string | undefined,
  messages: Message[],
  isCurrentTurnLoading: boolean,
): SubtaskStatus {
  if (isCurrentTurnLoading || hasSubtaskToolResult(toolCallId, messages)) {
    return "in_progress";
  }
  return "failed";
}

function parseFromText(trimmed: string): SubtaskResultUpdate {
  if (trimmed.startsWith(SUCCESS_PREFIX)) {
    return {
      status: "completed",
      result: trimmed.slice(SUCCESS_PREFIX.length).trim(),
    };
  }

  if (trimmed.startsWith(FAILURE_PREFIX)) {
    return {
      status: "failed",
      error: trimmed.slice(FAILURE_PREFIX.length).trim(),
    };
  }

  if (trimmed.startsWith(TIMEOUT_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  if (trimmed.startsWith(CANCELLED_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  if (trimmed.startsWith(POLLING_TIMEOUT_PREFIX)) {
    return { status: "failed", error: trimmed };
  }

  // ToolErrorHandlingMiddleware-style wrapper, or any other terminal error
  // signal the backend forwards to the lead agent.
  if (ERROR_WRAPPER_PATTERN.test(trimmed)) {
    return { status: "failed", error: trimmed };
  }

  return { status: "in_progress" };
}

interface StructuredStatus {
  status: SubtaskStatus;
  error?: string;
}

function readStructuredStatus(
  additionalKwargs: Record<string, unknown> | null | undefined,
): StructuredStatus | null {
  if (!additionalKwargs) return null;
  const rawStatus = additionalKwargs[SUBAGENT_STATUS_KEY];
  if (typeof rawStatus !== "string") return null;
  const mapped = STRUCTURED_STATUS_TO_SUBTASK[rawStatus];
  if (mapped === undefined) {
    // Unknown future status value — stay on the legacy prefix fallback
    // so a backend that ships a new enum variant before the frontend
    // upgrades still renders something predictable instead of getting
    // pinned to "in_progress" by an empty branch.
    return null;
  }
  const rawError = additionalKwargs[SUBAGENT_ERROR_KEY];
  const result: StructuredStatus = { status: mapped };
  if (typeof rawError === "string" && rawError.trim()) {
    result.error = rawError;
  }
  return result;
}

/**
 * Decide whether an incoming subtask update must keep the previous status
 * instead of applying its own. Pure helper extracted from
 * `useUpdateSubtask` (`core/tasks/context.tsx`) so the guard is unit-testable.
 *
 * Two protections:
 * - an ``in_progress`` update must not stomp a terminal status
 *   (``completed`` / ``failed`` / ``expired``) — MessageList writes the
 *   pending tool-call state before parsing the matching ToolMessage in the
 *   same render, and keeping the terminal result stable avoids a
 *   notification loop;
 * - any non-terminal update (``in_progress`` / ``idle`` / ``interrupted``)
 *   must not stomp a task whose TTL already expired — the backend
 *   ``task_expired`` SSE event set ``ttlExpired`` + a terminal status, and
 *   stale ``wait_for_tasks`` JSON arriving later would otherwise revive it.
 */
export function shouldKeepPreviousSubtaskStatus(
  incoming: { status?: SubtaskStatus },
  previous: Subtask | undefined,
): boolean {
  const previousStatus = previous?.status;
  const isTerminalPrevious =
    previousStatus === "completed" ||
    previousStatus === "failed" ||
    previousStatus === "expired";
  const isNonTerminalIncoming =
    incoming.status === "in_progress" ||
    incoming.status === "idle" ||
    incoming.status === "interrupted";
  return (
    (incoming.status === "in_progress" && isTerminalPrevious) ||
    (isNonTerminalIncoming && previous?.ttlExpired === true)
  );
}
