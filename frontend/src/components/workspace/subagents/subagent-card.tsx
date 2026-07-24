"use client";

import { PencilIcon, Trash2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { type Subagent, useDeleteSubagent } from "@/core/subagents";

interface SubagentCardProps {
  subagent: Subagent;
}

export function SubagentCard({ subagent }: SubagentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const del = useDeleteSubagent();

  function handleEdit() {
    router.push(`/workspace/subagents/${encodeURIComponent(subagent.name)}`);
  }

  async function handleDelete() {
    try {
      await del.mutateAsync(subagent.name);
      toast.success(t.subagents.deleted);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirmOpen(false);
    }
  }

  return (
    <Card className="group flex flex-col transition-shadow hover:shadow-md">
      <CardHeader className="pb-3">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">
              <span className="font-mono">[{subagent.name}]</span>
            </CardTitle>
            <CardDescription className="mt-2 line-clamp-2 text-sm">
              {subagent.description}
            </CardDescription>
          </div>
          <Badge variant={subagent.readonly ? "secondary" : "default"}>
            {subagent.source}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <div className="text-muted-foreground text-xs">
          {t.subagents.model}: {subagent.model} · {t.subagents.maxTurns}:{" "}
          {subagent.max_turns}
        </div>
      </CardContent>
      <CardFooter className="mt-auto gap-2 pt-3">
        {!subagent.readonly && (
          <>
            <Button variant="outline" size="sm" onClick={handleEdit}>
              <PencilIcon className="mr-1.5 h-4 w-4" />
              {t.common.edit}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmOpen(true)}
            >
              <Trash2Icon className="mr-1.5 h-4 w-4" />
              {t.common.delete}
            </Button>
          </>
        )}
      </CardFooter>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.subagents.deleteConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.subagents.deleteConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={del.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={del.isPending}
            >
              {del.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
