import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const THREADS = Array.from({ length: 5 }, (_, i) => {
  const index = String(i + 1).padStart(3, "0");
  return {
    thread_id: `00000000-0000-0000-0000-0000000${index.padStart(5, "0")}`,
    title: `Conversation ${index}`,
    updated_at: `2025-06-${String((i % 28) + 1).padStart(2, "0")}T12:00:00Z`,
  };
});

test.describe("Chats bulk delete", () => {
  test("select and delete multiple threads", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats");

    // Wait for threads to render
    const main = page.locator("main");
    await expect(main.getByText("Conversation 001")).toBeVisible({
      timeout: 15_000,
    });
    await expect(main.getByText("Conversation 005")).toBeVisible();

    // Select threads 001 and 003
    await page
      .getByTestId(`chats-thread-checkbox-${THREADS[0]!.thread_id}`)
      .click();
    await page
      .getByTestId(`chats-thread-checkbox-${THREADS[2]!.thread_id}`)
      .click();

    // Action bar should appear showing "2 selected"
    await expect(page.getByTestId("chats-delete-selected")).toBeVisible();
    await expect(page.getByText("2 selected")).toBeVisible();

    // Click delete and confirm
    await page.getByTestId("chats-delete-selected").click();
    await expect(page.getByTestId("chats-confirm-delete")).toBeVisible();
    await page.getByTestId("chats-confirm-delete").click();

    // Deleted threads should be gone; remaining threads stay
    await expect(main.getByText("Conversation 001")).toHaveCount(0);
    await expect(main.getByText("Conversation 003")).toHaveCount(0);
    await expect(main.getByText("Conversation 002")).toBeVisible();
    await expect(main.getByText("Conversation 004")).toBeVisible();
    await expect(main.getByText("Conversation 005")).toBeVisible();

    // Action bar should disappear after deletion
    await expect(page.getByTestId("chats-delete-selected")).toHaveCount(0);
  });

  test("select all selects and deselects all visible threads", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats");

    const main = page.locator("main");
    await expect(main.getByText("Conversation 001")).toBeVisible({
      timeout: 15_000,
    });

    // Click select-all
    await page.getByTestId("chats-select-all").click();

    // All 5 should be selected
    await expect(page.getByText("5 selected")).toBeVisible();

    // Click select-all again to deselect
    await page.getByTestId("chats-select-all").click();

    // Action bar should disappear
    await expect(page.getByTestId("chats-delete-selected")).toHaveCount(0);
  });

  test("cancel selection clears selection and hides action bar", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats");

    const main = page.locator("main");
    await expect(main.getByText("Conversation 001")).toBeVisible({
      timeout: 15_000,
    });

    // Select one thread
    await page
      .getByTestId(`chats-thread-checkbox-${THREADS[0]!.thread_id}`)
      .click();
    await expect(page.getByTestId("chats-delete-selected")).toBeVisible();

    // Click cancel
    await page.getByTestId("chats-cancel-selection").click();

    // Action bar should disappear
    await expect(page.getByTestId("chats-delete-selected")).toHaveCount(0);
  });

  test("delete confirmation dialog can be cancelled", async ({ page }) => {
    mockLangGraphAPI(page, { threads: THREADS });

    await page.goto("/workspace/chats");

    const main = page.locator("main");
    await expect(main.getByText("Conversation 001")).toBeVisible({
      timeout: 15_000,
    });

    // Select one thread and open delete dialog
    await page
      .getByTestId(`chats-thread-checkbox-${THREADS[0]!.thread_id}`)
      .click();
    await page.getByTestId("chats-delete-selected").click();
    await expect(page.getByTestId("chats-confirm-delete")).toBeVisible();

    // Cancel the dialog
    await page.getByRole("button", { name: /Cancel/i }).click();

    // Dialog should close, thread should still be selected
    await expect(page.getByTestId("chats-confirm-delete")).toHaveCount(0);
    await expect(page.getByTestId("chats-delete-selected")).toBeVisible();
  });
});
