import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface FollowUpCardProps {
  className?: string;
  taskId: string;
  prompt: string;
  isLoading: boolean;
  resultText?: string;
  resultStatus?: "completed" | "failed" | "in_progress";
}

export function FollowUpCard({
  className,
  taskId,
  prompt,
  isLoading,
  resultText,
  resultStatus,
}: FollowUpCardProps) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);

  const icon = useMemo(() => {
    if (resultStatus === "completed") {
      return <CheckCircleIcon className="size-3" />;
    }
    if (resultStatus === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    }
    if (isLoading) {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
    return null;
  }, [resultStatus, isLoading]);

  const statusLabel = useMemo(() => {
    if (resultStatus === "completed") return t.subtasks.completed;
    if (resultStatus === "failed") return t.subtasks.failed;
    return t.subtasks.in_progress;
  }, [resultStatus, t]);

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
                className="min-w-0 flex-1 font-normal"
                label={
                  <span className="block truncate">
                    {t.followUp.label(taskId, prompt)}
                  </span>
                }
                icon={<ClipboardListIcon />}
              />
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      resultStatus === "failed"
                        ? "text-red-500 opacity-67"
                        : "",
                    )}
                  >
                    {icon}
                    <span>{statusLabel}</span>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          <ChainOfThoughtStep label={t.followUp.targetTask}>
            <code className="text-xs">{taskId}</code>
          </ChainOfThoughtStep>
          {prompt && (
            <ChainOfThoughtStep label={t.followUp.promptLabel}>
              <span className="text-sm">{prompt}</span>
            </ChainOfThoughtStep>
          )}
          {resultStatus === "completed" && resultText && (
            <ChainOfThoughtStep
              label={t.subtasks.completed}
              icon={<CheckCircleIcon className="size-4" />}
            >
              <span className="text-sm">{resultText}</span>
            </ChainOfThoughtStep>
          )}
          {resultStatus === "failed" && resultText && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{resultText}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            />
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
