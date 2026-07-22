"use client";

import { FolderTreeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";

import { useFileBrowser } from "./context";

export function FileBrowserTrigger() {
  const { toggle } = useFileBrowser();

  return (
    <Tooltip content="Browse files">
      <Button
        className="text-muted-foreground hover:text-foreground"
        variant="ghost"
        size="icon-sm"
        onClick={toggle}
      >
        <FolderTreeIcon />
      </Button>
    </Tooltip>
  );
}
