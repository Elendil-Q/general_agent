"use client";

import { ArrowLeftIcon, ChevronDownIcon, CopyIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  SubagentNameCheckError,
  checkSubagentName,
  useCreateSubagent,
  useSubagentCatalogs,
  useUpdateSubagent,
  type CreateSubagentRequest,
  type Subagent,
  type UpdateSubagentRequest,
} from "@/core/subagents";

type Mode = "create" | "edit";

type NameStatus = "idle" | "checking" | "available" | "taken" | "invalid";

interface SubagentFormState {
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  tools: string[];
  skills: string[];
  skills_on_demand: string[];
  max_turns: number;
  timeout_seconds: number;
}

interface CheckboxItem {
  value: string;
  label: string;
  description: string | null;
}

interface SubagentDesignerFormProps {
  mode: Mode;
  initial?: Subagent | null;
  isClone?: boolean;
}

const NAME_PATTERN = /^[A-Za-z0-9-]+$/;

const DEFAULT_STATE: SubagentFormState = {
  name: "",
  description: "",
  system_prompt: "",
  model: "inherit",
  tools: [],
  skills: [],
  skills_on_demand: [],
  max_turns: 50,
  timeout_seconds: 900,
};

const MIN_MAX_TURNS = 1;
const MAX_MAX_TURNS = 500;
const MIN_TIMEOUT = 60;
const MAX_TIMEOUT = 7200;

function stateFromSubagent(
  subagent: Subagent,
  isClone: boolean,
): SubagentFormState {
  return {
    name: isClone ? "" : subagent.name,
    description: subagent.description ?? "",
    system_prompt: subagent.system_prompt ?? "",
    model: subagent.model,
    tools: subagent.tools ?? [],
    skills: subagent.skills ?? [],
    skills_on_demand: subagent.skills_on_demand ?? [],
    max_turns: subagent.max_turns,
    timeout_seconds: subagent.timeout_seconds,
  };
}

