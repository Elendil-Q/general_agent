"use client";

import { CheckIcon, ClipboardIcon, FileTextIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { useThreadSystemPrompt } from "@/core/threads/hooks";
interface SystemPromptTriggerProps {
  threadId?: string | null;
}

export function SystemPromptTrigger({ threadId }: SystemPromptTriggerProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data, isLoading } = useThreadSystemPrompt(threadId, {
    enabled: open && Boolean(threadId),
  });

  const handleCopy = useCallback(async () => {
    if (!data?.system_prompt) return;
    try {
      await navigator.clipboard.writeText(data.system_prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard write failed — silently ignore
    }
  }, [data?.system_prompt]);

  const metadataItems = useMemo(() => {
    if (!data) return [];
    const items: { label: string; value: string }[] = [];
    if (data.caller) {
      items.push({ label: t.systemPrompt.caller, value: data.caller });
    }
    if (data.model_name) {
      items.push({ label: t.systemPrompt.model, value: data.model_name });
    }
    if (data.captured_at) {
      items.push({
        label: t.systemPrompt.capturedAt,
        value: new Date(data.captured_at).toLocaleString(),
      });
    }
    if (data.llm_call_index != null) {
      items.push({
        label: t.systemPrompt.callIndex,
        value: `#${data.llm_call_index}`,
      });
    }
    return items;
  }, [data, t]);

  if (!threadId) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        type="button"
        variant="ghost"
        className="text-muted-foreground hover:text-foreground"
        onClick={() => setOpen(true)}
      >
        <FileTextIcon />
        {t.systemPrompt.label}
      </Button>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t.systemPrompt.title}</DialogTitle>
        </DialogHeader>

        {isLoading && (
          <div className="text-muted-foreground py-8 text-center text-sm">
            Loading...
          </div>
        )}

        {!isLoading && (
          <>
            {/* Metadata bar */}
            {metadataItems.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                {metadataItems.map((item) => (
                  <span
                    key={item.label}
                    className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
                  >
                    <span className="font-medium">{item.label}:</span>
                    <span>{item.value}</span>
                  </span>
                ))}
              </div>
            )}

            {/* System prompt body */}
            {data?.system_prompt ? (
              <div className="relative">
                <div className="absolute top-2 right-2 z-10 flex gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={handleCopy}
                  >
                    {copied ? (
                      <>
                        <CheckIcon className="size-3" />
                        {t.systemPrompt.copied}
                      </>
                    ) : (
                      <>
                        <ClipboardIcon className="size-3" />
                        {t.systemPrompt.copy}
                      </>
                    )}
                  </Button>
                </div>
                <div className="max-h-96 overflow-auto rounded-md border">
                  <pre className="text-muted-foreground p-4 text-xs leading-relaxed whitespace-pre-wrap">
                    {data.system_prompt}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="text-muted-foreground py-8 text-center text-sm">
                {t.systemPrompt.triggerHint}
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
