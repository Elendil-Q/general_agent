/**
 * Tests for the modes config API client — specifically the 404 fallback that
 * lets the frontend work against an older backend that does not yet serve
 * `GET /api/modes/config`.
 */
import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { loadModesConfig } from "@/core/modes/api";

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

describe("loadModesConfig", () => {
  test("returns parsed presets on 200", async () => {
    const body = {
      presets: [
        {
          name: "flash",
          thinking_enabled: false,
          is_plan_mode: false,
          subagent_enabled: false,
          reasoning_effort: "minimal",
          icon: "zap",
          label: null,
          description: null,
        },
      ],
      default: "flash",
    };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, body));
    await expect(loadModesConfig()).resolves.toEqual(body);
  });

  test("returns null on 404 so callers fall back to built-in presets", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(404, { detail: "Not Found" }),
    );
    await expect(loadModesConfig()).resolves.toBeNull();
  });

  test("throws on other non-2xx statuses", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(500, { detail: "boom" }));
    await expect(loadModesConfig()).rejects.toThrow(
      /Failed to load modes config/,
    );
  });
});
