import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import {
  SUBAGENT_ERROR_KEY,
  SUBAGENT_STATUS_KEY,
  derivePendingSubtaskStatus,
  hasSubtaskToolResult,
  isTransientWaitForTasksStatus,
  mapWaitForTasksStatus,
  parseSubtaskResult,
  shouldKeepPreviousSubtaskStatus,
} from "@/core/tasks/subtask-result";
import type { Subtask } from "@/core/tasks/types";

interface ContractCase {
  name: string;
  content: string;
  expected_status: string | null;
  expected_error_contains: string | null;
}

interface ContractFile {
  valid_status_values: string[];
  cases: ContractCase[];
}

const CONTRACT_PATH = resolve(
  __dirname,
  "../../../../../contracts/subagent_status_contract.json",
);
const CONTRACT: ContractFile = JSON.parse(
  readFileSync(CONTRACT_PATH, "utf-8"),
) as ContractFile;

describe("parseSubtaskResult", () => {
  it("recognises the standard success prefix", () => {
    const parsed = parseSubtaskResult(
      "Task Succeeded. Result: investigated and produced a 3-page report",
    );
    expect(parsed.status).toBe("completed");
    expect(parsed.result).toBe("investigated and produced a 3-page report");
  });

  it("recognises the standard failure prefix", () => {
    const parsed = parseSubtaskResult(
      "Task failed. underlying tool raised RuntimeError",
    );
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBe("underlying tool raised RuntimeError");
  });

  it("recognises the standard timeout prefix", () => {
    const parsed = parseSubtaskResult("Task timed out after 900s");
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBe("Task timed out after 900s");
  });

  it("recognises the cancelled-by-user prefix", () => {
    // bytedance/deer-flow#3131 review: this is one of the five terminal
    // strings task_tool.py actually emits — the previous cut treated it as
    // unrecognised content and pushed the card back to in_progress.
    const parsed = parseSubtaskResult("Task cancelled by user.");
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBe("Task cancelled by user.");
  });

  it("recognises the polling-timed-out prefix", () => {
    // Emitted by task_tool when the background polling loop runs out of
    // budget waiting for the subagent to reach a terminal state.
    const parsed = parseSubtaskResult(
      "Task polling timed out after 15 minutes. This may indicate the background task is stuck. Status: RUNNING",
    );
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toContain("polling timed out");
  });

  it("recognises polling-timed-out with different durations", () => {
    // `task_tool` emits `Task polling timed out after {N} minutes` where N
    // varies with the configured subagent timeout. Guard against the regex
    // accidentally being pinned to a specific number.
    for (const n of [1, 5, 60]) {
      const parsed = parseSubtaskResult(
        `Task polling timed out after ${n} minutes. Status: RUNNING`,
      );
      expect(parsed.status).toBe("failed");
    }
  });

  it("trims whitespace around cancelled and polling-timed-out prefixes", () => {
    // Streaming chunks sometimes arrive with leading/trailing newlines.
    expect(parseSubtaskResult("  Task cancelled by user.  \n").status).toBe(
      "failed",
    );
    expect(
      parseSubtaskResult("\n\nTask polling timed out after 3 minutes").status,
    ).toBe("failed");
  });

  it("recognises task_tool pre-execution Error: returns via the wrapper", () => {
    // `task_tool.py` returns `Error:` strings for unknown subagent
    // type and "task disappeared". They share the ERROR_WRAPPER_PATTERN,
    // not a dedicated prefix, so this guards against a refactor splitting
    // them off.
    for (const text of [
      "Error: Unknown subagent type 'foo'. Available: general-purpose",
      "Error: Task 1234 disappeared from background tasks",
    ]) {
      expect(parseSubtaskResult(text).status).toBe("failed");
    }
  });

  it("treats middleware-wrapped tool errors as terminal failures", () => {
    // bytedance/deer-flow issue #3107 BUG-007: the parent-visible ToolMessage
    // produced by ToolErrorHandlingMiddleware never matches the three legacy
    // prefixes, so subtask cards stay stuck on "in_progress".
    const parsed = parseSubtaskResult(
      "Error: Tool 'task' failed with TypeError: 'AsyncCallbackManager' object is not iterable. Continue with available context, or choose an alternative tool.",
    );
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toContain("AsyncCallbackManager");
  });

  it("treats any other Error: prefix as a terminal failure", () => {
    const parsed = parseSubtaskResult("Error: subagent worker pool exhausted");
    expect(parsed.status).toBe("failed");
  });

  it("keeps unrecognised non-error output as in_progress", () => {
    // Streaming partial chunks should not flip the card to terminal early.
    const parsed = parseSubtaskResult("Investigating ...");
    expect(parsed.status).toBe("in_progress");
    expect(parsed.error).toBeUndefined();
    expect(parsed.result).toBeUndefined();
  });

  it("trims surrounding whitespace before matching prefixes", () => {
    const parsed = parseSubtaskResult("   Task Succeeded. Result: ok   ");
    expect(parsed.status).toBe("completed");
    expect(parsed.result).toBe("ok");
  });
});

