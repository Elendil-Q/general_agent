"use client";

import { FileIcon } from "lucide-react";
import { useCallback } from "react";

import { useDirectoryListing } from "@/core/files/hooks";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts/context";

import { FileTree } from "./file-tree";

interface FileBrowserPanelProps {
  threadId: string;
  className?: string;
}

export function FileBrowserPanel({
  threadId,
  className,
}: FileBrowserPanelProps) {
  const { data: rootListing, isLoading } = useDirectoryListing(threadId);
  const {
    select: selectArtifact,
    setOpen: setArtifactsOpen,
    selectedArtifact,
  } = useArtifacts();

  const handleFileSelect = useCallback(
    (path: string) => {
      selectArtifact(path, false);
      setArtifactsOpen(true);
    },
    [selectArtifact, setArtifactsOpen],
  );

  return (
    <div className={cn("flex h-full flex-col overflow-hidden", className)}>
      {/* Header */}
      <div className="flex items-center border-b px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <FileIcon className="size-4" />
          Files
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto py-1">
        {rootListing && rootListing.entries.length > 0 ? (
          <FileTree
            threadId={threadId}
            entries={rootListing.entries}
            parentPath={null}
            onFileSelect={handleFileSelect}
            selectedFile={selectedArtifact}
          />
        ) : isLoading ? (
          <div className="text-muted-foreground flex items-center justify-center p-4 text-sm">
            Loading...
          </div>
        ) : (
          <div className="text-muted-foreground flex items-center justify-center p-4 text-sm">
            No files found
          </div>
        )}
      </div>
    </div>
  );
}
