import { CheckCircleIcon, Loader2Icon, XCircleIcon } from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface WaitForTasksCardProps {
  className?: string;
  taskIds: string[];
  isLoading: boolean;
  result?: Record<string, { status: string; result?: unknown; error?: string }>;
}

export function WaitForTasksCard({
  className,
  taskIds,
  isLoading,
  result,
}: WaitForTasksCardProps) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);

  const statusIcon = useMemo(() => {
    if (isLoading) {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
    if (result) {
      const hasFailure = Object.values(result).some(
        (r) =>
          r.status === "failed" ||
          r.status === "pending" ||
          r.status === "unknown",
      );
      return hasFailure ? (
        <XCircleIcon className="size-3 text-red-500" />
      ) : (
        <CheckCircleIcon className="size-3" />
      );
    }
    return <Loader2Icon className="size-3 animate-spin" />;
  }, [isLoading, result]);

  const statusLabel = useMemo(() => {
    if (isLoading) {
      return (
        <Shimmer as="span" duration={3} spread={3}>
          {t.waitForTasks.collecting(taskIds.length)}
        </Shimmer>
      );
    }
    if (result) {
      return t.waitForTasks.collected;
    }
    return t.waitForTasks.collecting(taskIds.length);
  }, [isLoading, result, taskIds.length, t]);

  return (
    <ChainOfThought
      className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
      open={!collapsed}
    >
      <div className="bg-background/95 flex w-full flex-col rounded-lg">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={statusLabel}
                icon={<Loader2Icon />}
              />
              <div className="text-muted-foreground flex items-center gap-1 text-xs font-normal">
                {statusIcon}
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          <ChainOfThoughtStep label={t.waitForTasks.waitingFor}>
            <ul className="list-disc pl-4">
              {taskIds.map((tid) => {
                const r = result?.[tid];
                const status = r?.status ?? "waiting";
                return (
                  <li key={tid} className="text-sm">
                    <code className="text-xs">{tid}</code>
                    {result && (
                      <span
                        className={cn(
                          "ml-2",
                          status === "completed" && "text-green-600",
                          status === "failed" && "text-red-500",
                          status === "pending" && "text-yellow-600",
                          status === "unknown" && "text-red-500",
                        )}
                      >
                        — {status}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </ChainOfThoughtStep>
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
