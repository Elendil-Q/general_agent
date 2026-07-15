import { cookies } from "next/headers";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/workspace/command-palette";
import { GatewayOfflineBanner } from "@/components/workspace/gateway-offline-banner";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

function parseSidebarOpenCookie(
  value: string | undefined,
): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

function parseSidebarWidthCookie(
  value: string | undefined,
): number | undefined {
  if (!value) return undefined;
  const n = Number(value);
  if (!Number.isFinite(n)) return undefined;
  return n;
}

export async function WorkspaceContent({
  children,
  gatewayUnavailable = false,
}: Readonly<{
  children: React.ReactNode;
  gatewayUnavailable?: boolean;
}>) {
  const cookieStore = await cookies();
  const initialSidebarOpen = parseSidebarOpenCookie(
    cookieStore.get("sidebar_state")?.value,
  );
  const initialSidebarWidth = parseSidebarWidthCookie(
    cookieStore.get("sidebar_width")?.value,
  );

  return (
    <QueryClientProvider>
      <SidebarProvider
        className="h-dvh"
        defaultOpen={initialSidebarOpen}
        defaultWidth={initialSidebarWidth}
      >
        <WorkspaceSidebar />
        <SidebarInset className="min-w-0">
          <GatewayOfflineBanner gatewayUnavailable={gatewayUnavailable} />
          {children}
        </SidebarInset>
      </SidebarProvider>
      <CommandPalette />
      <Toaster position="top-center" />
    </QueryClientProvider>
  );
}
