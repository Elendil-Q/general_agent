"use client";

import { ActivityIcon } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useThread } from "@/components/workspace/messages/context";
import { TodoList } from "@/components/workspace/todo-list";
import { useI18n } from "@/core/i18n/hooks";
import { useSubtaskContext } from "@/core/tasks/context";

import { SubagentList } from "./subagent-list";

export function ActivityPanel() {
  const { t } = useI18n();
  const { thread } = useThread();
  const { tasks } = useSubtaskContext();

  const todos = thread.values.todos ?? [];
  const activeTasks = Object.values(tasks).filter(
    (task) =>
      task.status === "in_progress" ||
      task.status === "idle" ||
      task.status === "completed" ||
      task.status === "failed",
  );

  return (
    <div className="bg-background flex h-full flex-col border-r">
      <header className="flex h-12 shrink-0 items-center border-b px-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ActivityIcon className="size-4" />
          {t.activity.title}
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        {todos.length > 0 && (
          <section className="flex shrink-0 flex-col border-b">
            <h3 className="text-muted-foreground px-3 pt-3 text-xs font-semibold uppercase">
              {t.activity.todos}
            </h3>
            <div className="px-3 pt-2 pb-3">
              <TodoList
                todos={todos}
                collapsed={false}
                variant="sidebar"
                className="rounded-lg border"
              />
            </div>
          </section>
        )}
        {activeTasks.length > 0 && (
          <section className="flex min-h-0 flex-1 flex-col">
            <h3 className="text-muted-foreground shrink-0 px-3 pt-3 text-xs font-semibold uppercase">
              {t.activity.subagents}
            </h3>
            <ScrollArea className="min-h-0 flex-1">
              <div className="px-3 pt-2 pb-3">
                <SubagentList tasks={activeTasks} />
              </div>
            </ScrollArea>
          </section>
        )}
      </div>
    </div>
  );
}
