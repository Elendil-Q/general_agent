import { beforeEach, describe, expect, test, rs } from "@rstest/core";

import {
  type CreateSubagentRequest,
  checkSubagentName,
  createSubagent,
  deleteSubagent,
  getSubagent,
  getSubagentCatalogs,
  listSubagents,
  updateSubagent,
} from "@/core/subagents";

// Mirror the module-mock pattern used by core/agents/api.test.ts: stub the
// fetcher and config modules so every request funnels through `mockedFetch`
// and every URL is built against an empty backend base ("").
rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

const { fetch: fetcher } = await import("@/core/api/fetcher");
const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const sampleSubagent = {
  name: "researcher",
  description: "Research assistant",
  system_prompt: "You research things.",
  tools: ["search"],
  skills: [],
  skills_on_demand: [],
  model: "inherit",
  max_turns: 50,
  timeout_seconds: 900,
  readonly: false,
  source: "user" as const,
  overrides_global: false,
};

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("listSubagents", () => {
  test("GETs /api/subagents and unwraps the subagents array", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { subagents: [sampleSubagent] }),
    );
    const result = await listSubagents();
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents");
    expect(result).toEqual([sampleSubagent]);
  });

  test("throws when the backend responds non-ok", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 500, statusText: "Internal Server Error" }),
    );
    await expect(listSubagents()).rejects.toThrow(
      "Failed to load subagents: Internal Server Error",
    );
  });
});

describe("getSubagent", () => {
  test("GETs /api/subagents/{name} and returns the subagent", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, sampleSubagent));
    const result = await getSubagent("researcher");
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents/researcher");
    expect(result).toEqual(sampleSubagent);
  });

  test("throws when the subagent is not found", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 404, statusText: "Not Found" }),
    );
    await expect(getSubagent("ghost")).rejects.toThrow(
      "Subagent 'ghost' not found",
    );
  });
});

describe("createSubagent", () => {
  const request: CreateSubagentRequest = {
    name: "researcher",
    description: "Research assistant",
    system_prompt: "You research things.",
    tools: ["search"],
    skills: [],
    skills_on_demand: [],
    model: "inherit",
    max_turns: 50,
    timeout_seconds: 900,
  };

  test("POSTs to /api/subagents and returns the created subagent", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(201, sampleSubagent));
    const result = await createSubagent(request);
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    expect(result).toEqual(sampleSubagent);
  });

  test("surfaces the backend detail on a 422 invalid-name rejection", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(422, { detail: "Invalid or reserved subagent name" }),
    );
    await expect(createSubagent(request)).rejects.toThrow(
      "Invalid or reserved subagent name",
    );
  });

  test("surfaces the backend detail on a 409 conflict", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(409, { detail: "Subagent 'researcher' already exists" }),
    );
    await expect(createSubagent(request)).rejects.toThrow(
      "Subagent 'researcher' already exists",
    );
  });
});

describe("updateSubagent", () => {
  test("PUTs to /api/subagents/{name} and returns the updated subagent", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, sampleSubagent));
    const result = await updateSubagent("researcher", { max_turns: 30 });
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents/researcher", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_turns: 30 }),
    });
    expect(result).toEqual(sampleSubagent);
  });

  test("surfaces the backend detail on a 403 read-only rejection", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(403, { detail: "Built-in subagents are read-only" }),
    );
    await expect(updateSubagent("builtin", {})).rejects.toThrow(
      "Built-in subagents are read-only",
    );
  });
});

describe("deleteSubagent", () => {
  test("DELETEs /api/subagents/{name}", async () => {
    mockedFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await deleteSubagent("researcher");
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents/researcher", {
      method: "DELETE",
    });
  });

  test("throws when the backend responds non-ok", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 404, statusText: "Not Found" }),
    );
    await expect(deleteSubagent("ghost")).rejects.toThrow(
      "Failed to delete subagent: Not Found",
    );
  });
});

describe("getSubagentCatalogs", () => {
  test("GETs /api/subagents/catalogs and returns the catalogs", async () => {
    const catalogs = {
      models: [{ name: "inherit", label: "Inherit parent" }],
      tools: ["search", "coder"],
      skills: [{ name: "summarize", description: "Summarize content" }],
    };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, catalogs));
    const result = await getSubagentCatalogs();
    expect(mockedFetch).toHaveBeenCalledWith("/api/subagents/catalogs");
    expect(result).toEqual(catalogs);
  });
});

describe("checkSubagentName", () => {
  test("returns availability payload on 200", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { available: true, name: "researcher" }),
    );
    await expect(checkSubagentName("Researcher")).resolves.toEqual({
      available: true,
      name: "researcher",
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/subagents/check?name=Researcher",
    );
  });

  test("treats network-layer fetch rejection as backend_unreachable", async () => {
    mockedFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(checkSubagentName("researcher")).rejects.toMatchObject({
      name: "SubagentNameCheckError",
      reason: "backend_unreachable",
    });
  });

  test.each([502, 503, 504])(
    "treats HTTP %i as backend_unreachable",
    async (status) => {
      mockedFetch.mockResolvedValueOnce(
        jsonResponse(status, { detail: "Bad Gateway" }),
      );
      await expect(checkSubagentName("researcher")).rejects.toMatchObject({
        name: "SubagentNameCheckError",
        reason: "backend_unreachable",
      });
    },
  );

  test("carries backend 422 detail through SubagentNameCheckError.detail", async () => {
    const detail = "Invalid or reserved subagent name";
    mockedFetch.mockResolvedValueOnce(jsonResponse(422, { detail }));
    await expect(checkSubagentName("bad name")).rejects.toMatchObject({
      name: "SubagentNameCheckError",
      reason: "request_failed",
      detail,
      message: detail,
    });
  });

  test("falls back to statusText in message but leaves detail null when backend returns no detail", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 500, statusText: "Internal Server Error" }),
    );
    await expect(checkSubagentName("researcher")).rejects.toMatchObject({
      name: "SubagentNameCheckError",
      reason: "request_failed",
      detail: null,
      message: expect.stringContaining("Internal Server Error"),
    });
  });

  test("treats non-string detail as null (defence against schema drift)", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(500, { detail: { code: "x", message: "y" } }),
    );
    await expect(checkSubagentName("researcher")).rejects.toMatchObject({
      name: "SubagentNameCheckError",
      reason: "request_failed",
      detail: null,
    });
  });
});
