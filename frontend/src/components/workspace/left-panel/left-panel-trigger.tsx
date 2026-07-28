"use client";

import { PanelLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { useLeftPanel } from "./context";

export function LeftPanelTrigger() {
  const { t } = useI18n();
  const { open, toggle } = useLeftPanel();

  return (
    <Tooltip content={open ? t.leftPanel.hide : t.leftPanel.show}>
      <Button
        className={cn(
          "text-muted-foreground hover:text-foreground",
          open && "bg-accent text-foreground",
        )}
        variant="ghost"
        size="icon-sm"
        onClick={toggle}
      >
        <PanelLeftIcon />
      </Button>
    </Tooltip>
  );
}