function clampInt(
  raw: string,
  min: number,
  max: number,
  fallback: number,
): number {
  const n = Number.parseInt(raw, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function yamlQuote(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function yamlArray(values: string[]): string {
  if (values.length === 0) return "[]";
  return `[${values.map(yamlQuote).join(", ")}]`;
}

function buildYaml(form: SubagentFormState): string {
  const lines: string[] = [];
  const name = form.name.trim();
  lines.push(`name: ${name ? yamlQuote(name) : '""'}`);
  lines.push(
    `description: ${form.description.trim() ? yamlQuote(form.description) : '""'}`,
  );
  const prompt = form.system_prompt.trim();
  if (prompt) {
    lines.push("system_prompt: |-");
    for (const line of form.system_prompt.split("\n")) lines.push(`  ${line}`);
  } else {
    lines.push("system_prompt: null");
  }
  lines.push(`model: ${form.model || "inherit"}`);
  lines.push(`tools: ${form.tools.length ? yamlArray(form.tools) : "null"}`);
  lines.push(`skills: ${yamlArray(form.skills)}`);
  lines.push(`skills_on_demand: ${yamlArray(form.skills_on_demand)}`);
  lines.push(`max_turns: ${form.max_turns}`);
  lines.push(`timeout_seconds: ${form.timeout_seconds}`);
  return lines.join("\n");
}

function CheckboxList({
  items,
  selected,
  onToggle,
  disabled,
  emptyHint,
}: {
  items: CheckboxItem[];
  selected: string[];
  onToggle: (value: string) => void;
  disabled?: boolean;
  emptyHint: string;
}) {
  if (items.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border p-3 text-sm">
        {emptyHint}
      </div>
    );
  }
  return (
    <div className="max-h-56 overflow-y-auto rounded-md border p-1">
      <div className="space-y-0.5">
        {items.map((item) => {
          const checked = selected.includes(item.value);
          return (
            <label
              key={item.value}
              className={
                "flex items-start gap-2.5 rounded px-2 py-1.5" +
                (disabled
                  ? " cursor-not-allowed"
                  : " hover:bg-accent cursor-pointer")
              }
            >
              <Checkbox
                checked={checked}
                onCheckedChange={() => onToggle(item.value)}
                disabled={disabled}
                className="mt-0.5"
              />
              <div className="min-w-0 space-y-0.5">
                <p className="text-sm leading-none font-medium">{item.label}</p>
                {item.description ? (
                  <p className="text-muted-foreground text-xs leading-snug">
                    {item.description}
                  </p>
                ) : null}
              </div>
            </label>
          );
        })}
      </div>
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="text-sm leading-none font-medium">{children}</label>;
}
export function SubagentDesignerForm({
  mode,
  initial,
  isClone = false,
}: SubagentDesignerFormProps) {
  const { t } = useI18n();
  const router = useRouter();
  const { catalogs } = useSubagentCatalogs();
  const create = useCreateSubagent();
  const update = useUpdateSubagent();

  const readOnly = mode === "edit" && initial?.readonly === true;
  const nameEditable = mode === "create";

  const [form, setForm] = useState<SubagentFormState>(() =>
    initial ? stateFromSubagent(initial, isClone) : DEFAULT_STATE,
  );
  const [nameStatus, setNameStatus] = useState<NameStatus>("idle");
  const [saving, setSaving] = useState(false);
  const initialized = useRef(!!initial);

  // When the subagent arrives asynchronously (edit / clone), seed once.
  useEffect(() => {
    if (initial && !initialized.current) {
      initialized.current = true;
      setForm(stateFromSubagent(initial, isClone));
    }
  }, [initial, isClone]);

  // Debounced name availability check (create mode only).
  useEffect(() => {
    if (!nameEditable) return;
    const name = form.name.trim();
    if (!name) {
      setNameStatus("idle");
      return;
    }
    if (!NAME_PATTERN.test(name)) {
      setNameStatus("invalid");
      return;
    }
    setNameStatus("checking");
    const handle = window.setTimeout(() => {
      void checkSubagentName(name)
        .then((res) => setNameStatus(res.available ? "available" : "taken"))
        .catch((err: unknown) => {
          if (
            err instanceof SubagentNameCheckError &&
            err.reason === "request_failed"
          ) {
            setNameStatus("invalid");
          } else {
            // Backend unreachable: don't block authoring.
            setNameStatus("idle");
          }
        });
    }, 400);
    return () => window.clearTimeout(handle);
  }, [form.name, nameEditable]);

  const setField = useCallback(
    <K extends keyof SubagentFormState>(
      key: K,
      value: SubagentFormState[K],
    ) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const toggleInArray = useCallback(
    (key: "tools" | "skills" | "skills_on_demand", value: string) => {
      setForm((prev) => {
        const has = prev[key].includes(value);
        return {
          ...prev,
          [key]: has
            ? prev[key].filter((v) => v !== value)
            : [...prev[key], value],
        };
      });
    },
    [],
  );

  const toolItems = useMemo<CheckboxItem[]>(
    () =>
      (catalogs?.tools ?? []).map((name) => ({
        value: name,
        label: name,
        description: null,
      })),
    [catalogs],
  );
  const skillItems = useMemo<CheckboxItem[]>(
    () =>
      (catalogs?.skills ?? []).map((s) => ({
        value: s.name,
        label: s.name,
        description: s.description,
      })),
    [catalogs],
  );

  const modelOptions = useMemo(() => {
    const list = [{ name: "inherit", label: "Inherit" }];
    for (const m of catalogs?.models ?? []) {
      if (!list.some((x) => x.name === m.name)) list.push(m);
    }
    if (
      form.model &&
      form.model !== "inherit" &&
      !list.some((x) => x.name === form.model)
    ) {
      list.push({ name: form.model, label: form.model });
    }
    return list;
  }, [catalogs, form.model]);

  const trimmedName = form.name.trim();
  const nameFormatValid = NAME_PATTERN.test(trimmedName);
  const createBlocked =
    !trimmedName ||
    !nameFormatValid ||
    nameStatus === "taken" ||
    nameStatus === "invalid" ||
    nameStatus === "checking";

  const nameHint = (() => {
    if (!nameEditable) return null;
    if (!trimmedName) return null;
    if (!nameFormatValid || nameStatus === "invalid") {
      return { ok: false, text: t.subagents.nameInvalid };
    }
    if (nameStatus === "checking") {
      return { ok: null, text: "…" };
    }
    if (nameStatus === "taken")
      return { ok: false, text: t.subagents.nameTaken };
    if (nameStatus === "available") return { ok: true, text: "✓" };
    return null;
  })();

  function handleClone() {
    if (!initial) return;
    router.push(
      `/workspace/subagents/new?clone=${encodeURIComponent(initial.name)}`,
    );
  }

  async function handleCreate() {
    if (createBlocked || saving) return;
    const name = trimmedName;
    setSaving(true);
    try {
      const request: CreateSubagentRequest = {
        name,
        description: form.description.trim(),
        system_prompt: form.system_prompt.trim() || null,
        model: form.model,
        tools: form.tools.length ? form.tools : null,
        skills: form.skills.length ? form.skills : null,
        skills_on_demand: form.skills_on_demand.length
          ? form.skills_on_demand
          : null,
        max_turns: form.max_turns,
        timeout_seconds: form.timeout_seconds,
      };
      const created = await create.mutateAsync(request);
      toast.success(t.subagents.saved);
      router.push(`/workspace/subagents/${encodeURIComponent(created.name)}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    if (!initial || saving) return;
    setSaving(true);
    try {
      const request: UpdateSubagentRequest = {
        description: form.description.trim(),
        system_prompt: form.system_prompt.trim() || null,
        model: form.model,
        tools: form.tools.length ? form.tools : null,
        skills: form.skills.length ? form.skills : null,
        skills_on_demand: form.skills_on_demand.length
          ? form.skills_on_demand
          : null,
        max_turns: form.max_turns,
        timeout_seconds: form.timeout_seconds,
      };
      await update.mutateAsync({ name: initial.name, request });
      toast.success(t.subagents.saved);
      router.push("/workspace/subagents");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const title =
    mode === "create"
      ? t.subagents.newSubagent
      : (initial?.name ?? t.subagents.title);
  const yaml = buildYaml(form);

  return (
    <div className="flex size-full flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push("/workspace/subagents")}
            aria-label={t.common.back}
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-xl font-semibold">{title}</h1>
            {readOnly ? (
              <p className="text-muted-foreground mt-0.5 text-sm">
                {t.subagents.description}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {readOnly ? (
            <Button onClick={handleClone}>
              <CopyIcon className="mr-1.5 h-4 w-4" />
              {t.subagents.clone}
            </Button>
          ) : mode === "create" ? (
            <Button onClick={handleCreate} disabled={createBlocked || saving}>
              {t.subagents.create}
            </Button>
          ) : (
            <Button onClick={handleSave} disabled={saving}>
              {t.common.save}
            </Button>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto grid max-w-5xl grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            {/* Name */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldName}</FieldLabel>
              <Input
                value={form.name}
                onChange={(e) => setField("name", e.target.value)}
                disabled={!nameEditable || saving}
                placeholder="my-subagent"
                aria-invalid={nameHint?.ok === false ? true : undefined}
              />
              {nameHint ? (
                <p
                  className={
                    "text-xs " +
                    (nameHint.ok === false
                      ? "text-destructive"
                      : nameHint.ok === true
                        ? "text-muted-foreground"
                        : "text-muted-foreground")
                  }
                >
                  {nameHint.text}
                </p>
              ) : null}
            </div>

            {/* Description */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldDescription}</FieldLabel>
              <Input
                value={form.description}
                onChange={(e) => setField("description", e.target.value)}
                disabled={readOnly || saving}
                placeholder="What does this subagent do?"
              />
            </div>

            {/* System prompt */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldSystemPrompt}</FieldLabel>
              <Textarea
                value={form.system_prompt}
                onChange={(e) => setField("system_prompt", e.target.value)}
                disabled={readOnly || saving}
                rows={6}
                className="font-mono text-sm"
                placeholder="You are a meticulous researcher…"
              />
            </div>

            {/* Model */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldModel}</FieldLabel>
              <Select
                value={form.model}
                onValueChange={(v) => setField("model", v)}
                disabled={readOnly || saving}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((m) => (
                    <SelectItem key={m.name} value={m.name}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Tools */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldTools}</FieldLabel>
              <CheckboxList
                items={toolItems}
                selected={form.tools}
                onToggle={(v) => toggleInArray("tools", v)}
                disabled={readOnly || saving}
                emptyHint={t.common.loading}
              />
            </div>

            {/* Skills */}
            <div className="space-y-2">
              <FieldLabel>{t.subagents.fieldSkills}</FieldLabel>
              <CheckboxList
                items={skillItems}
                selected={form.skills}
                onToggle={(v) => toggleInArray("skills", v)}
                disabled={readOnly || saving}
                emptyHint={t.common.loading}
              />
            </div>

            {/* Advanced: skills on demand */}
            <Collapsible>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" className="w-full justify-start">
                  <ChevronDownIcon className="mr-1.5 h-4 w-4" />
                  {t.subagents.advanced}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-2 pt-4">
                <FieldLabel>{t.subagents.fieldSkillsOnDemand}</FieldLabel>
                <CheckboxList
                  items={skillItems}
                  selected={form.skills_on_demand}
                  onToggle={(v) => toggleInArray("skills_on_demand", v)}
                  disabled={readOnly || saving}
                  emptyHint={t.common.loading}
                />
              </CollapsibleContent>
            </Collapsible>

            {/* Limits */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <FieldLabel>{t.subagents.fieldMaxTurns}</FieldLabel>
                <Input
                  type="number"
                  min={MIN_MAX_TURNS}
                  max={MAX_MAX_TURNS}
                  value={form.max_turns}
                  onChange={(e) =>
                    setField(
                      "max_turns",
                      clampInt(
                        e.target.value,
                        MIN_MAX_TURNS,
                        MAX_MAX_TURNS,
                        form.max_turns,
                      ),
                    )
                  }
                  disabled={readOnly || saving}
                />
              </div>
              <div className="space-y-2">
                <FieldLabel>{t.subagents.fieldTimeout}</FieldLabel>
                <Input
                  type="number"
                  min={MIN_TIMEOUT}
                  max={MAX_TIMEOUT}
                  value={form.timeout_seconds}
                  onChange={(e) =>
                    setField(
                      "timeout_seconds",
                      clampInt(
                        e.target.value,
                        MIN_TIMEOUT,
                        MAX_TIMEOUT,
                        form.timeout_seconds,
                      ),
                    )
                  }
                  disabled={readOnly || saving}
                />
              </div>
            </div>
          </div>

          {/* YAML preview */}
          <div className="lg:col-span-1">
            <div className="space-y-2 lg:sticky lg:top-0">
              <FieldLabel>{t.subagents.yamlPreview}</FieldLabel>
              <pre className="bg-muted text-muted-foreground overflow-x-auto rounded-lg p-4 text-xs leading-relaxed">
                {yaml}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
