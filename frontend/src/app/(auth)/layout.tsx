import { redirect } from "next/navigation";
import { type ReactNode } from "react";

import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

export const dynamic = "force-dynamic";

export default async function AuthLayout({
  children,
}: {
  children: ReactNode;
}) {
  // When auth is explicitly required, always show the auth page — do not
  // redirect past it even if the user already has a valid session cookie.
  if (process.env.DEER_FLOW_AUTH_DISABLED === "0") {
    return <AuthProvider initialUser={null}>{children}</AuthProvider>;
  }

  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      redirect("/workspace");
    case "needs_setup":
      // Allow access to setup page
      return <AuthProvider initialUser={result.user}>{children}</AuthProvider>;
    case "system_setup_required":
    case "unauthenticated":
      return <AuthProvider initialUser={null}>{children}</AuthProvider>;
    case "gateway_unavailable":
      // Auth pages have no banner of their own, so render one here. The
      // fallback's AuthProvider replaces the bare-HTML branch that
      // previously locked users out without any logout/retry capability.
      return (
        <GatewayOfflineFallback renderBanner>
          <div className="flex h-dvh flex-col items-center justify-center gap-4">
            <p className="text-muted-foreground">
              Service temporarily unavailable.
            </p>
          </div>
        </GatewayOfflineFallback>
      );
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}
