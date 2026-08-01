import { describe, expect, it } from "@rstest/core";

import {
  findExpiredIdleTasks,
  findNextIdleExpiry,
  hasSubagentMirror,
  mapMirrorStatusToSubtask,
  mirrorEntryToSubtaskUpdate,
  shouldApplyMirrorUpdate,
} from "@/core/tasks/mirror";
import type { Subtask } from "@/core/tasks/types";
import type { SubagentMirrorEntry } from "@/core/threads/types";

function makeEntry(
  overrides: Partial<SubagentMirrorEntry> = {},
): SubagentMirrorEntry {
  return {
    task_id: "task-1",
    subagent_type: "general-purpose",
    status: "running",
    updated_at: 1000,
    ...overrides,
  };
}

function makeSubtask(overrides: Partial<Subtask> = {}): Subtask {
  return {
    id: "task-1",
    status: "in_progress",
    subagent_type: "general-purpose",
    description: "do something",
    prompt: "prompt",
    ...overrides,
  };
}

describe("mapMirrorStatusToSubtask", () => {
  it("maps pending and running to in_progress", () => {
    expect(mapMirrorStatusToSubtask("pending")).toBe("in_progress");
    expect(mapMirrorStatusToSubtask("running")).toBe("in_progress");
  });

  it("maps idle and interrupted to their own card states", () => {
    expect(mapMirrorStatusToSubtask("idle")).toBe("idle");
    expect(mapMirrorStatusToSubtask("interrupted")).toBe("interrupted");
  });

  it("maps completed to completed", () => {
    expect(mapMirrorStatusToSubtask("completed")).toBe("completed");
  });

  it("collapses failed and cancelled to failed", () => {
    expect(mapMirrorStatusToSubtask("failed")).toBe("failed");
    expect(mapMirrorStatusToSubtask("cancelled")).toBe("failed");
  });
});

describe("shouldApplyMirrorUpdate", () => {
  it("applies when there is no existing task", () => {
    expect(shouldApplyMirrorUpdate(undefined, makeEntry())).toBe(true);
  });

  it("applies when the existing task never saw a mirror update", () => {
    expect(shouldApplyMirrorUpdate(makeSubtask(), makeEntry())).toBe(true);
  });

  it("applies when the entry is newer than the last applied mirror update", () => {
    const existing = makeSubtask({ mirrorUpdatedAt: 999 });
    expect(
      shouldApplyMirrorUpdate(existing, makeEntry({ updated_at: 1000 })),
    ).toBe(true);
  });

  it("applies when the entry has the same timestamp (idempotent re-apply)", () => {
    const existing = makeSubtask({ mirrorUpdatedAt: 1000 });
    expect(
      shouldApplyMirrorUpdate(existing, makeEntry({ updated_at: 1000 })),
    ).toBe(true);
  });

  it("skips when the existing task already saw a newer mirror update", () => {
    const existing = makeSubtask({ mirrorUpdatedAt: 1001 });
    expect(
      shouldApplyMirrorUpdate(existing, makeEntry({ updated_at: 1000 })),
    ).toBe(false);
  });
});

describe("hasSubagentMirror", () => {
  it("is false for undefined and null", () => {
    expect(hasSubagentMirror(undefined)).toBe(false);
    expect(hasSubagentMirror(null)).toBe(false);
  });

  it("is true for an empty mirror object", () => {
    expect(hasSubagentMirror({})).toBe(true);
  });

  it("is true for a mirror with entries", () => {
    expect(hasSubagentMirror({ "task-1": makeEntry() })).toBe(true);
  });
});

describe("findExpiredIdleTasks", () => {
  const now = 1_000_000;

  it("returns idle tasks whose idleExpiresAt is in the past", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "idle", idleExpiresAt: now - 1 }),
    };
    expect(findExpiredIdleTasks(tasks, now)).toEqual(["task-1"]);
  });

  it("returns idle tasks whose idleExpiresAt is exactly now", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "idle", idleExpiresAt: now }),
    };
    expect(findExpiredIdleTasks(tasks, now)).toEqual(["task-1"]);
  });

  it("excludes idle tasks whose idleExpiresAt is in the future", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "idle", idleExpiresAt: now + 1000 }),
    };
    expect(findExpiredIdleTasks(tasks, now)).toEqual([]);
  });

  it("excludes idle tasks without idleExpiresAt", () => {
    const tasks = { "task-1": makeSubtask({ status: "idle" }) };
    expect(findExpiredIdleTasks(tasks, now)).toEqual([]);
  });

  it("excludes non-idle tasks even when idleExpiresAt is in the past", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "in_progress", idleExpiresAt: now - 1 }),
      "task-2": makeSubtask({
        id: "task-2",
        status: "completed",
        idleExpiresAt: now - 1,
      }),
    };
    expect(findExpiredIdleTasks(tasks, now)).toEqual([]);
  });
});

describe("findNextIdleExpiry", () => {
  const now = 1_000_000;

  it("returns the earliest future expiry among idle tasks", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "idle", idleExpiresAt: now + 5000 }),
      "task-2": makeSubtask({
        id: "task-2",
        status: "idle",
        idleExpiresAt: now + 2000,
      }),
    };
    expect(findNextIdleExpiry(tasks, now)).toBe(now + 2000);
  });

  it("ignores past expiries and non-idle tasks", () => {
    const tasks = {
      "task-1": makeSubtask({ status: "idle", idleExpiresAt: now - 1 }),
      "task-2": makeSubtask({
        id: "task-2",
        status: "completed",
        idleExpiresAt: now + 1000,
      }),
      "task-3": makeSubtask({
        id: "task-3",
        status: "idle",
        idleExpiresAt: now + 3000,
      }),
    };
    expect(findNextIdleExpiry(tasks, now)).toBe(now + 3000);
  });

  it("returns null when no idle task has a future expiry", () => {
    expect(findNextIdleExpiry({}, now)).toBeNull();
    const tasks = { "task-1": makeSubtask({ status: "idle" }) };
    expect(findNextIdleExpiry(tasks, now)).toBeNull();
  });
});

describe("mirrorEntryToSubtaskUpdate", () => {
  it("maps entry fields into a subtask update", () => {
    const entry = makeEntry({
      status: "idle",
      updated_at: 1234,
      idle_expires_at: 2000,
    });
    expect(mirrorEntryToSubtaskUpdate("task-1", entry)).toEqual({
      id: "task-1",
      subagent_type: "general-purpose",
      status: "idle",
      mirrorUpdatedAt: 1234,
      idleExpiresAt: 2000 * 1000,
      parent_task_id: null,
    });
  });

  it("passes parent_task_id through for nested tasks", () => {
    const entry = makeEntry({ parent_task_id: "parent-1" });
    expect(mirrorEntryToSubtaskUpdate("nested-1", entry)).toMatchObject({
      id: "nested-1",
      parent_task_id: "parent-1",
    });
  });

  it("defaults parent_task_id to null when absent", () => {
    const entry = makeEntry();
    expect(
      mirrorEntryToSubtaskUpdate("task-1", entry).parent_task_id,
    ).toBeNull();
  });

  it("omits idleExpiresAt when the entry has none", () => {
    const entry = makeEntry({ idle_expires_at: null });
    expect(mirrorEntryToSubtaskUpdate("task-1", entry)).not.toHaveProperty(
      "idleExpiresAt",
    );
  });
});
