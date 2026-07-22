import { fetch } from "../api/fetcher";

export interface FileEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size?: number | null;
  modified?: number | null;
  extension?: string | null;
}

export interface DirectoryListing {
  current_path: string;
  parent_path: string | null;
  entries: FileEntry[];
}

export async function listDirectory(
  threadId: string,
  virtualPath?: string,
): Promise<DirectoryListing> {
  const params = new URLSearchParams();
  if (virtualPath) {
    params.set("path", virtualPath);
  }
  const qs = params.toString();
  const url = `/api/threads/${threadId}/files/tree${qs ? `?${qs}` : ""}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `Failed to list directory: ${response.status} ${response.statusText}`,
    );
  }
  return response.json() as Promise<DirectoryListing>;
}
