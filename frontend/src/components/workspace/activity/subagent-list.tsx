"use client";

import {
  CheckCircleIcon,
  ClockIcon,
  Loader2Icon,
  MessageCircleQuestionIcon,
  PauseCircleIcon,
  XCircleIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";

import type { Subtask } from "@/core/tasks/types";
import { useResolvedThreadId } from "@/core/threads/thread-id";
import { cn } from "@/lib/utils";

function statusIcon(status: Subtask["status"]) {
  switch (status) {
    case "completed":
      return <CheckCircleIcon className="size-3.5 text-green-500" />;
    case "failed":
      return <XCircleIcon className="size-3.5 text-red-500" />;
    case "expired":
      return <ClockIcon className="size-3.5 text-red-500" />;
    case "idle":
      return <PauseCircleIcon className="size-3.5 text-yellow-500" />;
    case "interrupted":
      return <MessageCircleQuestionIcon className="size-3.5 text-yellow-500" />;
    case "in_progress":
    default:
      return <Loader2Icon className="size-3.5 animate-spin" />;
  }
}

export function SubagentList({ tasks }: { tasks: Subtask[] }) {
  const router = useRouter();
  const threadId = useResolvedThreadId();

  const handleClick = (taskId: string) => {
    if (!threadId) {
      return;
    }
    router.push(
      `/workspace/chats/${encodeURIComponent(threadId)}/subagents/${encodeURIComponent(taskId)}`,
    );
  };

  return (
    <ul className="flex flex-col gap-1">
      {tasks.map((task) => (
        <li key={task.id}>
          <button
            type="button"
            onClick={() => handleClick(task.id)}
            className={cn(
              "hover:bg-accent flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
            )}
          >
            <span className="mt-0.5 shrink-0">{statusIcon(task.status)}</span>
            <span className="min-w-0 flex-1">
              <span className="font-semibold">[{task.subagent_type}]</span>{" "}
              <span className="text-muted-foreground line-clamp-2">
                {task.description}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
