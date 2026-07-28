import { useQuery } from "@tanstack/react-query";

import { listDirectory, type DirectoryListing } from "./api";

const ROOT_PLACEHOLDER: DirectoryListing = {
  current_path: "/mnt/user-data",
  parent_path: null,
  entries: [
    { name: "workspace", path: "/mnt/user-data/workspace", is_directory: true },
    { name: "uploads", path: "/mnt/user-data/uploads", is_directory: true },
    { name: "outputs", path: "/mnt/user-data/outputs", is_directory: true },
  ],
};

export function useDirectoryListing(
  threadId: string,
  virtualPath?: string,
  enabled?: boolean,
) {
  const isRoot = !virtualPath;

  return useQuery<DirectoryListing>({
    queryKey: ["files", "tree", threadId, virtualPath ?? ""],
    queryFn: () => listDirectory(threadId, virtualPath),
    enabled: enabled ?? !!threadId,
    staleTime: isRoot ? Infinity : 10_000,
    gcTime: isRoot ? 30 * 60 * 1000 : undefined,
    placeholderData: isRoot ? ROOT_PLACEHOLDER : undefined,
  });
}
