import { afterEach, describe, expect, it, rs } from "@rstest/core";

import type { Subtask } from "@/core/tasks/types";

function makeSubtask(overrides: Partial<Subtask> = {}): Subtask {
  return {
    id: "task-1",
    status: "in_progress",
    subagent_type: "general-purpose",
    description: "research something",
    prompt: "prompt",
    ...overrides,
  };
}

type TestElement = { props: Record<string, unknown> };

function childrenOf(element: unknown): unknown[] {
  const children = (element as TestElement).props.children;
  return Array.isArray(children) ? children : [children];
}

async function loadSubagentList(
  push: ReturnType<typeof rs.fn>,
  params: Record<string, string> = { thread_id: "thread-1" },
  pathname = `/workspace/chats/${params.thread_id}`,
) {
  rs.resetModules();
  rs.doMock("next/navigation", () => ({
    useRouter: () => ({ push }),
    useParams: () => params,
    usePathname: () => pathname,
  }));
  return import("@/components/workspace/activity/subagent-list");
}

afterEach(() => {
  rs.doUnmock("next/navigation");
  rs.resetModules();
});

describe("SubagentList", () => {
  it("navigates to the subagent conversation route on click", async () => {
    const push = rs.fn();
    const { SubagentList } = await loadSubagentList(push);

    const element = SubagentList({
      tasks: [makeSubtask({ id: "task-42" })],
    }) as unknown as TestElement;

    const listItems = childrenOf(element);
    expect(listItems).toHaveLength(1);
    const button = childrenOf(listItems[0])[0] as TestElement;
    const onClick = button.props.onClick as () => void;
    onClick();

    expect(push).toHaveBeenCalledTimes(1);
    expect(push).toHaveBeenCalledWith(
      "/workspace/chats/thread-1/subagents/task-42",
    );
  });

  it("uses the current thread id from the route params", async () => {
    const push = rs.fn();
    const { SubagentList } = await loadSubagentList(push, {
      thread_id: "thread-xyz",
    });

    const element = SubagentList({
      tasks: [makeSubtask({ id: "task-7" })],
    }) as unknown as TestElement;

    const button = childrenOf(childrenOf(element)[0])[0] as TestElement;
    (button.props.onClick as () => void)();

    expect(push).toHaveBeenCalledWith(
      "/workspace/chats/thread-xyz/subagents/task-7",
    );
  });

  it("uses the real thread id from the pathname when route params are stale", async () => {
    const push = rs.fn();
    // New-conversation flow: onStart swaps the URL via the native History
    // API, leaving useParams stuck on "new" while the pathname carries the
    // backend-created thread id.
    const { SubagentList } = await loadSubagentList(
      push,
      { thread_id: "new" },
      "/workspace/chats/thread-real",
    );

    const element = SubagentList({
      tasks: [makeSubtask({ id: "task-42" })],
    }) as unknown as TestElement;

    const button = childrenOf(childrenOf(element)[0])[0] as TestElement;
    (button.props.onClick as () => void)();

    expect(push).toHaveBeenCalledWith(
      "/workspace/chats/thread-real/subagents/task-42",
    );
  });

  it("renders one list item per task", async () => {
    const push = rs.fn();
    const { SubagentList } = await loadSubagentList(push);

    const element = SubagentList({
      tasks: [makeSubtask({ id: "task-1" }), makeSubtask({ id: "task-2" })],
    }) as unknown as TestElement;

    expect(childrenOf(element)).toHaveLength(2);
  });
});
