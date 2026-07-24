"use client";

import { Loader2, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ThreadChannelBadge,
  ThreadChannelIcon,
} from "@/components/workspace/thread-channel-source";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { useBulkDeleteThreads, useInfiniteThreads } from "@/core/threads/hooks";
import {
  channelSourceOfThread,
  pathOfThread,
  titleOfThread,
} from "@/core/threads/utils";
import { formatTimeAgo } from "@/core/utils/datetime";

export default function ChatsPage() {
  const { t } = useI18n();
  const {
    data: infiniteThreads,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteThreads();
  const threads = useMemo(
    () => infiniteThreads?.pages.flat() ?? [],
    [infiniteThreads],
  );
  const [search, setSearch] = useState("");
  const isSearching = search.trim().length > 0;

  // Bulk selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const { mutate: bulkDelete, isPending: isBulkDeleting } =
    useBulkDeleteThreads();

  useEffect(() => {
    document.title = `${t.pages.chats} - ${t.pages.appName}`;
  }, [t.pages.chats, t.pages.appName]);

  const filteredThreads = useMemo(() => {
    return threads.filter((thread) => {
      return titleOfThread(thread).toLowerCase().includes(search.toLowerCase());
    });
  }, [threads, search]);

  const filteredIds = useMemo(
    () => filteredThreads.map((t) => t.thread_id),
    [filteredThreads],
  );

  const allFilteredSelected =
    filteredIds.length > 0 && filteredIds.every((id) => selectedIds.has(id));
  const someFilteredSelected =
    !allFilteredSelected && filteredIds.some((id) => selectedIds.has(id));

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (allFilteredSelected) {
        const next = new Set(prev);
        for (const id of filteredIds) {
          next.delete(id);
        }
        return next;
      }
      return new Set([...prev, ...filteredIds]);
    });
  }, [allFilteredSelected, filteredIds]);

  const toggleSelect = useCallback((threadId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleConfirmDelete = useCallback(() => {
    const idsToDelete = Array.from(selectedIds);
    bulkDelete(
      { threadIds: idsToDelete },
      {
        onSuccess({ succeeded, failed, total }) {
          if (failed.length > 0) {
            if (succeeded.length > 0) {
              toast.warning(t.chats.deletePartialFailure(failed.length, total));
            } else {
              toast.error(t.chats.deletePartialFailure(failed.length, total));
            }
          } else {
            toast.success(t.chats.deleteSuccess(succeeded.length));
          }
          setSelectedIds(new Set());
          setDeleteDialogOpen(false);
        },
        onError() {
          toast.error(
            t.chats.deletePartialFailure(
              idsToDelete.length,
              idsToDelete.length,
            ),
          );
          setDeleteDialogOpen(false);
        },
      },
    );
  }, [bulkDelete, selectedIds, t]);

  // Sentinel-based auto load-more for the unfiltered list (issue #3482).
  // In search mode we deliberately do NOT auto-paginate, otherwise an empty
  // filtered view would keep the sentinel in the viewport and drain the
  // entire backend list one page at a time.  Searching falls back to an
  // explicit button so users can still reach older conversations on demand.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasNextPage || isSearching) {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px 0px 200px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, isSearching]);

  const hasSelection = selectedIds.size > 0;

  return (
    <WorkspaceContainer>
      <WorkspaceHeader></WorkspaceHeader>
      <WorkspaceBody>
        <div className="flex size-full flex-col">
          <header className="flex shrink-0 items-center justify-center pt-8">
            <Input
              type="search"
              className="h-12 w-full max-w-(--container-width-md) text-xl"
              placeholder={t.chats.searchChats}
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </header>
          {/* Select-all toolbar */}
          <div className="mx-auto flex w-full max-w-(--container-width-md) shrink-0 items-center gap-2 px-4 py-2">
            <Checkbox
              checked={allFilteredSelected}
              indeterminate={someFilteredSelected}
              onCheckedChange={toggleSelectAll}
              aria-label={t.chats.selectAll}
              data-testid="chats-select-all"
            />
            <span className="text-muted-foreground text-sm">
              {hasSelection
                ? t.chats.selectedCount(selectedIds.size)
                : t.chats.selectAll}
            </span>
          </div>
          <main className="min-h-0 flex-1">
            <ScrollArea className="size-full py-4">
              <div className="mx-auto flex size-full max-w-(--container-width-md) flex-col">
                {filteredThreads.map((thread) => {
                  const channelSource = channelSourceOfThread(thread);
                  const isSelected = selectedIds.has(thread.thread_id);
                  return (
                    <div
                      key={thread.thread_id}
                      className={`hover:bg-muted/50 flex items-start gap-2 border-b p-4 transition-colors ${isSelected ? "bg-muted/40" : ""}`}
                      data-testid="chats-thread-row"
                    >
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleSelect(thread.thread_id)}
                        aria-label={titleOfThread(thread)}
                        className="mt-0.5"
                        data-testid={`chats-thread-checkbox-${thread.thread_id}`}
                      />
                      <Link
                        href={pathOfThread(thread)}
                        className="flex min-w-0 flex-1 flex-col gap-2"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <ThreadChannelIcon source={channelSource} />
                          <div className="min-w-0 flex-1 truncate">
                            {titleOfThread(thread)}
                          </div>
                          <ThreadChannelBadge
                            source={channelSource}
                            className="hidden sm:inline-flex"
                          />
                        </div>
                        {thread.updated_at && (
                          <div className="text-muted-foreground text-sm">
                            {formatTimeAgo(thread.updated_at)}
                          </div>
                        )}
                      </Link>
                    </div>
                  );
                })}
                {hasNextPage && !isSearching && (
                  <div
                    ref={sentinelRef}
                    aria-hidden="true"
                    className="h-px w-full"
                    data-testid="chats-page-sentinel"
                  />
                )}
                {hasNextPage && isSearching && (
                  <div className="flex justify-center p-4">
                    <Button
                      variant="outline"
                      onClick={() => void fetchNextPage()}
                      disabled={isFetchingNextPage}
                      data-testid="chats-page-load-more"
                    >
                      {isFetchingNextPage
                        ? t.chats.loadingMore
                        : t.chats.loadMoreToSearch}
                    </Button>
                  </div>
                )}
              </div>
            </ScrollArea>
          </main>
          {/* Floating action bar when threads are selected */}
          {hasSelection && (
            <div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 mx-auto flex w-full max-w-(--container-width-md) shrink-0 items-center justify-between gap-2 border-t px-4 py-3 backdrop-blur">
              <span className="text-muted-foreground text-sm">
                {t.chats.selectedCount(selectedIds.size)}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={clearSelection}
                  data-testid="chats-cancel-selection"
                >
                  <X className="size-4" />
                  {t.chats.cancelSelection}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteDialogOpen(true)}
                  disabled={isBulkDeleting}
                  data-testid="chats-delete-selected"
                >
                  <Trash2 className="size-4" />
                  {t.chats.deleteSelected}
                </Button>
              </div>
            </div>
          )}
        </div>
      </WorkspaceBody>
      {/* Delete confirmation dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>{t.chats.deleteConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.chats.deleteConfirmDescription(selectedIds.size)}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={isBulkDeleting}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={isBulkDeleting}
              data-testid="chats-confirm-delete"
            >
              {isBulkDeleting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              {t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </WorkspaceContainer>
  );
}
