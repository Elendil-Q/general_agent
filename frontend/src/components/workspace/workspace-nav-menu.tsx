"use client";

import { SettingsIcon, SparklesIcon, WrenchIcon } from "lucide-react";
import { useEffect, useState } from "react";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsDialog } from "./settings";

type SettingsSection =
  | "appearance"
  | "memory"
  | "tools"
  | "skills"
  | "notification"
  | "about";

function NavEntryButton({
  icon: Icon,
  label,
  isSidebarOpen,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  isSidebarOpen: boolean;
  onClick: () => void;
}) {
  return (
    <SidebarMenuButton
      size="lg"
      onClick={onClick}
      tooltip={isSidebarOpen ? undefined : label}
      className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
    >
      {isSidebarOpen ? (
        <div className="text-muted-foreground flex w-full items-center gap-2 text-left text-sm">
          <Icon className="size-4" />
          <span>{label}</span>
        </div>
      ) : (
        <div className="flex size-full items-center justify-center">
          <Icon className="text-muted-foreground size-4" />
        </div>
      )}
    </SidebarMenuButton>
  );
}

function PlaceholderButton({
  icon: Icon,
  isSidebarOpen,
}: {
  icon: React.ComponentType<{ className?: string }>;
  isSidebarOpen: boolean;
}) {
  return (
    <SidebarMenuButton size="lg" className="pointer-events-none">
      {isSidebarOpen ? (
        <div className="text-muted-foreground flex w-full items-center gap-2 text-left text-sm">
          <Icon className="size-4" />
        </div>
      ) : (
        <div className="flex size-full items-center justify-center">
          <Icon className="text-muted-foreground size-4" />
        </div>
      )}
    </SidebarMenuButton>
  );
}

export function WorkspaceNavMenu() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDefaultSection, setSettingsDefaultSection] =
    useState<SettingsSection>("appearance");
  const [mounted, setMounted] = useState(false);
  const { open: isSidebarOpen } = useSidebar();
  const { t } = useI18n();

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <>
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        defaultSection={settingsDefaultSection}
      />
      <SidebarMenu className="w-full">
        {mounted ? (
          <>
            <SidebarMenuItem>
              <NavEntryButton
                icon={SparklesIcon}
                label={t.workspace.skills}
                isSidebarOpen={isSidebarOpen}
                onClick={() => {
                  setSettingsDefaultSection("skills");
                  setSettingsOpen(true);
                }}
              />
            </SidebarMenuItem>
            <SidebarMenuItem>
              <NavEntryButton
                icon={WrenchIcon}
                label={t.workspace.mcpServices}
                isSidebarOpen={isSidebarOpen}
                onClick={() => {
                  setSettingsDefaultSection("tools");
                  setSettingsOpen(true);
                }}
              />
            </SidebarMenuItem>
            <SidebarMenuItem>
              <NavEntryButton
                icon={SettingsIcon}
                label={t.common.settings}
                isSidebarOpen={isSidebarOpen}
                onClick={() => {
                  setSettingsDefaultSection("appearance");
                  setSettingsOpen(true);
                }}
              />
            </SidebarMenuItem>
          </>
        ) : (
          <SidebarMenuItem>
            <PlaceholderButton
              icon={SettingsIcon}
              isSidebarOpen={isSidebarOpen}
            />
          </SidebarMenuItem>
        )}
      </SidebarMenu>
    </>
  );
}