describe("hasSubtaskToolResult", () => {
  it("matches a task tool call to its ToolMessage", () => {
    const messages = [
      { type: "ai" },
      { type: "tool", tool_call_id: "call_task_1" },
    ] as Message[];

    expect(hasSubtaskToolResult("call_task_1", messages)).toBe(true);
  });

  it("returns false when a task tool call has no ToolMessage", () => {
    const messages = [
      { type: "ai" },
      { type: "tool", tool_call_id: "call_other" },
    ] as Message[];

    expect(hasSubtaskToolResult("call_task_1", messages)).toBe(false);
  });
});

describe("derivePendingSubtaskStatus", () => {
  it("keeps a task in progress while its own assistant turn is loading", () => {
    const messages = [{ type: "ai" }] as Message[];

    expect(derivePendingSubtaskStatus("call_task_1", messages, true)).toBe(
      "in_progress",
    );
  });

  it("does not revive an earlier unfinished task during a later turn", () => {
    const messages = [{ type: "ai" }] as Message[];

    expect(derivePendingSubtaskStatus("call_task_1", messages, false)).toBe(
      "failed",
    );
  });

  it("leaves result parsing to the ToolMessage path when a result exists", () => {
    const messages = [
      { type: "ai" },
      { type: "tool", tool_call_id: "call_task_1" },
    ] as Message[];

    expect(derivePendingSubtaskStatus("call_task_1", messages, false)).toBe(
      "in_progress",
    );
  });
});

describe("isTransientWaitForTasksStatus", () => {
  it("returns true for idle, interrupted, pending, running", () => {
    expect(isTransientWaitForTasksStatus("idle")).toBe(true);
    expect(isTransientWaitForTasksStatus("interrupted")).toBe(true);
    expect(isTransientWaitForTasksStatus("pending")).toBe(true);
    expect(isTransientWaitForTasksStatus("running")).toBe(true);
  });

  it("returns false for terminal statuses", () => {
    expect(isTransientWaitForTasksStatus("completed")).toBe(false);
    expect(isTransientWaitForTasksStatus("failed")).toBe(false);
    expect(isTransientWaitForTasksStatus("cancelled")).toBe(false);
    expect(isTransientWaitForTasksStatus("timed_out")).toBe(false);
  });

  it("returns false for unknown statuses", () => {
    expect(isTransientWaitForTasksStatus("unknown")).toBe(false);
    expect(isTransientWaitForTasksStatus("")).toBe(false);
  });
});

