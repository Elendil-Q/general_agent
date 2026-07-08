"use client";

import {
  EyeIcon,
  PencilIcon,
  SparklesIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

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
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Item,
  ItemActions,
  ItemTitle,
  ItemContent,
  ItemDescription,
} from "@/components/ui/item";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  useCustomSkill,
  useDeleteCustomSkill,
  useEnableSkill,
  useSkills,
  useUpdateCustomSkill,
  useUploadSkill,
  useSkillContent,
} from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

const MAX_SKILL_FILE_SIZE = 50 * 1024 * 1024; // 50MB

export function SkillSettingsPage({ onClose }: { onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { skills, isLoading, error } = useSkills();
  return (
    <SettingsSection
      title={t.settings.skills.title}
      description={t.settings.skills.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div>Error: {error.message}</div>
      ) : (
        <SkillSettingsList skills={skills} onClose={onClose} />
      )}
    </SettingsSection>
  );
}

function SkillSettingsList({
  skills,
  onClose,
}: {
  skills: Skill[];
  onClose?: () => void;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [filter, setFilter] = useState<string>("public");
  const [viewingSkill, setViewingSkill] = useState<Skill | null>(null);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [deletingSkill, setDeletingSkill] = useState<Skill | null>(null);
  const { mutate: enableSkill } = useEnableSkill();
  const { mutateAsync: uploadSkill } = useUploadSkill();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const filteredSkills = useMemo(
    () => skills.filter((skill) => skill.category === filter),
    [skills, filter],
  );
  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);
  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      if (!file.name.endsWith(".skill")) {
        toast.error(t.settings.skills.uploadError);
        return;
      }
      if (file.size > MAX_SKILL_FILE_SIZE) {
        toast.error(t.settings.skills.uploadError);
        return;
      }
      try {
        const result = await uploadSkill(file);
        if (result.success) {
          toast.success(t.settings.skills.uploadSuccess);
          setFilter("custom");
        } else {
          toast.error(result.message || t.settings.skills.uploadError);
        }
      } catch {
        toast.error(t.settings.skills.uploadError);
      }
    },
    [t.settings.skills, uploadSkill],
  );
  const handleCreateSkill = () => {
    onClose?.();
    router.push("/workspace/chats/new?mode=skill");
  };
  return (
    <>
      <div className="flex w-full flex-col gap-4">
        <header className="flex justify-between">
          <input
            ref={fileInputRef}
            type="file"
            accept=".skill"
            className="hidden"
            onChange={handleFileChange}
          />
          <div className="flex gap-2">
            <Tabs defaultValue="public" onValueChange={setFilter}>
              <TabsList variant="line">
                <TabsTrigger value="public">{t.common.public}</TabsTrigger>
                <TabsTrigger value="custom">{t.common.custom}</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleUploadClick}
              disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
            >
              <UploadIcon className="size-4" />
              {t.settings.skills.uploadSkill}
            </Button>
            <Button size="sm" onClick={handleCreateSkill}>
              <SparklesIcon className="size-4" />
              {t.settings.skills.createSkill}
            </Button>
          </div>
        </header>
        {filteredSkills.length === 0 && (
          <EmptySkill onCreateSkill={handleCreateSkill} />
        )}
        {filteredSkills.length > 0 &&
          filteredSkills.map((skill) => (
            <Item className="w-full" variant="outline" key={skill.name}>
              <ItemContent>
                <ItemTitle>
                  <div className="flex items-center gap-2">{skill.name}</div>
                </ItemTitle>
                <ItemDescription className="line-clamp-4">
                  {skill.description}
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <Switch
                  checked={skill.enabled}
                  disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
                  onCheckedChange={(checked) =>
                    enableSkill({ skillName: skill.name, enabled: checked })
                  }
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => setViewingSkill(skill)}
                >
                  <EyeIcon className="size-3.5" />
                </Button>
                {skill.category === "custom" && (
                  <div className="ml-1 flex gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setEditingSkill(skill)}
                    >
                      <PencilIcon className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setDeletingSkill(skill)}
                    >
                      <Trash2Icon className="size-3.5" />
                    </Button>
                  </div>
                )}
              </ItemActions>
            </Item>
          ))}
      </div>

      {editingSkill && (
        <SkillEditorSheet
          skill={editingSkill}
          open={!!editingSkill}
          onClose={() => setEditingSkill(null)}
        />
      )}

      {deletingSkill && (
        <Dialog
          open={!!deletingSkill}
          onOpenChange={(open) => !open && setDeletingSkill(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t.settings.skills.deleteConfirmTitle}</DialogTitle>
              <DialogDescription>
                {t.settings.skills.deleteConfirmDescription}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeletingSkill(null)}>
                {t.common.cancel}
              </Button>
              <SkillDeleteButton
                skill={deletingSkill}
                onDeleted={() => setDeletingSkill(null)}
              />
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {viewingSkill && (
        <SkillViewerSheet
          skill={viewingSkill}
          open={!!viewingSkill}
          onClose={() => setViewingSkill(null)}
        />
      )}
    </>
  );
}
function SkillViewerSheet({
  skill,
  open,
  onClose,
}: {
  skill: Skill;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { data, isLoading } = useSkillContent(open ? skill : null);

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent
        side="right"
        className="flex w-[600px] flex-col sm:max-w-[600px]"
      >
        <SheetHeader>
          <SheetTitle>{skill.name}</SheetTitle>
          <SheetDescription>
            {t.settings.skills.viewSkill} ({skill.category})
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 py-4">
          {isLoading ? (
            <div className="text-muted-foreground text-sm">
              {t.common.loading}
            </div>
          ) : (
            <Textarea
              readOnly
              className="field-sizing-fixed min-h-0 flex-1 resize-none overflow-y-auto font-mono text-sm"
              value={data?.content ?? ""}
            />
          )}
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t pt-4">
          <Button onClick={onClose}>{t.common.cancel}</Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function SkillDeleteButton({
  skill,
  onDeleted,
}: {
  skill: Skill;
  onDeleted: () => void;
}) {
  const { t } = useI18n();
  const { mutateAsync: deleteSkill } = useDeleteCustomSkill();
  const [pending, setPending] = useState(false);

  const handleDelete = useCallback(async () => {
    setPending(true);
    try {
      await deleteSkill(skill.name);
      toast.success(t.settings.skills.deleteSuccess);
      onDeleted();
    } catch {
      toast.error(t.settings.skills.deleteSkill);
    } finally {
      setPending(false);
    }
  }, [skill.name, deleteSkill, onDeleted, t]);

  return (
    <Button onClick={handleDelete} disabled={pending} variant="destructive">
      {pending ? t.common.loading : t.settings.skills.deleteSkill}
    </Button>
  );
}

function SkillEditorSheet({
  skill,
  open,
  onClose,
}: {
  skill: Skill;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { data, isLoading } = useCustomSkill(open ? skill.name : "");
  const { mutateAsync: updateSkill } = useUpdateCustomSkill();
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const initialized = useRef(false);

  useEffect(() => {
    if (data?.content && !initialized.current) {
      setContent(data.content);
      initialized.current = true;
    }
  }, [data?.content]);

  useEffect(() => {
    if (!open) {
      initialized.current = false;
      setContent("");
    }
  }, [open]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await updateSkill({ name: skill.name, content });
      toast.success(t.settings.skills.updateSuccess);
      onClose();
    } catch {
      toast.error(t.settings.skills.uploadError);
    } finally {
      setSaving(false);
    }
  }, [content, skill.name, updateSkill, onClose, t]);

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent
        side="right"
        className="flex w-[600px] flex-col sm:max-w-[600px]"
      >
        <SheetHeader>
          <SheetTitle>{skill.name}</SheetTitle>
          <SheetDescription>{t.settings.skills.editSkill}</SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 py-4">
          {isLoading ? (
            <div className="text-muted-foreground text-sm">
              {t.common.loading}
            </div>
          ) : (
            <Textarea
              className="field-sizing-fixed min-h-0 flex-1 resize-none overflow-y-auto font-mono text-sm"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="---
name: ...
description: ...
---

"
            />
          )}
        </div>

        <div className="flex shrink-0 justify-end gap-2 border-t pt-4">
          <Button variant="outline" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button onClick={handleSave} disabled={saving || isLoading}>
            {saving ? t.common.loading : t.settings.skills.saveSkill}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function EmptySkill({ onCreateSkill }: { onCreateSkill: () => void }) {
  const { t } = useI18n();
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SparklesIcon />
        </EmptyMedia>
        <EmptyTitle>{t.settings.skills.emptyTitle}</EmptyTitle>
        <EmptyDescription>
          {t.settings.skills.emptyDescription}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button onClick={onCreateSkill}>{t.settings.skills.emptyButton}</Button>
      </EmptyContent>
    </Empty>
  );
}
