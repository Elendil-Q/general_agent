"use client";

import { useSearchParams } from "next/navigation";

import { SubagentDesignerForm } from "@/components/workspace/subagents/subagent-designer-form";
import { useI18n } from "@/core/i18n/hooks";
import { useSubagent } from "@/core/subagents";

export default function NewSubagentPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const cloneName = searchParams.get("clone");
  const { subagent, isLoading } = useSubagent(cloneName);

  if (cloneName && isLoading) {
    return (
      <div className="text-muted-foreground flex size-full items-center justify-center text-sm">
        {t.common.loading}
      </div>
    );
  }

  return (
    <SubagentDesignerForm
      mode="create"
      initial={cloneName ? subagent : null}
      isClone={!!cloneName}
    />
  );
}