describe("mapWaitForTasksStatus", () => {
  describe("when turn is not loading (restart, turn ended)", () => {
    it("maps idle to completed", () => {
      const result = mapWaitForTasksStatus("idle", false, "task output");
      expect(result).toEqual({ status: "completed", result: "task output" });
    });

    it("maps interrupted to completed", () => {
      const result = mapWaitForTasksStatus(
        "interrupted",
        false,
        undefined,
        "paused",
      );
      expect(result).toEqual({ status: "completed", error: "paused" });
    });

    it("skips running (returns null)", () => {
      const result = mapWaitForTasksStatus("running", false);
      expect(result).toBeNull();
    });

    it("skips pending (returns null)", () => {
      const result = mapWaitForTasksStatus("pending", false);
      expect(result).toBeNull();
    });

    it("still applies completed as completed", () => {
      const result = mapWaitForTasksStatus("completed", false, "done");
      expect(result).toEqual({ status: "completed", result: "done" });
    });

    it("still applies failed as failed", () => {
      const result = mapWaitForTasksStatus("failed", false, undefined, "oops");
      expect(result).toEqual({ status: "failed", error: "oops" });
    });

    it("maps cancelled to failed", () => {
      const result = mapWaitForTasksStatus("cancelled", false);
      expect(result).toEqual({ status: "failed" });
    });

    it("maps timed_out to failed", () => {
      const result = mapWaitForTasksStatus("timed_out", false);
      expect(result).toEqual({ status: "failed" });
    });

    it("maps unknown status to failed", () => {
      const result = mapWaitForTasksStatus("unknown_status", false);
      expect(result).toEqual({ status: "failed" });
    });
  });

  describe("when turn is loading (active streaming)", () => {
    it("applies idle as idle", () => {
      const result = mapWaitForTasksStatus("idle", true);
      expect(result).toEqual({ status: "idle" });
    });

    it("applies interrupted as interrupted", () => {
      const result = mapWaitForTasksStatus("interrupted", true);
      expect(result).toEqual({ status: "interrupted" });
    });

    it("applies running as in_progress", () => {
      const result = mapWaitForTasksStatus("running", true);
      expect(result).toEqual({ status: "in_progress" });
    });

    it("applies pending as in_progress", () => {
      const result = mapWaitForTasksStatus("pending", true);
      expect(result).toEqual({ status: "in_progress" });
    });

    it("applies completed as completed", () => {
      const result = mapWaitForTasksStatus("completed", true, "done");
      expect(result).toEqual({ status: "completed", result: "done" });
    });

    it("applies failed as failed", () => {
      const result = mapWaitForTasksStatus("failed", true, undefined, "err");
      expect(result).toEqual({ status: "failed", error: "err" });
    });
  });

  it("preserves result and error fields when mapping idle to completed", () => {
    const result = mapWaitForTasksStatus("idle", false, "output", "warning");
    expect(result).toEqual({
      status: "completed",
      result: "output",
      error: "warning",
    });
  });

  describe("when turn is not loading but the task went live this session (seenLive)", () => {
    // Turn ended in a live session: the IDLE/INTERRUPTED subagent may still
    // be resident server-side (TTL / resume), so its real state must show.
    // The cold-open folding (commit 8b7f6953) is keyed on seenLive=false.
    it("keeps idle as idle", () => {
      const result = mapWaitForTasksStatus(
        "idle",
        false,
        "task output",
        undefined,
        true,
      );
      expect(result).toEqual({ status: "idle", result: "task output" });
    });

    it("keeps interrupted as interrupted", () => {
      const result = mapWaitForTasksStatus(
        "interrupted",
        false,
        undefined,
        "paused",
        true,
      );
      expect(result).toEqual({ status: "interrupted", error: "paused" });
    });

    it("applies running / pending as in_progress with seenLive", () => {
      // Live session: a detached task that is still running must keep
      // showing as running — the transient skip only applies to cold-opened
      // history (seenLive=false).
      for (const status of ["running", "pending"]) {
        const result = mapWaitForTasksStatus(
          status,
          false,
          undefined,
          undefined,
          true,
        );
        expect(result).toEqual({ status: "in_progress" });
      }
    });

    it("still applies terminal statuses with seenLive", () => {
      const result = mapWaitForTasksStatus(
        "completed",
        false,
        "done",
        undefined,
        true,
      );
      expect(result).toEqual({ status: "completed", result: "done" });
    });
  });

  describe("when a backend subagent mirror is present (hasMirror)", () => {
    // With the ThreadState `subagents` channel the mirror owns transient
    // truth; stale wait_for_tasks JSON must not stomp it, so transient
    // statuses are skipped instead of folded to completed.
    it("skips idle instead of folding to completed", () => {
      const result = mapWaitForTasksStatus(
        "idle",
        false,
        "task output",
        undefined,
        false,
        true,
      );
      expect(result).toBeNull();
    });

    it("skips interrupted instead of folding to completed", () => {
      const result = mapWaitForTasksStatus(
        "interrupted",
        false,
        undefined,
        "paused",
        false,
        true,
      );
      expect(result).toBeNull();
    });

    it("skips running / pending", () => {
      for (const status of ["running", "pending"]) {
        const result = mapWaitForTasksStatus(
          status,
          false,
          undefined,
          undefined,
          false,
          true,
        );
        expect(result).toBeNull();
      }
    });

    it("skips transient statuses even with seenLive", () => {
      const result = mapWaitForTasksStatus(
        "idle",
        false,
        "task output",
        undefined,
        true,
        true,
      );
      expect(result).toBeNull();
    });

    it("still applies terminal statuses", () => {
      expect(
        mapWaitForTasksStatus(
          "completed",
          false,
          "done",
          undefined,
          false,
          true,
        ),
      ).toEqual({ status: "completed", result: "done" });
      expect(
        mapWaitForTasksStatus("failed", false, undefined, "oops", false, true),
      ).toEqual({ status: "failed", error: "oops" });
    });

    it("applies transient statuses as-is while the turn is loading", () => {
      const result = mapWaitForTasksStatus(
        "idle",
        true,
        undefined,
        undefined,
        false,
        true,
      );
      expect(result).toEqual({ status: "idle" });
    });
  });
});

