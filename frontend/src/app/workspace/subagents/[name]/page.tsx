"use client";

import { use } from "react";

import { SubagentDesignerForm } from "@/components/workspace/subagents/subagent-designer-form";
import { useI18n } from "@/core/i18n/hooks";
import { useSubagent } from "@/core/subagents";

export default function EditSubagentPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { t } = useI18n();
  const { name } = use(params);
  const decodedName = decodeURIComponent(name);
  const { subagent, isLoading } = useSubagent(decodedName);

  if (isLoading) {
    return (
      <div className="text-muted-foreground flex size-full items-center justify-center text-sm">
        {t.common.loading}
      </div>
    );
  }

  if (!subagent) {
    return (
      <div className="text-muted-foreground flex size-full flex-col items-center justify-center gap-3 text-sm">
        <p>{t.subagents.title}</p>
        <button
          type="button"
          className="text-primary underline"
          onClick={() =>
            window.history.length > 1 ? window.history.back() : undefined
          }
        >
          {t.common.back}
        </button>
      </div>
    );
  }

  return <SubagentDesignerForm mode="edit" initial={subagent} />;
}
