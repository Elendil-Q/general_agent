import { useParams, usePathname } from "next/navigation";

const CHATS_PATH_SEGMENT = "chats";

export function resolveThreadIdFromPathname(
  pathname: string,
  fallbackThreadId: string | undefined,
): string | undefined {
  const segments = pathname.split("/").filter(Boolean);
  const chatsIndex = segments.indexOf(CHATS_PATH_SEGMENT);
  const threadIdFromPath =
    chatsIndex !== -1 ? segments[chatsIndex + 1] : undefined;
  if (threadIdFromPath && threadIdFromPath !== "new") {
    return threadIdFromPath;
  }
  return fallbackThreadId ?? threadIdFromPath;
}

// The chat page swaps the URL via the native History API when a new thread is
// created (see onStart in app/workspace/chats/[thread_id]/page.tsx), which
// leaves useParams() stuck on the stale "new" value. usePathname() does track
// native history, so it is the source of truth for the real thread id.
export function useResolvedThreadId() {
  const pathname = usePathname();
  const { thread_id: threadIdFromParams } = useParams<{
    thread_id?: string;
  }>();
  return resolveThreadIdFromPathname(pathname, threadIdFromParams);
}