describe("shouldKeepPreviousSubtaskStatus", () => {
  const makePrevious = (
    status: Subtask["status"],
    ttlExpired?: boolean,
  ): Subtask =>
    ({
      id: "task-1",
      status,
      subagent_type: "general-purpose",
      description: "d",
      prompt: "p",
      ...(ttlExpired ? { ttlExpired: true } : {}),
    }) as Subtask;

  it("in_progress never stomps a terminal previous status", () => {
    for (const terminal of ["completed", "failed", "expired"] as const) {
      expect(
        shouldKeepPreviousSubtaskStatus(
          { status: "in_progress" },
          makePrevious(terminal),
        ),
      ).toBe(true);
    }
  });

  it("non-terminal updates never stomp a ttlExpired previous status", () => {
    for (const incoming of ["in_progress", "idle", "interrupted"] as const) {
      expect(
        shouldKeepPreviousSubtaskStatus(
          { status: incoming },
          makePrevious("completed", true),
        ),
      ).toBe(true);
    }
  });

  it("interrupted may replace a completed status that was not TTL-expired", () => {
    // Turn ended → JSON folding wrote completed; a live task_interrupted
    // replay must still be able to correct it when no TTL expiry happened.
    expect(
      shouldKeepPreviousSubtaskStatus(
        { status: "interrupted" },
        makePrevious("completed"),
      ),
    ).toBe(false);
  });

  it("terminal updates always apply", () => {
    expect(
      shouldKeepPreviousSubtaskStatus(
        { status: "completed" },
        makePrevious("idle"),
      ),
    ).toBe(false);
    expect(
      shouldKeepPreviousSubtaskStatus(
        { status: "failed" },
        makePrevious("completed"),
      ),
    ).toBe(false);
  });

  it("applies freely when there is no previous task", () => {
    expect(
      shouldKeepPreviousSubtaskStatus({ status: "in_progress" }, undefined),
    ).toBe(false);
  });
});

/**
 * Structured-status path (bytedance/deer-flow#3146).
 *
 * The backend stamps `ToolMessage.additional_kwargs.subagent_status`
 * directly. The frontend should prefer that over reverse-engineering it
 * from the content string.
 */
