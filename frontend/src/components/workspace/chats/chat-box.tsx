import { FilesIcon, XIcon } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  useArtifacts,
} from "../artifacts";
import { useFileBrowser } from "../file-browser/context";
import { FileBrowserPanel } from "../file-browser/file-browser-panel";
import { useThread } from "../messages/context";

// Three-panel layouts: { "file-browser", chat, artifacts } percentages
const LAYOUT_BOTH_CLOSED = { "file-browser": 0, chat: 100, artifacts: 0 };
const LAYOUT_FILE_BROWSER_ONLY = { "file-browser": 20, chat: 80, artifacts: 0 };
const LAYOUT_ARTIFACTS_ONLY = { "file-browser": 0, chat: 60, artifacts: 40 };
const LAYOUT_BOTH_OPEN = { "file-browser": 18, chat: 47, artifacts: 35 };

const ChatBox: React.FC<{ children: React.ReactNode; threadId: string }> = ({
  children,
  threadId,
}) => {
  const { thread } = useThread();
  const pathname = usePathname();
  const threadIdRef = useRef(threadId);
  const layoutRef = useRef<GroupImperativeHandle>(null);

  const {
    artifacts,
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
    selectedArtifact,
  } = useArtifacts();

  const { open: fileBrowserOpen, setOpen: setFileBrowserOpen } =
    useFileBrowser();

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);
  useEffect(() => {
    const threadArtifacts = Array.isArray(thread.values.artifacts)
      ? thread.values.artifacts
      : undefined;

    if (threadIdRef.current !== threadId) {
      threadIdRef.current = threadId;
      deselect();
      setArtifacts([]);
    }

    // Update artifacts from the current thread
    if (threadArtifacts) {
      setArtifacts(threadArtifacts);
    }

    if (
      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" &&
      autoSelectFirstArtifact
    ) {
      if (threadArtifacts && threadArtifacts.length > 0) {
        setAutoSelectFirstArtifact(false);
        selectArtifact(threadArtifacts[0]!);
      }
    }
  }, [
    threadId,
    autoSelectFirstArtifact,
    deselect,
    selectArtifact,
    selectedArtifact,
    setArtifacts,
    thread.values.artifacts,
  ]);

  const artifactPanelOpen = useMemo(() => {
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return artifactsOpen && artifacts?.length > 0;
    }
    return artifactsOpen;
  }, [artifactsOpen, artifacts]);

  const resizableIdBase = useMemo(() => {
    return pathname.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  }, [pathname]);

  // Compute three-panel layout based on which panels are open
  const layout = useMemo(() => {
    const hasFileBrowser = fileBrowserOpen;
    const hasArtifacts = artifactPanelOpen;

    if (hasFileBrowser && hasArtifacts) return LAYOUT_BOTH_OPEN;
    if (hasFileBrowser) return LAYOUT_FILE_BROWSER_ONLY;
    if (hasArtifacts) return LAYOUT_ARTIFACTS_ONLY;
    return LAYOUT_BOTH_CLOSED;
  }, [fileBrowserOpen, artifactPanelOpen]);

  useEffect(() => {
    if (layoutRef.current) {
      layoutRef.current.setLayout(layout);
    }
  }, [layout]);

  return (
    <ResizablePanelGroup
      id={`${resizableIdBase}-panels`}
      orientation="horizontal"
      defaultLayout={{ "file-browser": 0, chat: 100, artifacts: 0 }}
      resizeTargetMinimumSize={{ coarse: 16, fine: 12 }}
      groupRef={layoutRef}
    >
      {/* ── File Browser Panel (left) ── */}
      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !fileBrowserOpen && "opacity-0",
        )}
        defaultSize={0}
        id="file-browser"
        maxSize="40%"
        minSize="12%"
      >
        <div
          className={cn(
            "h-full transition-transform duration-300 ease-in-out",
            fileBrowserOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <FileBrowserPanel
            threadId={threadId}
            onClose={() => setFileBrowserOpen(false)}
          />
        </div>
      </ResizablePanel>

      <ResizableHandle
        id={`${resizableIdBase}-file-browser-separator`}
        className={cn(!fileBrowserOpen && "pointer-events-none opacity-0")}
      />

      {/* ── Chat Panel (center) ── */}
      <ResizablePanel className="relative" defaultSize={100} id="chat">
        {children}
      </ResizablePanel>

      <ResizableHandle
        id={`${resizableIdBase}-separator`}
        disabled
        className={cn(
          "opacity-33 hover:opacity-100",
          !artifactPanelOpen && "pointer-events-none opacity-0",
        )}
      />

      {/* ── Artifacts Panel (right) ── */}
      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !artifactsOpen && "opacity-0",
        )}
        id="artifacts"
      >
        <div
          className={cn(
            "h-full p-4 transition-transform duration-300 ease-in-out",
            artifactPanelOpen ? "translate-x-0" : "translate-x-full",
          )}
        >
          {selectedArtifact ? (
            <ArtifactFileDetail
              className="size-full"
              filepath={selectedArtifact}
              threadId={threadId}
            />
          ) : (
            <div className="relative flex size-full justify-center">
              <div className="absolute top-1 right-1 z-30">
                <Button
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => {
                    setArtifactsOpen(false);
                  }}
                >
                  <XIcon />
                </Button>
              </div>
              {artifacts.length === 0 ? (
                <ConversationEmptyState
                  icon={<FilesIcon />}
                  title="No artifact selected"
                  description="Select an artifact to view its details"
                />
              ) : (
                <div className="flex size-full max-w-(--container-width-sm) flex-col justify-center p-4 pt-8">
                  <header className="shrink-0">
                    <h2 className="text-lg font-medium">Artifacts</h2>
                  </header>
                  <main className="min-h-0 grow">
                    <ArtifactFileList
                      className="max-w-(--container-width-sm) p-4 pt-12"
                      files={artifacts}
                      threadId={threadId}
                    />
                  </main>
                </div>
              )}
            </div>
          )}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
};

export { ChatBox };
