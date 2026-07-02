/**
 * Resume a paused subagent by feeding its ``interrupt()`` a value via the
 * per-subagent Gateway endpoint.
 *
 * Under Route 甲 the lead run stays open (blocked in the ``task`` tool) while a
 * subagent is paused, and the lead stream carries the subsequent
 * ``task_running``/``task_completed`` events. So this call is fire-and-forget:
 * it only needs to *kick* the subagent (``resume_background_subagent`` runs
 * before the endpoint starts streaming), then release the response body. The
 * caller does not consume the endpoint's redundant SSE stream.
 */

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export async function resumeSubagent(
  threadId: string,
  taskId: string,
  resume: unknown,
): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/subagents/${encodeURIComponent(taskId)}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume }),
    },
  );
  if (!res.ok) {
    throw new Error(`Failed to resume subagent ${taskId}: ${res.status}`);
  }
  // The endpoint streams the resumed run's task_* events as SSE, but the lead
  // run's stream is the source of truth under Route 甲. Drop the body so the
  // connection does not linger for the subagent's whole resumed run; the kick
  // already happened before streaming began.
  void res.body?.cancel().catch(() => undefined);
}
