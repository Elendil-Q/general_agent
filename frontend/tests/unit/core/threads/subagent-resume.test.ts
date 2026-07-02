import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

test("resumeSubagent POSTs the resume value to the per-subagent endpoint", async () => {
  const cancel = rs.fn().mockResolvedValue(undefined);
  fetchWithAuth.mockResolvedValue({ ok: true, body: { cancel } });

  const { resumeSubagent } = await import("@/core/threads/subagent-resume");

  await resumeSubagent("thread-1", "tc-9", "staging");

  expect(fetchWithAuth).toHaveBeenCalledWith(
    expect.stringContaining("/api/threads/thread-1/subagents/tc-9/resume"),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume: "staging" }),
    },
  );
  // Body is released (fire-and-forget) so the redundant SSE stream does not linger.
  expect(cancel).toHaveBeenCalled();
});

test("resumeSubagent throws when the endpoint rejects", async () => {
  fetchWithAuth.mockResolvedValue({
    ok: false,
    status: 409,
    body: { cancel: rs.fn().mockResolvedValue(undefined) },
  });

  const { resumeSubagent } = await import("@/core/threads/subagent-resume");

  await expect(resumeSubagent("thread-1", "tc-x", "yes")).rejects.toThrow(
    "409",
  );
});
