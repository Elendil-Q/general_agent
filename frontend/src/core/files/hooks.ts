import { useQuery } from "@tanstack/react-query";

import { listDirectory, type DirectoryListing } from "./api";

export function useDirectoryListing(
  threadId: string,
  virtualPath?: string,
  enabled?: boolean,
) {
  return useQuery<DirectoryListing>({
    queryKey: ["files", "tree", threadId, virtualPath ?? ""],
    queryFn: () => listDirectory(threadId, virtualPath),
    enabled: enabled ?? !!threadId,
    staleTime: 10_000, // 10 seconds — directories can change during agent runs
  });
}
