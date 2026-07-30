import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import type { SubagentMirror } from "./mirror";
import {
  findExpiredIdleTasks,
  findNextIdleExpiry,
  mapMirrorStatusToSubtask,
  shouldApplyMirrorUpdate,
} from "./mirror";
import { shouldKeepPreviousSubtaskStatus } from "./subtask-result";
import type { Subtask } from "./types";

function isTerminalSubtaskStatus(status: Subtask["status"] | undefined) {
  return status === "completed" || status === "failed" || status === "expired";
}

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: (tasks: Record<string, Subtask>) => void;
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  setTasks: () => {
    /* noop */
  },
});

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;

  // Local IDLE TTL: the backend task_expired SSE event is emitted outside
  // the graph and is lost once the lead run's stream has closed, so the
  // mirror's idle_expires_at drives a client-side flip to
  // completed + ttlExpired (same shape as the SSE handler). No deps array:
  // re-evaluate after every render and keep at most one pending timer. The
  // setTasks call is guarded by an actual expiry, so it cannot loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const now = Date.now();
    const expired = findExpiredIdleTasks(tasksRef.current, now);
    if (expired.length > 0) {
      const next = { ...tasksRef.current };
      for (const id of expired) {
        const task = next[id];
        if (task?.status === "idle") {
          next[id] = { ...task, status: "completed", ttlExpired: true };
        }
      }
      setTasks(next);
      return;
    }
    const nextExpiry = findNextIdleExpiry(tasksRef.current, now);
    if (nextExpiry == null) {
      return;
    }
    const timer = setTimeout(() => {
      const firingNow = Date.now();
      const firing = findExpiredIdleTasks(tasksRef.current, firingNow);
      if (firing.length === 0) {
        return;
      }
      const next = { ...tasksRef.current };
      for (const id of firing) {
        const task = next[id];
        if (task?.status === "idle") {
          next[id] = { ...task, status: "completed", ttlExpired: true };
        }
      }
      setTasks(next);
    }, nextExpiry - now);
    return () => clearTimeout(timer);
  });

  return (
    <SubtaskContext.Provider value={{ tasks, setTasks }}>
      {children}
    </SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

export function useUpdateSubtask() {
  const { tasks, setTasks } = useSubtaskContext();
  const shouldNotifyAfterRenderRef = useRef(false);
  // No deps: must run after every render to check the ref set during render.
  useEffect(() => {
    if (!shouldNotifyAfterRenderRef.current) {
      return;
    }
    shouldNotifyAfterRenderRef.current = false;
    setTasks({ ...tasks });
  });

  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      const previous = tasks[task.id];
      const previousStatus = previous?.status;
      // MessageList writes the pending task tool-call state before parsing the
      // matching ToolMessage in the same render. Keep terminal results stable
      // across the next render so the refresh notification does not loop.
      // Also guard against stale wait_for_tasks JSON overriding a status that
      // was set by a backend TTL-expiry SSE event (ttlExpired flag).
      const keepPreviousStatus = shouldKeepPreviousSubtaskStatus(
        task,
        previous,
      );
      const next = {
        ...previous,
        ...task,
        ...(keepPreviousStatus ? { status: previousStatus } : {}),
      } as Subtask;

      const becameTerminal =
        isTerminalSubtaskStatus(next.status) && previousStatus !== next.status;

      tasks[task.id] = next;

      if (task.latestMessage) {
        setTasks({ ...tasks });
      } else if (becameTerminal) {
        shouldNotifyAfterRenderRef.current = true;
      }
    },
    [tasks, setTasks],
  );

  return updateSubtask;
}

/**
 * Sync the backend ``ThreadState.subagents`` mirror channel into the
 * subtask store. The mirror is the persisted baseline — it survives
 * restarts and cold opens where live SSE events are unavailable — while
 * ``custom`` SSE events stay the real-time increments. Out-of-order
 * snapshots are skipped via ``mirrorUpdatedAt`` and terminal/ttlExpired
 * statuses are protected by the guards inside {@link useUpdateSubtask}.
 */
export function useSubagentMirrorSync(
  mirror: SubagentMirror | null | undefined,
) {
  const updateSubtask = useUpdateSubtask();
  const { tasks } = useSubtaskContext();
  useEffect(() => {
    if (!mirror) {
      return;
    }
    for (const [taskId, entry] of Object.entries(mirror)) {
      if (!shouldApplyMirrorUpdate(tasks[taskId], entry)) {
        continue;
      }
      updateSubtask({
        id: taskId,
        subagent_type: entry.subagent_type,
        status: mapMirrorStatusToSubtask(entry.status),
        mirrorUpdatedAt: entry.updated_at,
        ...(entry.idle_expires_at != null
          ? { idleExpiresAt: entry.idle_expires_at * 1000 }
          : {}),
      });
    }
  }, [mirror, tasks, updateSubtask]);
}
