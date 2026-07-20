"use client";

import type { ChatStatus } from "ai";
import {
  CheckIcon,
  GraduationCapIcon,
  type LucideIcon,
  LightbulbIcon,
  PaperclipIcon,
  PlusIcon,
  RotateCcwIcon,
  SparklesIcon,
  RocketIcon,
  WorkflowIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { fetch } from "@/core/api/fetcher";
import type { Chain } from "@/core/chains";
import type { ChainProgressSummary } from "@/core/chains";
import { useChainProgress, useChains } from "@/core/chains/hooks";
import { getBackendBaseURL } from "@/core/config";
import { useEffectiveEffortsConfig, EFFORT_ORDER } from "@/core/effort/hooks";
import type { EffortFlags } from "@/core/effort/types";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import type { Skill } from "@/core/skills";
import { useSkills } from "@/core/skills/hooks";
import { useSuggestionsConfig } from "@/core/suggestions/hooks";
import type { AgentThreadContext } from "@/core/threads";
import { textOfMessage } from "@/core/threads/utils";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
  ModelSelectorTrigger,
} from "../ai-elements/model-selector";
import { Suggestion, Suggestions } from "../ai-elements/suggestion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

import { EffortHoverGuide } from "./effort-hover-guide";
import { useThread } from "./messages/context";
import { Tooltip } from "./tooltip";

/**
 * The four built-in efforts are a closed set. Each effort maps to a bundle of
 * runtime flags fetched from the backend via `GET /api/efforts/config`.
 */
type InputEffort = "flash" | "thinking" | "pro" | "ultra";

/**
 * Map effort names to lucide components. All four built-in efforts have
 * hard-coded icons; the fallback {@link SparklesIcon} is defensive only.
 */
const EFFORT_ICONS: Record<InputEffort, LucideIcon> = {
  flash: ZapIcon,
  thinking: LightbulbIcon,
  pro: GraduationCapIcon,
  ultra: RocketIcon,
};

function getEffortIcon(effort: InputEffort): LucideIcon {
  return EFFORT_ICONS[effort] ?? SparklesIcon;
}

/**
 * i18n keys for the four built-in efforts. Keys are aligned to the internal
 * effort name (`thinking` → `thinkingEffort`) to avoid colliding with the
 * existing `reasoningEffort` key used by the reasoning-effort slider.
 */
type InputBoxStringKey =
  | "flashEffort"
  | "thinkingEffort"
  | "proEffort"
  | "ultraEffort";
type InputBoxDescriptionKey =
  | "flashEffortDescription"
  | "thinkingEffortDescription"
  | "proEffortDescription"
  | "ultraEffortDescription";

const EFFORT_I18N_LABEL: Record<InputEffort, InputBoxStringKey> = {
  flash: "flashEffort",
  thinking: "thinkingEffort",
  pro: "proEffort",
  ultra: "ultraEffort",
};
const EFFORT_I18N_DESCRIPTION: Record<InputEffort, InputBoxDescriptionKey> = {
  flash: "flashEffortDescription",
  thinking: "thinkingEffortDescription",
  pro: "proEffortDescription",
  ultra: "ultraEffortDescription",
};

function getEffortLabel(effort: InputEffort, t: Translations): string {
  const key = EFFORT_I18N_LABEL[effort];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return effort;
}

function getEffortDescription(effort: InputEffort, t: Translations): string {
  const key = EFFORT_I18N_DESCRIPTION[effort];
  if (key && t.inputBox[key]) {
    return t.inputBox[key];
  }
  return "";
}

const MAX_SKILL_SUGGESTIONS = 6;
const SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN =
  /\[(?:主题|来源|topic|source)\]/i;

function findSuggestionTemplatePlaceholder(text: string) {
  const match = SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN.exec(text);
  if (!match) {
    return null;
  }

  return {
    start: match.index,
    end: match.index + match[0].length,
  };
}

interface SlashCommand {
  name: string;
  description: string;
  category?: string;
  placeholder?: boolean;
}

function getSlashCommandQuery(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }
  const query = value.slice(1);
  if (query.includes("/") || /\s/.test(query)) {
    return null;
  }
  return query;
}

type ChainPickerMode = "chain" | "chain-resume";

/**
 * Detect a *bare* ``/chain`` or ``/chain-resume`` command (no ``:name``,
 * no additional text beyond optional trailing whitespace).
 *
 * Returns the picker mode to activate, or ``null`` when the input is not a
 * bare chain command. When active, a dedicated picker listing every
 * available/resumable chain is shown (instead of the fuzzy-filtered
 * autocomplete), and Enter is intercepted so the bare command is never sent
 * to the LLM.
 */
function getChainPickerMode(value: string): ChainPickerMode | null {
  const trimmed = value.trim();
  if (trimmed === "/chain") return "chain";
  if (trimmed === "/chain-resume") return "chain-resume";
  return null;
}

