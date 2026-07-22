"use client";

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

interface FileBrowserContextType {
  open: boolean;
  toggle: () => void;
  setOpen: (open: boolean) => void;
}

const FileBrowserContext = createContext<FileBrowserContextType | undefined>(
  undefined,
);

export function FileBrowserProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((prev) => !prev), []);

  return (
    <FileBrowserContext.Provider value={{ open, toggle, setOpen }}>
      {children}
    </FileBrowserContext.Provider>
  );
}

export function useFileBrowser() {
  const context = useContext(FileBrowserContext);
  if (context === undefined) {
    throw new Error("useFileBrowser must be used within a FileBrowserProvider");
  }
  return context;
}
