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
import { ActivityPanel } from "@/components/workspace/activity";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  useArtifacts,
} from "../artifacts";
import { FileBrowserPanel } from "../file-browser/file-browser-panel";
import { useLeftPanel } from "../left-panel";
import { useThread } from "../messages/context";

// Three-panel layouts: { "left-column", chat, artifacts } percentages
const LAYOUT_BOTH_CLOSED = { "left-column": 0, chat: 100, artifacts: 0 };
const LAYOUT_LEFT_ONLY = { "left-column": 20, chat: 80, artifacts: 0 };
const LAYOUT_ARTIFACTS_ONLY = { "left-column": 0, chat: 60, artifacts: 40 };
const LAYOUT_BOTH_OPEN = { "left-column": 18, chat: 47, artifacts: 35 };

// Vertical layout inside the left column: { activity, files } (fixed 35/65)
const VERTICAL_LAYOUT = { activity: 35, files: 65 };

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

  const { open: leftPanelOpen } = useLeftPanel();

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
    const hasLeft = leftPanelOpen;
    const hasArtifacts = artifactPanelOpen;

    if (hasLeft && hasArtifacts) return LAYOUT_BOTH_OPEN;
    if (hasLeft) return LAYOUT_LEFT_ONLY;
    if (hasArtifacts) return LAYOUT_ARTIFACTS_ONLY;
    return LAYOUT_BOTH_CLOSED;
  }, [leftPanelOpen, artifactPanelOpen]);

  useEffect(() => {
    if (layoutRef.current) {
      layoutRef.current.setLayout(layout);
    }
  }, [layout]);

  return (
    <ResizablePanelGroup
      id={`${resizableIdBase}-panels`}
      orientation="horizontal"
      defaultLayout={{ "left-column": 0, chat: 100, artifacts: 0 }}
      resizeTargetMinimumSize={{ coarse: 0, fine: 0 }}
      groupRef={layoutRef}
    >
      {/* ── Left column: Activity (top) + Files (bottom) ── */}
      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !leftPanelOpen && "opacity-0",
        )}
        defaultSize={0}
        id="left-column"
      >
        <div
          className={cn(
            "h-full transition-transform duration-300 ease-in-out",
            leftPanelOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <ResizablePanelGroup
            id={`${resizableIdBase}-left-panels`}
            orientation="vertical"
            defaultLayout={VERTICAL_LAYOUT}
            resizeTargetMinimumSize={{ coarse: 0, fine: 0 }}
          >
            <ResizablePanel id="activity">
              <ActivityPanel />
            </ResizablePanel>

            <ResizableHandle
              disabled
              className={cn(
                "opacity-33 hover:opacity-100",
                !leftPanelOpen && "pointer-events-none opacity-0",
              )}
            />

            <ResizablePanel id="files">
              <FileBrowserPanel threadId={threadId} />
            </ResizablePanel>
          </ResizablePanelGroup>
        </div>
      </ResizablePanel>

      <ResizableHandle
        id={`${resizableIdBase}-file-browser-separator`}
        disabled
        className={cn(!leftPanelOpen && "pointer-events-none opacity-0")}
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
