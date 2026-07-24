"use client";

import { BoxesIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { useSubagents } from "@/core/subagents";

import { SubagentCard } from "./subagent-card";

export function SubagentGallery() {
  const { t } = useI18n();
  const { subagents, isLoading } = useSubagents();
  const router = useRouter();

  const handleNew = () => router.push("/workspace/subagents/new");

  return (
    <div className="flex size-full flex-col">
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.subagents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.subagents.description}
          </p>
        </div>
        <Button onClick={handleNew}>
          <PlusIcon className="mr-1.5 h-4 w-4" />
          {t.subagents.newSubagent}
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
            {t.common.loading}
          </div>
        ) : subagents.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
            <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
              <BoxesIcon className="text-muted-foreground h-7 w-7" />
            </div>
            <div>
              <p className="font-medium">{t.subagents.emptyTitle}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {t.subagents.emptyDescription}
              </p>
            </div>
            <Button variant="outline" className="mt-2" onClick={handleNew}>
              <PlusIcon className="mr-1.5 h-4 w-4" />
              {t.subagents.newSubagent}
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {subagents.map((s) => (
              <SubagentCard key={s.name} subagent={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
