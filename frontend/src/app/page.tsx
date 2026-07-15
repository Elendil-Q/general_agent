import { redirect } from "next/navigation";

import { Footer } from "@/components/landing/footer";
import { Header } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";
import { CaseStudySection } from "@/components/landing/sections/case-study-section";
import { CommunitySection } from "@/components/landing/sections/community-section";
import { SandboxSection } from "@/components/landing/sections/sandbox-section";
import { SkillsSection } from "@/components/landing/sections/skills-section";
import { WhatsNewSection } from "@/components/landing/sections/whats-new-section";
import { GatewayOfflineFallback } from "@/components/workspace/gateway-offline-fallback";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";
import { isStaticWebsiteOnly } from "@/core/static-mode";

export const dynamic = "force-dynamic";

export default async function RootEntryPage() {
  // Static demo build keeps its marketing landing page.
  if (isStaticWebsiteOnly()) {
    return (
      <div className="min-h-dvh w-full bg-[#0a0a0a]">
        <Header />
        <main className="flex w-full flex-col">
          <Hero />
          <CaseStudySection />
          <SkillsSection />
          <SandboxSection />
          <WhatsNewSection />
          <CommunitySection />
        </main>
        <Footer />
      </div>
    );
  }

  // When auth is explicitly required, always redirect to /login — do not
  // check the session, force the user through the login page.
  if (process.env.DEER_FLOW_AUTH_DISABLED === "0") {
    redirect("/login");
  }

  // Gateway entry: skip the welcome screen and route straight to the auth or
  // agent surface based on the resolved session — DEER_FLOW_AUTH_DISABLED=1
  // resolves to an authenticated synthetic user and lands in the workspace.
  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      redirect("/workspace");
    case "needs_setup":
      redirect("/setup");
    case "system_setup_required":
      redirect("/setup");
    case "unauthenticated":
      redirect("/login");
    case "gateway_unavailable":
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
