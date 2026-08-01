import { describe, expect, it } from "@rstest/core";

import { resolveThreadIdFromPathname } from "@/core/threads/thread-id";

describe("resolveThreadIdFromPathname", () => {
  it("prefers the real id in the pathname over a stale 'new' route param", () => {
    expect(
      resolveThreadIdFromPathname("/workspace/chats/thread-123", "new"),
    ).toBe("thread-123");
  });

  it("parses the thread id from a subagent conversation path", () => {
    expect(
      resolveThreadIdFromPathname(
        "/workspace/chats/thread-123/subagents/task-42",
        "new",
      ),
    ).toBe("thread-123");
  });

  it("falls back to the route param when the pathname still points at 'new'", () => {
    expect(resolveThreadIdFromPathname("/workspace/chats/new", "new")).toBe(
      "new",
    );
  });

  it("prefers the route param when the pathname has no chat segment", () => {
    expect(resolveThreadIdFromPathname("/workspace/agents", "thread-9")).toBe(
      "thread-9",
    );
  });

  it("returns undefined when neither source has an id", () => {
    expect(
      resolveThreadIdFromPathname("/workspace", undefined),
    ).toBeUndefined();
  });
});
