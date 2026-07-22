"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { FileBrowserProvider } from "@/components/workspace/file-browser/context";
import { SubtasksProvider } from "@/core/tasks/context";

export function ChatProviders({ children }: { children: React.ReactNode }) {
  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <FileBrowserProvider>
          <PromptInputProvider>{children}</PromptInputProvider>
        </FileBrowserProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
