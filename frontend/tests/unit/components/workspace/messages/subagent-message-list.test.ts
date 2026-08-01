import type { AIMessage, Message, ToolMessage } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import { MessageGroup } from "@/components/workspace/messages/message-group";
import { MessageListItem } from "@/components/workspace/messages/message-list-item";
import { SubagentMessageList } from "@/components/workspace/messages/subagent-message-list";

function aiMessage(
  id: string,
  content: string,
  toolCalls?: AIMessage["tool_calls"],
): AIMessage {
  return {
    id,
    type: "ai",
    content,
    ...(toolCalls ? { tool_calls: toolCalls } : {}),
  } as AIMessage;
}

function toolMessage(
  id: string,
  toolCallId: string,
  content: string,
): ToolMessage {
  return {
    id,
    type: "tool",
    name: "write_file",
    tool_call_id: toolCallId,
    content,
  } as unknown as ToolMessage;
}

type TestElement = {
  type: unknown;
  props: Record<string, unknown>;
};

function flattenChildren(element: TestElement): TestElement[] {
  const out: TestElement[] = [];
  const walk = (node: unknown) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node == null || typeof node !== "object") {
      return;
    }
    const child = node as TestElement;
    if (typeof child.type === "string" && "children" in child.props) {
      walk(child.props.children);
      return;
    }
    out.push(child);
  };
  walk(element.props.children);
  return out;
}

function render(messages: Message[]) {
  const element = SubagentMessageList({
    messages,
    threadId: "thread-1",
  }) as unknown as TestElement;
  return flattenChildren(element);
}

describe("SubagentMessageList", () => {
  it("renders tool exchanges as a MessageGroup instead of bare bubbles", () => {
    const messages: Message[] = [
      aiMessage("ai-1", "I'll create the file and then verify its contents.", [
        {
          id: "call-1",
          name: "write_file",
          args: { file_path: "/mnt/user-data/outputs/one.md", content: "1" },
        },
      ]),
      toolMessage("tool-1", "call-1", "OK"),
      aiMessage("ai-2", "1", [
        {
          id: "call-2",
          name: "read_file",
          args: { file_path: "/mnt/user-data/outputs/one.md" },
        },
      ]),
      toolMessage("tool-2", "call-2", "1"),
      aiMessage("ai-3", "文件已成功创建，内容验证正确。"),
    ];

    const children = render(messages);

    const messageGroups = children.filter(
      (child) => child.type === MessageGroup,
    );
    expect(messageGroups).toHaveLength(1);
    const groupMessages = messageGroups[0]?.props.messages as Message[];
    expect(groupMessages.map((message) => message.id)).toEqual([
      "ai-1",
      "tool-1",
      "ai-2",
      "tool-2",
    ]);
    expect(messageGroups[0]?.props.isLoading).toBe(false);

    // The final text answer stays a normal assistant bubble; raw tool output
    // must never surface as one.
    const listItems = children.filter(
      (child) => child.type === MessageListItem,
    );
    expect(listItems).toHaveLength(1);
    expect((listItems[0]?.props.message as Message).id).toBe("ai-3");
  });

  it("renders human messages with MessageListItem", () => {
    const messages: Message[] = [
      { id: "human-1", type: "human", content: "do the thing" } as Message,
      aiMessage("ai-1", "done"),
    ];

    const children = render(messages);

    const listItems = children.filter(
      (child) => child.type === MessageListItem,
    );
    expect(listItems.map((item) => (item.props.message as Message).id)).toEqual(
      ["human-1", "ai-1"],
    );
  });
});
