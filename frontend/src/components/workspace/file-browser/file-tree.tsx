"use client";

import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
  LoaderIcon,
} from "lucide-react";
import { useCallback, useState } from "react";

import type { FileEntry } from "@/core/files/api";
import { useDirectoryListing } from "@/core/files/hooks";
import { getFileIcon } from "@/core/utils/files";
import { cn } from "@/lib/utils";

const MAX_DEPTH = 10;

interface FileTreeProps {
  threadId: string;
  entries: FileEntry[];
  parentPath: string | null;
  onFileSelect: (path: string) => void;
  selectedFile?: string | null;
  className?: string;
  depth?: number;
}

export function FileTree({
  threadId,
  entries,
  parentPath: _parentPath,
  onFileSelect,
  selectedFile,
  className,
  depth = 0,
}: FileTreeProps) {
  if (depth > MAX_DEPTH) {
    return (
      <div className="text-muted-foreground px-2 py-1 text-xs">
        Max depth reached
      </div>
    );
  }

  return (
    <ul className={cn("space-y-0.5", className)}>
      {entries.map((entry) => (
        <FileTreeNode
          key={entry.path}
          entry={entry}
          threadId={threadId}
          onFileSelect={onFileSelect}
          selectedFile={selectedFile}
          depth={depth}
        />
      ))}
    </ul>
  );
}

function FileTreeNode({
  entry,
  threadId,
  onFileSelect,
  selectedFile,
  depth = 0,
}: {
  entry: FileEntry;
  threadId: string;
  onFileSelect: (path: string) => void;
  selectedFile?: string | null;
  depth?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, isError } = useDirectoryListing(
    threadId,
    entry.path,
    expanded, // Only fetch when expanded
  );

  const handleClick = useCallback(() => {
    if (entry.is_directory) {
      setExpanded((prev) => !prev);
    } else {
      onFileSelect(entry.path);
    }
  }, [entry.is_directory, entry.path, onFileSelect]);

  const isSelected = selectedFile === entry.path;

  return (
    <li>
      <button
        className={cn(
          "hover:bg-accent/50 flex w-full items-center gap-1.5 rounded-sm px-2 py-1 text-left text-sm",
          isSelected && "bg-accent text-accent-foreground",
        )}
        onClick={handleClick}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
        type="button"
      >
        {entry.is_directory ? (
          <>
            <ChevronRightIcon
              className={cn(
                "text-muted-foreground size-3.5 shrink-0 transition-transform",
                expanded && "rotate-90",
              )}
            />
            {expanded ? (
              <FolderOpenIcon className="text-muted-foreground size-4 shrink-0" />
            ) : (
              <FolderIcon className="text-muted-foreground size-4 shrink-0" />
            )}
          </>
        ) : (
          <>
            {/* Spacer to align with folder chevron */}
            <span className="w-3.5 shrink-0" />
            {getFileIcon(entry.name, "size-4 shrink-0 text-muted-foreground")}
          </>
        )}
        <span className="truncate">{entry.name}</span>
      </button>

      {entry.is_directory && expanded && (
        <div>
          {isLoading ? (
            <div className="text-muted-foreground flex items-center gap-2 px-2 py-1 text-xs">
              <LoaderIcon className="size-3 animate-spin" />
              Loading...
            </div>
          ) : isError ? (
            <div
              className="text-muted-foreground px-2 py-1 text-xs"
              style={{ paddingLeft: `${8 + (depth + 1) * 16 + 20}px` }}
            >
              Failed to load directory
            </div>
          ) : data?.entries && data.entries.length > 0 ? (
            <FileTree
              threadId={threadId}
              entries={data.entries}
              parentPath={entry.path}
              onFileSelect={onFileSelect}
              selectedFile={selectedFile}
              depth={depth + 1}
            />
          ) : (
            <div
              className="text-muted-foreground px-2 py-1 text-xs"
              style={{ paddingLeft: `${8 + (depth + 1) * 16 + 20}px` }}
            >
              Empty directory
            </div>
          )}
        </div>
      )}
    </li>
  );
}
