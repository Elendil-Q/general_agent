import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// The e2e server runs with DEER_FLOW_AUTH_DISABLED=1 (see playwright.config.ts),
// so the root entry resolves to an authenticated synthetic user and skips the
// former marketing landing page — it must redirect straight into the agent.
test.describe("Root entry", () => {
  test("redirects past the landing page into the workspace", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/");

    await page.waitForURL("**/workspace/chats/new");
    await expect(page).toHaveURL(/\/workspace\/chats\/new/);
  });
});
