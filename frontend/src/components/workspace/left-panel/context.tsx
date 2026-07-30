"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useThread } from "@/components/workspace/messages/context";
import { useLocalSettings } from "@/core/settings";
import { useSubtaskContext } from "@/core/tasks/context";

interface LeftPanelContextType {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

const LeftPanelContext = createContext<LeftPanelContextType | undefined>(
  undefined,
);

export function LeftPanelProvider({ children }: { children: ReactNode }) {
  const { thread } = useThread();
  const { tasks } = useSubtaskContext();
  const [localSettings, setLocalSettings] = useLocalSettings();

  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  const hasActiveSubagents = Object.values(tasks).some(
    (task) =>
      task.status === "in_progress" ||
      task.status === "idle" ||
      task.status === "interrupted",
  );
  const shouldBeOpen = hasTodos || hasActiveSubagents;

  const [open, setOpenState] = useState(
    () => localSettings.leftPanel?.open ?? false,
  );
  const wasActiveRef = useRef(shouldBeOpen);

  useEffect(() => {
    if (shouldBeOpen && !wasActiveRef.current) {
      setOpenState(true);
    }
    wasActiveRef.current = shouldBeOpen;
  }, [shouldBeOpen]);

  const setOpen = useCallback(
    (next: boolean) => {
      setOpenState(next);
      setLocalSettings("leftPanel", { open: next });
    },
    [setLocalSettings],
  );

  const toggle = useCallback(() => {
    setOpen(!open);
  }, [open, setOpen]);

  return (
    <LeftPanelContext.Provider value={{ open, setOpen, toggle }}>
      {children}
    </LeftPanelContext.Provider>
  );
}

export function useLeftPanel() {
  const context = useContext(LeftPanelContext);
  if (context === undefined) {
    throw new Error("useLeftPanel must be used within a LeftPanelProvider");
  }
  return context;
}
