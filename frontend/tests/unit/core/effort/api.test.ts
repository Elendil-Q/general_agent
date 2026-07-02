/**
 * Tests for the efforts config API client — specifically the 404 fallback that
 * lets the frontend work against an older backend that does not yet serve
 * `GET /api/efforts/config`.
 */
import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { loadEffortsConfig } from "@/core/effort/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("loadEffortsConfig", () => {
  test("returns parsed efforts on 200", async () => {
    const body = {
      flash: {
        thinking_enabled: false,
        is_plan_mode: false,
        subagent_enabled: false,
        reasoning_effort: "minimal",
      },
      thinking: {
        thinking_enabled: true,
        is_plan_mode: false,
        subagent_enabled: false,
        reasoning_effort: "low",
      },
      pro: {
        thinking_enabled: true,
        is_plan_mode: true,
        subagent_enabled: false,
        reasoning_effort: "medium",
      },
      ultra: {
        thinking_enabled: true,
        is_plan_mode: true,
        subagent_enabled: true,
        reasoning_effort: "high",
      },
    };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, body));
    await expect(loadEffortsConfig()).resolves.toEqual(body);
  });

  test("returns null on 404 so callers fall back to built-in presets", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(404, { detail: "Not Found" }),
    );
    await expect(loadEffortsConfig()).resolves.toBeNull();
  });

  test("throws on other non-2xx statuses", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));
    await expect(loadEffortsConfig()).rejects.toThrow(
      /Failed to load efforts config/,
    );
  });
});