/**
 * True when *value* is a bare ``/chain`` or ``/chain-resume`` with no
 * ``:name``. Used as a submit guard so the bare command can never reach the
 * LLM (the user must pick a chain from the picker first).
 */
function isBareChainCommand(value: string): boolean {
  return getChainPickerMode(value) !== null;
}

function buildSlashCommands(skills: Skill[]): SlashCommand[] {
  const commands: SlashCommand[] = [];
  for (const skill of skills) {
    if (!skill.enabled) continue;
    commands.push({
      name: `skill:${skill.name}`,
      description: skill.description,
      category: skill.category,
    });
  }
  return commands;
}

function buildChainSlashCommands(chains: Chain[]): SlashCommand[] {
  // Chains become `/chain:<name>` slash commands. They are the first non-skill
  // command type in the web autocomplete - the generic SlashCommand interface
  // already supported this, it just had no non-skill source until now.
  if (chains.length > 0) {
    return chains.map((chain) => ({
      name: `chain:${chain.name}`,
      description: chain.description,
      category: "chain",
    }));
  }
  return [
    {
      name: "chain",
      description: "No chains available",
      category: "chain",
      placeholder: true,
    },
  ];
}
function buildChainResumeSlashCommands(
  progress: ChainProgressSummary[],
): SlashCommand[] {
  const resumable = progress.filter((entry) => entry.resumable);
  if (resumable.length > 0) {
    return resumable.map((entry) => ({
      name: `chain-resume:${entry.chain_name}`,
      description: `Resume ${entry.chain_name} (${entry.completed_count}/${entry.total_count})`,
      category: "chain-resume",
    }));
  }
  return [
    {
      name: "chain-resume",
      description: "No chains to resume",
      category: "chain-resume",
      placeholder: true,
    },
  ];
}

function getMatchingSlashCommands(
  commands: SlashCommand[],
  query: string,
): SlashCommand[] {
  const normalizedQuery = query.toLowerCase();

  return commands
    .map((command, index) => ({
      command,
      index,
      name: command.name.toLowerCase(),
    }))
    .filter(({ name }) => {
      return !normalizedQuery || name.includes(normalizedQuery);
    })
    .sort((a, b) => {
      const aStartsWith = a.name.startsWith(normalizedQuery);
      const bStartsWith = b.name.startsWith(normalizedQuery);
      if (aStartsWith !== bStartsWith) {
        return aStartsWith ? -1 : 1;
      }
      return a.index - b.index;
    })
    .slice(0, MAX_SKILL_SUGGESTIONS)
    .map(({ command }) => command);
}

function getResolvedEffort(
  effort: InputEffort | undefined,
  supportsThinking: boolean,
  efforts: Record<InputEffort, EffortFlags>,
): InputEffort {
  // A non-thinking model cannot use an effort that requires thinking; fall
  // back to flash (the only built-in effort with thinking_enabled=false).
  if (!supportsThinking) {
    if (effort && !efforts[effort].thinking_enabled) {
      return effort;
    }
    return "flash";
  }
  // Thinking-capable model: honor an explicitly selected effort, else default
  // to "pro" (legacy behavior).
  if (effort) {
    return effort;
  }
  return "pro";
}