describe("parseSubtaskResult — structured additional_kwargs (preferred path)", () => {
  it("uses additional_kwargs.subagent_status when present", () => {
    const parsed = parseSubtaskResult("Task Succeeded. Result: foo", {
      [SUBAGENT_STATUS_KEY]: "completed",
    });
    expect(parsed.status).toBe("completed");
  });

  it("collapses cancelled / timed_out / polling_timed_out to failed for the card UI", () => {
    for (const backendStatus of [
      "cancelled",
      "timed_out",
      "polling_timed_out",
    ]) {
      const parsed = parseSubtaskResult("anything at all", {
        [SUBAGENT_STATUS_KEY]: backendStatus,
      });
      expect(parsed.status).toBe("failed");
    }
  });

  it("maps structured idle / interrupted to the matching card states", () => {
    expect(
      parseSubtaskResult("anything at all", {
        [SUBAGENT_STATUS_KEY]: "idle",
      }).status,
    ).toBe("idle");
    expect(
      parseSubtaskResult("anything at all", {
        [SUBAGENT_STATUS_KEY]: "interrupted",
      }).status,
    ).toBe("interrupted");
  });

  it("uses subagent_error when supplied", () => {
    const parsed = parseSubtaskResult("ignored content", {
      [SUBAGENT_STATUS_KEY]: "failed",
      [SUBAGENT_ERROR_KEY]: "boom from backend",
    });
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBe("boom from backend");
  });

  it("ignores empty / non-string subagent_error", () => {
    const parsed = parseSubtaskResult("ignored content", {
      [SUBAGENT_STATUS_KEY]: "failed",
      [SUBAGENT_ERROR_KEY]: "",
    });
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBeUndefined();
  });

  it("falls back to prefix parsing when the structured status is missing", () => {
    const parsed = parseSubtaskResult("Task Succeeded. Result: foo", {
      // No subagent_status here — backend versions that pre-date the
      // middleware stamping commit still need to render.
      other_field: "irrelevant",
    });
    expect(parsed.status).toBe("completed");
    expect(parsed.result).toBe("foo");
  });

  it("falls back to prefix parsing when the structured status is an unknown future value", () => {
    const parsed = parseSubtaskResult("Task Succeeded. Result: foo", {
      [SUBAGENT_STATUS_KEY]: "renamed_in_v3",
    });
    // Falls back to prefix and still finds the success path.
    expect(parsed.status).toBe("completed");
  });

  it("structured status overrides legacy text — opposite content", () => {
    // Defence: if backend sends `failed` structured but the content
    // accidentally starts with "Task Succeeded.", we must trust the
    // structured field. The structured field is the source of truth.
    const parsed = parseSubtaskResult("Task Succeeded. Result: this is a lie", {
      [SUBAGENT_STATUS_KEY]: "failed",
    });
    expect(parsed.status).toBe("failed");
    // The misleading success body must be dropped — `result` is reserved
    // for the completed pill, and the suspicious text isn't replayed as
    // an error either.
    expect(parsed.result).toBeUndefined();
    expect(parsed.error).toBeUndefined();
  });

  it("back-fills `result` from the success-prefixed content when structured says completed", () => {
    // The backend currently stamps `subagent_status: completed` but the
    // success body still lives in `content`. Without back-fill the card
    // would render an empty completed pill (regression flagged in PR #3154
    // Copilot review).
    const parsed = parseSubtaskResult(
      "Task Succeeded. Result: investigated and produced a 3-page report",
      { [SUBAGENT_STATUS_KEY]: "completed" },
    );
    expect(parsed.status).toBe("completed");
    expect(parsed.result).toBe("investigated and produced a 3-page report");
  });

  it("back-fills `error` from a wrapped-error body when structured says failed and no subagent_error", () => {
    // Same regression on the failure side: the wrapper text is the only
    // place the diagnostic message exists when the backend stamps the
    // enum but not `subagent_error`.
    const parsed = parseSubtaskResult(
      "Error: Tool 'task' failed with TypeError: boom",
      { [SUBAGENT_STATUS_KEY]: "failed" },
    );
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toContain("TypeError: boom");
  });

  it("leaves `error` undefined when structured says failed with no error and unrecognised text", () => {
    // Don't dump arbitrary content into the error field — better to render
    // an empty `failed` pill than to surface noise.
    const parsed = parseSubtaskResult("partial streaming chunk", {
      [SUBAGENT_STATUS_KEY]: "failed",
    });
    expect(parsed.status).toBe("failed");
    expect(parsed.error).toBeUndefined();
  });
});

/**
 * Cross-language contract test (bytedance/deer-flow#3146).
 *
 * Loads the shared fixture at ``contracts/subagent_status_contract.json``
 * and runs every case through the legacy prefix parser. The matching
 * backend test (`backend/tests/test_subagent_status_contract.py`) runs
 * the same cases through ``extract_subagent_status``. Any drift between
 * the two implementations surfaces here.
 *
 * Status-collapse expectations:
 *   - `completed`  → `completed`
 *   - `failed`     → `failed`
 *   - `cancelled` / `timed_out` / `polling_timed_out` → `failed`
 *     (the frontend card has three pill states, not five)
 *   - `null`       → `in_progress`
 */
describe("parseSubtaskResult — shared contract fixture", () => {
  const expectedCardStatus = (backendStatus: string | null): string => {
    if (backendStatus === null) return "in_progress";
    if (backendStatus === "completed") return "completed";
    return "failed";
  };

  for (const c of CONTRACT.cases) {
    it(`legacy prefix parser matches contract: ${c.name}`, () => {
      const parsed = parseSubtaskResult(c.content);
      expect(parsed.status).toBe(expectedCardStatus(c.expected_status));
    });
  }

  it("every valid_status_values entry is handled by the structured-status path", () => {
    // Contract drift guard: the backend enum and the frontend's
    // STRUCTURED_STATUS_TO_SUBTASK must stay in sync — an unmapped value
    // falls back to prefix parsing and lands on in_progress.
    for (const value of CONTRACT.valid_status_values) {
      const parsed = parseSubtaskResult("anything at all", {
        [SUBAGENT_STATUS_KEY]: value,
      });
      expect(parsed.status).not.toBe("in_progress");
    }
  });
});
