"use client";

import { XIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import {
  type ClarificationInterruptRequest,
  buildMultiChoiceAnswer,
} from "@/core/threads/clarification";
import { cn } from "@/lib/utils";

interface ClarificationInlineFormProps {
  interrupt: ClarificationInterruptRequest;
  onSubmit: (answer: string) => void;
  /** Hide the form without answering; the paused run stays resumable. */
  onDismiss?: () => void;
  /** Disable submit while a resume is in-flight. */
  disabled?: boolean;
}

/**
 * Inline clarification form rendered at the end of the chat message stream
 * (NOT a floating Dialog). Driven by the latched `clarificationInterrupt` from
 * `useThreadStream`; submitting clears the latch, which unmounts this form.
 *
 * Renders single_choice (option buttons + "other" input), multi_choice
 * (checkboxes + supplement), or text (free-form input). single_choice and
 * multi_choice always allow custom free-form input. The `free` interaction
 * never reaches here — it takes the legacy `goto=END` markdown path.
 */
export function ClarificationInlineForm({
  interrupt,
  onSubmit,
  onDismiss,
  disabled = false,
}: ClarificationInlineFormProps) {
  const { t } = useI18n();
  const [textValue, setTextValue] = useState("");
  const [extraValue, setExtraValue] = useState("");
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  // Reset internal state whenever a new interrupt arrives.
  useEffect(() => {
    setTextValue("");
    setExtraValue("");
    setSelectedOption(null);
    setChecked(new Set());
  }, [interrupt.tool_call_id]);

  const handleSubmit = useCallback(
    (answer: string) => {
      if (disabled || !answer.trim()) return;
      onSubmit(answer.trim());
    },
    [disabled, onSubmit],
  );

  const interaction = interrupt.interaction;
  const options = interrupt.options ?? [];

  const handleCheckToggle = useCallback((option: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(option)) {
        next.delete(option);
      } else {
        next.add(option);
      }
      return next;
    });
  }, []);

  const handleMultiSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (disabled) return;
      handleSubmit(buildMultiChoiceAnswer(checked, extraValue));
    },
    [checked, disabled, extraValue, handleSubmit],
  );

  const handleTextSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      handleSubmit(textValue);
    },
    [handleSubmit, textValue],
  );

  const handleOptionExtraSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      handleSubmit(extraValue);
    },
    [extraValue, handleSubmit],
  );

  return (
    <div className="border-border bg-muted/30 w-full rounded-2xl border p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="flex-1 text-sm whitespace-pre-wrap">
          {interrupt.question}
        </p>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground -mt-1 -mr-1 shrink-0 rounded-md p-1 transition"
            aria-label={t.common.close}
          >
            <XIcon className="size-4" />
          </button>
        )}
      </div>

      <div className="mt-3 space-y-2">
        {/* single_choice: option buttons + always-on "other" input */}
        {interaction === "single_choice" && (
          <div className="space-y-2">
            {options.map((option) => (
              <button
                key={option}
                type="button"
                disabled={disabled}
                onClick={() => {
                  setSelectedOption(option);
                  handleSubmit(option);
                }}
                className={cn(
                  "w-full rounded-lg border px-4 py-2.5 text-left text-sm transition",
                  selectedOption === option
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border bg-background hover:bg-muted",
                  disabled && "pointer-events-none opacity-60",
                )}
              >
                {option}
              </button>
            ))}
            <form
              onSubmit={handleOptionExtraSubmit}
              className="flex gap-2 pt-1"
            >
              <Input
                type="text"
                value={extraValue}
                onChange={(e) => setExtraValue(e.target.value)}
                placeholder={t.clarification.other}
                disabled={disabled}
              />
              <Button type="submit" disabled={disabled || !extraValue.trim()}>
                {t.clarification.submit}
              </Button>
            </form>
          </div>
        )}

        {/* multi_choice: checkboxes + always-on supplement input */}
        {interaction === "multi_choice" && (
          <form onSubmit={handleMultiSubmit} className="space-y-2">
            {options.map((option) => (
              <label
                key={option}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-2.5 text-sm transition",
                  checked.has(option)
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border bg-background hover:bg-muted",
                  disabled && "pointer-events-none opacity-60",
                )}
              >
                <input
                  type="checkbox"
                  className="accent-primary size-4"
                  checked={checked.has(option)}
                  onChange={() => handleCheckToggle(option)}
                  disabled={disabled}
                />
                <span>{option}</span>
              </label>
            ))}
            <Input
              type="text"
              value={extraValue}
              onChange={(e) => setExtraValue(e.target.value)}
              placeholder={t.clarification.supplement}
              disabled={disabled}
            />
            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={
                  disabled || (checked.size === 0 && !extraValue.trim())
                }
              >
                {t.clarification.submit}
              </Button>
            </div>
          </form>
        )}

        {/* text: free-form input */}
        {interaction === "text" && (
          <form onSubmit={handleTextSubmit} className="flex gap-2">
            <Input
              type="text"
              value={textValue}
              onChange={(e) => setTextValue(e.target.value)}
              placeholder={t.clarification.fillPlaceholder}
              autoFocus
              disabled={disabled}
            />
            <Button type="submit" disabled={disabled || !textValue.trim()}>
              {t.clarification.submit}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