export function InputBox({
  className,
  disabled,
  autoFocus,
  status = "ready",
  context,
  extraHeader,
  isWelcomeMode,
  threadId,
  initialValue,
  onContextChange,
  onFollowupsVisibilityChange,
  onSubmit,
  onStop,
  ...props
}: Omit<ComponentProps<typeof PromptInput>, "onSubmit"> & {
  assistantId?: string | null;
  status?: ChatStatus;
  disabled?: boolean;
  context: Omit<
    AgentThreadContext,
    "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
  > & {
    effort: InputEffort | undefined;
    reasoning_effort?: "minimal" | "low" | "medium" | "high";
  };
  extraHeader?: React.ReactNode;
  /**
   * Whether to render the input in welcome layout (vertically centered,
   * with hero + quick action suggestions).  This is purely a visual flag,
   * decoupled from "the backend has created the thread" — see issue #2746.
   */
  isWelcomeMode?: boolean;
  threadId: string;
  initialValue?: string;
  onContextChange?: (
    context: Omit<
      AgentThreadContext,
      "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
    > & {
      effort: InputEffort | undefined;
      reasoning_effort?: "minimal" | "low" | "medium" | "high";
    },
  ) => void;
  onFollowupsVisibilityChange?: (visible: boolean) => void;
  onSubmit?: (message: PromptInputMessage) => void | Promise<void>;
  onStop?: () => void;
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const { models } = useModels();
  const { thread, isMock } = useThread();
  const { textInput } = usePromptInputController();
  const { skills } = useSkills();
  const { chains } = useChains();
  const { chainProgress } = useChainProgress(threadId);
  const promptRootRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const promptHistoryIndexRef = useRef<number | null>(null);
  const promptHistoryDraftRef = useRef("");

  const [followups, setFollowups] = useState<string[]>([]);
  const { efforts } = useEffectiveEffortsConfig();
  // The effort flags currently in effect (used to gate the reasoning-effort
  // picker on the active effort's `thinking_enabled`).
  const activeEffortFlags = context.effort
    ? efforts[context.effort]
    : efforts.flash;
  const { data: suggestionsConfig } = useSuggestionsConfig();
  const suggestionsConfigLoaded = suggestionsConfig !== undefined;
  const suggestionsEnabled = suggestionsConfig?.enabled;
  const [followupsHidden, setFollowupsHidden] = useState(false);
  const [followupsLoading, setFollowupsLoading] = useState(false);
  const [textareaFocused, setTextareaFocused] = useState(false);
  const [skillSuggestionIndex, setSkillSuggestionIndex] = useState(0);
  const [dismissedSkillSuggestionValue, setDismissedSkillSuggestionValue] =
    useState<string | null>(null);
  const lastGeneratedForAiIdRef = useRef<string | null>(null);
  const wasStreamingRef = useRef(false);
  const messagesRef = useRef(thread.messages);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingSuggestion, setPendingSuggestion] = useState<string | null>(
    null,
  );

  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    const currentModel = models.find((m) => m.name === context.model_name);
    const fallbackModel = currentModel ?? models[0]!;
    const supportsThinking = fallbackModel.supports_thinking ?? false;
    const nextModelName = fallbackModel.name;
    const nextEffort = getResolvedEffort(
      context.effort,
      supportsThinking,
      efforts,
    );

    if (context.model_name === nextModelName && context.effort === nextEffort) {
      return;
    }

    onContextChange?.({
      ...context,
      model_name: nextModelName,
      effort: nextEffort,
    });
  }, [context, models, onContextChange, efforts]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return models.find((m) => m.name === context.model_name) ?? models[0];
  }, [context.model_name, models]);

  const resolvedModelName = selectedModel?.name;

  const supportThinking = useMemo(
    () => selectedModel?.supports_thinking ?? false,
    [selectedModel],
  );

  const supportReasoningEffort = useMemo(
    () => selectedModel?.supports_reasoning_effort ?? false,
    [selectedModel],
  );

  const promptHistory = useMemo(() => {
    const history: string[] = [];
    for (const message of thread.messages) {
      if (message.type !== "human") {
        continue;
      }
      const additionalKwargs = message.additional_kwargs;
      if (
        additionalKwargs &&
        typeof additionalKwargs === "object" &&
        Reflect.get(additionalKwargs, "hide_from_ui") === true
      ) {
        continue;
      }
      const text = textOfMessage(message)?.trim();
      if (!text) {
        continue;
      }
      if (history.at(-1) !== text) {
        history.push(text);
      }
    }
    return history;
  }, [thread.messages]);

  useEffect(() => {
    promptHistoryIndexRef.current = null;
    promptHistoryDraftRef.current = "";
  }, [threadId]);

  useEffect(() => {
    const currentIndex = promptHistoryIndexRef.current;
    if (currentIndex !== null && currentIndex >= promptHistory.length) {
      promptHistoryIndexRef.current = null;
      promptHistoryDraftRef.current = "";
    }
  }, [promptHistory.length]);

  const handleModelSelect = useCallback(
    (model_name: string) => {
      const model = models.find((m) => m.name === model_name);
      if (!model) {
        return;
      }
      onContextChange?.({
        ...context,
        model_name,
        effort: getResolvedEffort(
          context.effort,
          model.supports_thinking ?? false,
          efforts,
        ),
        reasoning_effort: context.reasoning_effort,
      });
      setModelDialogOpen(false);
    },
    [onContextChange, context, models, efforts],
  );

  const handleEffortSelect = useCallback(
    (effort: InputEffort) => {
      const flags = efforts[effort];
      const resolvedEffort = getResolvedEffort(
        effort,
        supportThinking,
        efforts,
      );
      onContextChange?.({
        ...context,
        effort: resolvedEffort,
        reasoning_effort:
          flags.reasoning_effort ??
          (resolvedEffort === effort ? "minimal" : undefined),
      });
    },
    [onContextChange, context, supportThinking, efforts],
  );

  const handleReasoningEffortSelect = useCallback(
    (effort: "minimal" | "low" | "medium" | "high") => {
      onContextChange?.({
        ...context,
        reasoning_effort: effort,
      });
    },
    [onContextChange, context],
  );

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      if (status === "streaming") {
        onStop?.();
        return;
      }
      if (!message.text.trim() && message.files.length === 0) {
        return;
      }
      // Bare /chain or /chain-resume (no :name) must never reach the LLM -
      // the user is expected to pick a chain from the picker first.
      if (isBareChainCommand(message.text)) {
        return;
      }
      const placeholder = findSuggestionTemplatePlaceholder(message.text);
      if (placeholder) {
        toast.error(t.inputBox.suggestionPlaceholderRequired);
        requestAnimationFrame(() => {
          const textarea = textareaRef.current;
          if (!textarea) {
            return;
          }
          textarea.focus();
          textarea.setSelectionRange(placeholder.start, placeholder.end);
        });
        return Promise.reject(
          new Error("Suggestion template placeholder is unresolved."),
        );
      }
      promptHistoryIndexRef.current = null;
      promptHistoryDraftRef.current = "";
      setFollowups([]);
      setFollowupsHidden(false);
      setFollowupsLoading(false);

      // Guard against submitting before the initial model auto-selection
      // effect has flushed thread settings to storage/state.
      if (resolvedModelName && context.model_name !== resolvedModelName) {
        onContextChange?.({
          ...context,
          model_name: resolvedModelName,
          effort: getResolvedEffort(
            context.effort,
            selectedModel?.supports_thinking ?? false,
            efforts,
          ),
        });
        return new Promise<void>((resolve, reject) => {
          setTimeout(() => {
            Promise.resolve(onSubmit?.(message)).then(resolve).catch(reject);
          }, 0);
        });
      }

      return onSubmit?.(message);
    },
    [
      context,
      onContextChange,
      onSubmit,
      onStop,
      resolvedModelName,
      selectedModel?.supports_thinking,
      status,
      t.inputBox.suggestionPlaceholderRequired,
      efforts,
    ],
  );

  const requestFormSubmit = useCallback(() => {
    const form = promptRootRef.current?.querySelector("form");
    form?.requestSubmit();
  }, []);

  const handleFollowupClick = useCallback(
    (suggestion: string) => {
      if (status === "streaming") {
        return;
      }
      const current = (textInput.value ?? "").trim();
      if (current) {
        setPendingSuggestion(suggestion);
        setConfirmOpen(true);
        return;
      }
      textInput.setInput(suggestion);
      setFollowupsHidden(true);
      setTimeout(() => requestFormSubmit(), 0);
    },
    [requestFormSubmit, status, textInput],
  );

  const confirmReplaceAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    textInput.setInput(pendingSuggestion);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const confirmAppendAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    const current = (textInput.value ?? "").trim();
    const next = current
      ? `${current}\n${pendingSuggestion}`
      : pendingSuggestion;
    textInput.setInput(next);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const slashCommandQuery = useMemo(
    () => getSlashCommandQuery(textInput.value ?? ""),
    [textInput.value],
  );
  const chainPickerMode = useMemo(
    () => getChainPickerMode(textInput.value ?? ""),
    [textInput.value],
  );
  const slashCommands = useMemo(
    () => [
      ...buildSlashCommands(skills),
      ...buildChainResumeSlashCommands(chainProgress),
      ...buildChainSlashCommands(chains),
    ],
    [skills, chains, chainProgress],
  );
  const slashCommandSuggestions = useMemo(
    () =>
      slashCommandQuery === null
        ? []
        : getMatchingSlashCommands(slashCommands, slashCommandQuery),
    [slashCommands, slashCommandQuery],
  );
  // When the bare picker is active, show *every* available/resumable chain
  // (unfiltered) so the user can browse the full list rather than a
  // fuzzy-filtered subset.
  const chainPickerItems = useMemo<SlashCommand[]>(() => {
    if (chainPickerMode === "chain") return buildChainSlashCommands(chains);
    if (chainPickerMode === "chain-resume")
      return buildChainResumeSlashCommands(chainProgress);
    return [];
  }, [chainPickerMode, chains, chainProgress]);
  // Unified list: chain picker takes priority over the fuzzy filter.
  const activeSlashCommands =
    chainPickerMode !== null ? chainPickerItems : slashCommandSuggestions;
  const showSlashCommandSuggestions =
    !disabled &&
    textareaFocused &&
    (chainPickerMode !== null || slashCommandQuery !== null) &&
    activeSlashCommands.length > 0 &&
    dismissedSkillSuggestionValue !== textInput.value;
  // The bare picker shows even when empty so the user gets feedback that the
  // command was recognised but there is nothing to select.
  const showChainPickerEmpty =
    !disabled &&
    textareaFocused &&
    chainPickerMode !== null &&
    chainPickerItems.length === 0 &&
    dismissedSkillSuggestionValue !== textInput.value;

  useEffect(() => {
    setSkillSuggestionIndex(0);
  }, [
    slashCommandQuery,
    slashCommandSuggestions.length,
    chainPickerMode,
    chainPickerItems.length,
  ]);

  const applySlashCommandSuggestion = useCallback(
    (command: SlashCommand) => {
      if (command.placeholder) {
        toast.info(
          command.category === "chain"
            ? t.inputBox.chainPickerEmpty
            : t.inputBox.chainResumePickerEmpty,
        );
        setDismissedSkillSuggestionValue(textInput.value);
        return;
      }
      const nextValue = `/${command.name} `;
      textInput.setInput(nextValue);
      setDismissedSkillSuggestionValue(nextValue);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) {
          return;
        }
        textarea.focus();
        textarea.setSelectionRange(nextValue.length, nextValue.length);
      });
    },
    [textInput, t],
  );

  const handleSlashCommandSuggestionKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // The bare chain picker must intercept Enter even when the list is
      // empty (showChainPickerEmpty) so the bare command is never submitted
      // to the LLM. When there are items, Enter/Tab selects the highlighted
      // one; when empty, Enter is simply swallowed.
      if (!showSlashCommandSuggestions && !showChainPickerEmpty) {
        return;
      }

      if (event.key === "ArrowDown") {
        if (activeSlashCommands.length === 0) return;
        event.preventDefault();
        setSkillSuggestionIndex(
          (index) => (index + 1) % activeSlashCommands.length,
        );
        return;
      }

      if (event.key === "ArrowUp") {
        if (activeSlashCommands.length === 0) return;
        event.preventDefault();
        setSkillSuggestionIndex(
          (index) =>
            (index - 1 + activeSlashCommands.length) %
            activeSlashCommands.length,
        );
        return;
      }

      if (event.key === "Enter" || event.key === "Tab") {
        if (event.shiftKey) {
          return;
        }
        event.preventDefault();
        const selectedCommand = activeSlashCommands[skillSuggestionIndex];
        if (selectedCommand) {
          applySlashCommandSuggestion(selectedCommand);
        }
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        setDismissedSkillSuggestionValue(textInput.value);
      }
    },
    [
      applySlashCommandSuggestion,
      showSlashCommandSuggestions,
      showChainPickerEmpty,
      activeSlashCommands,
      skillSuggestionIndex,
      textInput.value,
    ],
  );

  const setPromptHistoryValue = useCallback(
    (value: string) => {
      textInput.setInput(value);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) {
          return;
        }
        textarea.focus();
        textarea.setSelectionRange(value.length, value.length);
      });
    },
    [textInput],
  );

  const handlePromptHistoryKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        isIMEComposing(event) ||
        promptHistory.length === 0 ||
        (event.key !== "ArrowUp" && event.key !== "ArrowDown")
      ) {
        return;
      }

      const currentValue = textInput.value ?? "";
      const currentHistoryIndex = promptHistoryIndexRef.current;
      const isBrowsingHistory = currentHistoryIndex !== null;

      if (!isBrowsingHistory && currentValue.length > 0) {
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        const nextIndex = isBrowsingHistory
          ? Math.max(currentHistoryIndex - 1, 0)
          : promptHistory.length - 1;
        if (!isBrowsingHistory) {
          promptHistoryDraftRef.current = currentValue;
        }
        promptHistoryIndexRef.current = nextIndex;
        setPromptHistoryValue(promptHistory[nextIndex] ?? "");
        return;
      }

      if (!isBrowsingHistory) {
        return;
      }

      event.preventDefault();
      if (currentHistoryIndex >= promptHistory.length - 1) {
        promptHistoryIndexRef.current = null;
        setPromptHistoryValue(promptHistoryDraftRef.current);
        promptHistoryDraftRef.current = "";
        return;
      }

      const nextIndex = currentHistoryIndex + 1;
      promptHistoryIndexRef.current = nextIndex;
      setPromptHistoryValue(promptHistory[nextIndex] ?? "");
    },
    [promptHistory, setPromptHistoryValue, textInput.value],
  );

  const handlePromptTextareaKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      handleSlashCommandSuggestionKeyDown(event);
      if (event.defaultPrevented) {
        return;
      }
      handlePromptHistoryKeyDown(event);
    },
    [handlePromptHistoryKeyDown, handleSlashCommandSuggestionKeyDown],
  );

  const handlePromptTextareaChange = useCallback(() => {
    promptHistoryIndexRef.current = null;
    promptHistoryDraftRef.current = "";
  }, []);

  const showFollowups =
    !disabled &&
    !isWelcomeMode &&
    !showSlashCommandSuggestions &&
    !followupsHidden &&
    (followupsLoading || followups.length > 0);

  useEffect(() => {
    onFollowupsVisibilityChange?.(showFollowups);
  }, [onFollowupsVisibilityChange, showFollowups]);

  useEffect(() => {
    return () => onFollowupsVisibilityChange?.(false);
  }, [onFollowupsVisibilityChange]);

  useEffect(() => {
    messagesRef.current = thread.messages;
  }, [thread.messages]);

  useEffect(() => {
    const streaming = status === "streaming";
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = streaming;
    if (!wasStreaming || streaming) {
      return;
    }

    if (disabled || isMock) {
      return;
    }

    const lastAi = [...messagesRef.current]
      .reverse()
      .find((m) => m.type === "ai");
    const lastAiId = lastAi?.id ?? null;
    if (!lastAiId || lastAiId === lastGeneratedForAiIdRef.current) {
      return;
    }
    if (!suggestionsConfigLoaded) {
      return;
    }
    lastGeneratedForAiIdRef.current = lastAiId;

    const recent = messagesRef.current
      .filter((m) => m.type === "human" || m.type === "ai")
      .filter((m) => !isHiddenFromUIMessage(m))
      .map((m) => {
        const role = m.type === "human" ? "user" : "assistant";
        const content = textOfMessage(m) ?? "";
        return { role, content };
      })
      .filter((m) => m.content.trim().length > 0)
      .slice(-6);

    if (recent.length === 0) {
      return;
    }

    if (!suggestionsEnabled) {
      setFollowups([]);
      return;
    }

    const controller = new AbortController();
    setFollowupsHidden(false);
    setFollowupsLoading(true);
    setFollowups([]);

    fetch(`${getBackendBaseURL()}/api/threads/${threadId}/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: recent,
        n: 3,
        model_name: context.model_name ?? undefined,
      }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          return { suggestions: [] as string[] };
        }
        return (await res.json()) as { suggestions?: string[] };
      })
      .then((data) => {
        const suggestions = (data.suggestions ?? [])
          .map((s) => (typeof s === "string" ? s.trim() : ""))
          .filter((s) => s.length > 0)
          .slice(0, 5);
        setFollowups(suggestions);
      })
      .catch(() => {
        setFollowups([]);
      })
      .finally(() => {
        setFollowupsLoading(false);
      });

    return () => controller.abort();
  }, [
    context.model_name,
    disabled,
    isMock,
    status,
    suggestionsConfigLoaded,
    suggestionsEnabled,
    threadId,
  ]);

  return (
    <div
      ref={promptRootRef}
      className={cn(
        "relative flex min-w-0 flex-col",
        isWelcomeMode ? "gap-4" : "gap-2",
      )}
    >
      {showFollowups && (
        <div className="flex items-center justify-center pb-1">
          <div className="flex items-center gap-2">
            {followupsLoading ? (
              <div className="text-muted-foreground bg-background/80 rounded-full border px-4 py-1.5 text-xs backdrop-blur-sm">
                {t.inputBox.followupLoading}
              </div>
            ) : (
              <Suggestions className="w-fit items-center">
                {followups.map((s) => (
                  <Suggestion
                    key={s}
                    className="py-1.5"
                    suggestion={s}
                    onClick={() => handleFollowupClick(s)}
                  />
                ))}
                <Button
                  aria-label={t.common.close}
                  className="text-muted-foreground h-auto cursor-pointer rounded-full px-2.5 py-1.5 text-xs font-normal"
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setFollowupsHidden(true)}
                >
                  <XIcon className="size-4" />
                </Button>
              </Suggestions>
            )}
          </div>
        </div>
      )}
      {(showSlashCommandSuggestions || showChainPickerEmpty) && (
        <div className="absolute right-0 bottom-full left-0 z-40 mb-2 px-1">
          <div
            aria-label="Slash command suggestions"
            className="bg-popover/95 text-popover-foreground border-border max-h-72 overflow-y-auto rounded-xl border p-1 shadow-lg backdrop-blur-sm"
            role="listbox"
          >
            {showChainPickerEmpty ? (
              <div
                className="text-muted-foreground flex min-h-12 items-center gap-3 rounded-lg px-3 py-2 text-sm"
                role="presentation"
              >
                {chainPickerMode === "chain-resume" ? (
                  <RotateCcwIcon className="size-4 shrink-0" />
                ) : (
                  <WorkflowIcon className="size-4 shrink-0" />
                )}
                <span>
                  {chainPickerMode === "chain-resume"
                    ? t.inputBox.chainResumePickerEmpty
                    : t.inputBox.chainPickerEmpty}
                </span>
              </div>
            ) : (
              activeSlashCommands.map((command, index) => {
                const selected = index === skillSuggestionIndex;
                return (
                  <button
                    aria-selected={selected}
                    className={cn(
                      "flex min-h-12 w-full min-w-0 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors",
                      selected
                        ? "bg-accent text-accent-foreground"
                        : "text-popover-foreground hover:bg-accent/70 hover:text-accent-foreground",
                    )}
                    key={command.name}
                    onClick={() => applySlashCommandSuggestion(command)}
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => setSkillSuggestionIndex(index)}
                    role="option"
                    type="button"
                  >
                    {command.category === "chain-resume" ? (
                      <RotateCcwIcon className="text-muted-foreground size-4 shrink-0" />
                    ) : command.category === "chain" ? (
                      <WorkflowIcon className="text-muted-foreground size-4 shrink-0" />
                    ) : (
                      <SparklesIcon className="text-muted-foreground size-4 shrink-0" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        /{command.name}
                      </span>
                      {command.description && (
                        <span className="text-muted-foreground block truncate text-xs">
                          {command.description}
                        </span>
                      )}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
      <PromptInput
        className={cn(
          "bg-background/85 rounded-2xl backdrop-blur-sm transition-all duration-300 ease-out *:data-[slot='input-group']:rounded-2xl",
          className,
        )}
        disabled={disabled}
        globalDrop
        multiple
        onSubmit={handleSubmit}
        {...props}
      >
        {extraHeader && (
          <div className="absolute top-0 right-0 left-0 z-10">
            <div className="absolute right-0 bottom-0 left-0 flex items-center justify-center">
              {extraHeader}
            </div>
          </div>
        )}
        <PromptInputAttachments>
          {(attachment) => <PromptInputAttachment data={attachment} />}
        </PromptInputAttachments>
        <PromptInputBody className="absolute top-0 right-0 left-0 z-3">
          <PromptInputTextarea
            className={cn("size-full")}
            disabled={disabled}
            placeholder={t.inputBox.placeholder}
            autoFocus={autoFocus}
            defaultValue={initialValue}
            onBlur={() => setTextareaFocused(false)}
            onChange={handlePromptTextareaChange}
            onFocus={() => setTextareaFocused(true)}
            onKeyDown={handlePromptTextareaKeyDown}
            ref={textareaRef}
          />
        </PromptInputBody>
        <PromptInputFooter className="flex flex-wrap gap-2 sm:flex-nowrap">
          <PromptInputTools className="min-w-0 flex-1 flex-wrap">
            {/* TODO: Add more connectors here
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger className="px-2!" />
            <PromptInputActionMenuContent>
              <PromptInputActionAddAttachments
                label={t.inputBox.addAttachments}
              />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu> */}
            <AddAttachmentsButton className="px-2!" />
            <PromptInputActionMenu>
              <EffortHoverGuide effort={context.effort ?? "flash"}>
                <PromptInputActionMenuTrigger className="max-w-28 gap-1! px-2! sm:max-w-none">
                  <div>
                    {(() => {
                      const effort = context.effort ?? "flash";
                      const Icon = getEffortIcon(effort);
                      const isUltra = effort === "ultra";
                      return (
                        <Icon
                          className={cn("size-3", isUltra && "text-[#dabb5e]")}
                        />
                      );
                    })()}
                  </div>
                  <div
                    className={cn(
                      "truncate text-xs font-normal",
                      context.effort === "ultra" ? "golden-text" : "",
                    )}
                  >
                    {context.effort ? getEffortLabel(context.effort, t) : ""}
                  </div>
                </PromptInputActionMenuTrigger>
              </EffortHoverGuide>
              <PromptInputActionMenuContent className="w-80">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-muted-foreground text-xs">
                    {t.inputBox.effort}
                  </DropdownMenuLabel>
                  <PromptInputActionMenu>
                    {EFFORT_ORDER.map((effort) => {
                      // Hide thinking-requiring efforts when the model cannot
                      // think (mirrors the legacy `supportThinking` gate).
                      if (
                        efforts[effort].thinking_enabled &&
                        !supportThinking
                      ) {
                        return null;
                      }
                      const isActive = context.effort === effort;
                      const Icon = getEffortIcon(effort);
                      const isUltra = effort === "ultra";
                      return (
                        <PromptInputActionMenuItem
                          key={effort}
                          className={cn(
                            isActive
                              ? "text-accent-foreground"
                              : "text-muted-foreground/65",
                          )}
                          onSelect={() => handleEffortSelect(effort)}
                        >
                          <div className="flex flex-col gap-2">
                            <div className="flex items-center gap-1 font-bold">
                              <Icon
                                className={cn(
                                  "mr-2 size-4",
                                  isUltra
                                    ? "text-[#dabb5e]"
                                    : isActive && "text-accent-foreground",
                                )}
                              />
                              <div className={cn(isUltra && "golden-text")}>
                                {getEffortLabel(effort, t)}
                              </div>
                            </div>
                            <div className="pl-7 text-xs">
                              {getEffortDescription(effort, t)}
                            </div>
                          </div>
                          {isActive ? (
                            <CheckIcon className="ml-auto size-4" />
                          ) : (
                            <div className="ml-auto size-4" />
                          )}
                        </PromptInputActionMenuItem>
                      );
                    })}
                  </PromptInputActionMenu>
                </DropdownMenuGroup>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
            {supportReasoningEffort && activeEffortFlags?.thinking_enabled && (
              <PromptInputActionMenu>
                <PromptInputActionMenuTrigger className="hidden gap-1! px-2! sm:inline-flex">
                  <div className="text-xs font-normal">
                    {t.inputBox.reasoningEffort}:
                    {context.reasoning_effort === "minimal" &&
                      " " + t.inputBox.reasoningEffortMinimal}
                    {context.reasoning_effort === "low" &&
                      " " + t.inputBox.reasoningEffortLow}
                    {context.reasoning_effort === "medium" &&
                      " " + t.inputBox.reasoningEffortMedium}
                    {context.reasoning_effort === "high" &&
                      " " + t.inputBox.reasoningEffortHigh}
                  </div>
                </PromptInputActionMenuTrigger>
                <PromptInputActionMenuContent className="w-70">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel className="text-muted-foreground text-xs">
                      {t.inputBox.reasoningEffort}
                    </DropdownMenuLabel>
                    <PromptInputActionMenu>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "minimal"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("minimal")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMinimal}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMinimalDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "minimal" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "low"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("low")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortLow}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortLowDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "low" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "medium" ||
                            !context.reasoning_effort
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("medium")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMedium}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMediumDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "medium" ||
                        !context.reasoning_effort ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "high"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("high")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortHigh}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortHighDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "high" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    </PromptInputActionMenu>
                  </DropdownMenuGroup>
                </PromptInputActionMenuContent>
              </PromptInputActionMenu>
            )}
          </PromptInputTools>
          <PromptInputTools className="min-w-0 justify-end">
            <ModelSelector
              open={modelDialogOpen}
              onOpenChange={setModelDialogOpen}
            >
              <ModelSelectorTrigger asChild>
                <PromptInputButton className="max-w-40 min-w-0 sm:max-w-56">
                  <div className="flex min-w-0 flex-col items-start text-left">
                    <ModelSelectorName className="text-xs font-normal">
                      {selectedModel?.display_name}
                    </ModelSelectorName>
                  </div>
                </PromptInputButton>
              </ModelSelectorTrigger>
              <ModelSelectorContent>
                <ModelSelectorInput placeholder={t.inputBox.searchModels} />
                <ModelSelectorList>
                  {models.map((m) => (
                    <ModelSelectorItem
                      key={m.name}
                      value={m.name}
                      onSelect={() => handleModelSelect(m.name)}
                    >
                      <div className="flex min-w-0 flex-1 flex-col">
                        <ModelSelectorName>{m.display_name}</ModelSelectorName>
                        <span className="text-muted-foreground truncate text-[10px]">
                          {m.model}
                        </span>
                      </div>
                      {m.name === context.model_name ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </ModelSelectorItem>
                  ))}
                </ModelSelectorList>
              </ModelSelectorContent>
            </ModelSelector>
            <PromptInputSubmit
              className="rounded-full"
              disabled={disabled}
              variant="outline"
              status={status}
            />
          </PromptInputTools>
        </PromptInputFooter>
        {!isWelcomeMode && (
          <div className="bg-background absolute right-0 -bottom-[17px] left-0 z-0 h-4"></div>
        )}
      </PromptInput>

      {isWelcomeMode &&
        searchParams.get("mode") !== "skill" &&
        !showSlashCommandSuggestions && (
          <div className="flex items-center justify-center pt-2">
            <SuggestionList textareaRef={textareaRef} />
          </div>
        )}

      <p className="text-muted-foreground/60 pointer-events-none relative z-10 self-end pt-0.5 pr-1 text-right text-xs leading-tight">
        {t.inputBox.disclaimer}
      </p>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.inputBox.followupConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.inputBox.followupConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button variant="secondary" onClick={confirmAppendAndSend}>
              {t.inputBox.followupConfirmAppend}
            </Button>
            <Button onClick={confirmReplaceAndSend}>
              {t.inputBox.followupConfirmReplace}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SuggestionList({
  textareaRef,
}: {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const handleSuggestionClick = useCallback(
    (prompt: string | undefined) => {
      if (!prompt) return;
      textInput.setInput(prompt);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        const placeholder = findSuggestionTemplatePlaceholder(prompt);
        if (textarea && placeholder) {
          textarea.focus();
          textarea.setSelectionRange(placeholder.start, placeholder.end);
        }
      });
    },
    [textareaRef, textInput],
  );
  return (
    <Suggestions className="min-h-16 w-full max-w-full justify-center px-4 sm:w-fit sm:px-0">
      <Button
        className="text-muted-foreground cursor-pointer rounded-full px-4 text-xs font-normal"
        variant="outline"
        size="sm"
        onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
      >
        <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
      </Button>
      {t.inputBox.suggestions.map((suggestion) => (
        <Suggestion
          key={suggestion.suggestion}
          icon={suggestion.icon}
          suggestion={suggestion.suggestion}
          onClick={() => handleSuggestionClick(suggestion.prompt)}
        />
      ))}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Suggestion icon={PlusIcon} suggestion={t.common.create} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuGroup>
            {t.inputBox.suggestionsCreate.map((suggestion, index) =>
              "type" in suggestion && suggestion.type === "separator" ? (
                <DropdownMenuSeparator key={index} />
              ) : (
                !("type" in suggestion) && (
                  <DropdownMenuItem
                    key={suggestion.suggestion}
                    onClick={() => handleSuggestionClick(suggestion.prompt)}
                  >
                    {suggestion.icon && <suggestion.icon className="size-4" />}
                    {suggestion.suggestion}
                  </DropdownMenuItem>
                )
              ),
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </Suggestions>
  );
}

function AddAttachmentsButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  return (
    <Tooltip content={t.inputBox.addAttachments}>
      <PromptInputButton
        className={cn("px-2!", className)}
        onClick={() => attachments.openFileDialog()}
      >
        <PaperclipIcon className="size-3" />
      </PromptInputButton>
    </Tooltip>
  );
}
