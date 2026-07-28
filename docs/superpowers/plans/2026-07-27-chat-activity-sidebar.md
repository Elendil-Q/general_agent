# Chat Activity Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the agent todo list and background-running subagent status into a
new "Activity" panel on the left side of the chat workspace, stacked above the
Files panel, while keeping Artifacts docked on the right.

**Architecture:** Wrap the chat page in an `ActivityProvider` that derives
auto-open state from `thread.values.todos` and `useSubtaskContext().tasks`.
Replace the single left `ResizablePanel` in `ChatBox` with a nested vertical
`ResizablePanelGroup` containing `ActivityPanel` (top) and `FileBrowserPanel`
(bottom). Add an `ActivityTrigger` to the chat header and remove the inline
`TodoList` from the composer area.

**Tech Stack:** Next.js 16, React 19, TypeScript 5.8, Tailwind CSS v4, shadcn/ui,
`react-resizable-panels`, LangGraph SDK React, `lucide-react`.

## Global Constraints

- Follow existing frontend conventions in `frontend/AGENTS.md`.
- Use `"use client"` only for interactive components; this plan touches only
  client components.
- Import ordering: builtin → external → internal → parent → sibling,
  alphabetized, newlines between groups; inline type imports (`import { type
  Foo }`).
- Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- Use `@/*` path alias mapped to `src/*`.
- Do not hand-edit files under `src/components/ui/` or
  `src/components/ai-elements/` (registry-generated).
- Run `pnpm check` (lint + type check) and `pnpm test` before considering work
  complete.
- Persist user preferences via the existing `useLocalSettings` /
  `deerflow.local-settings` mechanism.

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `src/components/workspace/activity/context.tsx` | `ActivityProvider` + `useActivity` hook: open state, auto-open logic, persistence |
| `src/components/workspace/activity/activity-panel.tsx` | Sidebar shell: header, scroll area, Todo + Subagent sections |
| `src/components/workspace/activity/activity-trigger.tsx` | Header button to toggle Activity panel |
| `src/components/workspace/activity/subagent-list.tsx` | Compact list of running/idle subagents with click-to-scroll |
| `src/components/workspace/activity/index.ts` | Public exports for the activity module |

### Modified files

| Path | Responsibility |
|------|----------------|
| `src/core/settings/local.ts` | Add `activity` section to `LocalSettings` and `DEFAULT_LOCAL_SETTINGS` |
| `src/components/workspace/chats/chat-box.tsx` | Nest Activity + Files vertically in the left column; update layout constants |
| `src/app/workspace/chats/[thread_id]/page.tsx` | Wrap `ChatBox` with `ActivityProvider`; remove inline `TodoList`; add `ActivityTrigger` |
| `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` | Same mirror changes as chat page |
| `src/app/workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx` | Add missing `FileBrowserProvider` to the provider stack |
| `src/components/workspace/todo-list.tsx` | Add `variant` prop so it can render without composer-specific absolute/fixed styles |
| `src/components/workspace/messages/subtask-card.tsx` | Add `data-task-id` marker for scroll-to |
| `src/core/i18n/locales/en-US.json` | Add `activity` translation keys |
| `src/core/i18n/locales/zh-CN.json` | Add `activity` translation keys |

---

## Task 1: Extend local settings for Activity panel state

**Files:**

- Modify: `src/core/settings/local.ts`

**Interfaces:**

- Consumes: existing `LocalSettings` shape and `mergeLocalSettings`.
- Produces: `LocalSettings["activity"]` with `{ open?: boolean }`.

- [ ] **Step 1: Add `activity` field to `LocalSettings` interface**

```ts
export interface LocalSettings {
  notification: { enabled: boolean };
  tokenUsage: {
    headerTotal: boolean;
    inlineMode: TokenUsageInlineMode;
  };
  context: Omit<...> & { ... };
  activity: {
    open?: boolean;
  };
}
```

- [ ] **Step 2: Add default value in `DEFAULT_LOCAL_SETTINGS`**

```ts
export const DEFAULT_LOCAL_SETTINGS: LocalSettings = {
  notification: { enabled: true },
  tokenUsage: { headerTotal: true, inlineMode: "per_turn" },
  context: { ... },
  activity: {},
};
```

- [ ] **Step 3: Update `mergeLocalSettings` to merge the new section**

```ts
function mergeLocalSettings(settings?: Partial<LocalSettings>): LocalSettings {
  return {
    ...DEFAULT_LOCAL_SETTINGS,
    context: { ...DEFAULT_LOCAL_SETTINGS.context, ...settings?.context },
    tokenUsage: { ...DEFAULT_LOCAL_SETTINGS.tokenUsage, ...settings?.tokenUsage },
    notification: { ...DEFAULT_LOCAL_SETTINGS.notification, ...settings?.notification },
    activity: { ...DEFAULT_LOCAL_SETTINGS.activity, ...settings?.activity },
  };
}
```

- [ ] **Step 4: Run TypeScript check on the settings module**

Run: `cd frontend && pnpm typecheck`
Expected: no errors related to `local.ts`.

---

## Task 2: Create Activity context

**Files:**

- Create: `src/components/workspace/activity/context.tsx`

**Interfaces:**

- Consumes: `useThread()` (todos), `useSubtaskContext()` (tasks),
  `useLocalSettings()` (persistence).
- Produces: `ActivityContextType { open: boolean; setOpen: (open: boolean) => void; toggle: () => void }`.

- [ ] **Step 1: Write the context file**

```tsx
"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useThread } from "@/components/workspace/messages/context";
import { useLocalSettings } from "@/core/settings";
import { useSubtaskContext } from "@/core/tasks/context";

interface ActivityContextType {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

const ActivityContext = createContext<ActivityContextType | undefined>(
  undefined,
);

export function ActivityProvider({ children }: { children: ReactNode }) {
  const { thread } = useThread();
  const { tasks } = useSubtaskContext();
  const [localSettings, setLocalSettings] = useLocalSettings();

  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  const hasActiveSubagents = Object.values(tasks).some(
    (task) => task.status === "in_progress" || task.status === "idle",
  );
  const shouldBeOpen = hasTodos || hasActiveSubagents;

  const [open, setOpenState] = useState(
    () => localSettings.activity?.open ?? false,
  );
  const wasActiveRef = useRef(shouldBeOpen);

  useEffect(() => {
    if (shouldBeOpen && !wasActiveRef.current) {
      setOpenState(true);
    }
    wasActiveRef.current = shouldBeOpen;
  }, [shouldBeOpen]);

  const setOpen = useCallback(
    (next: boolean) => {
      setOpenState(next);
      setLocalSettings("activity", { open: next });
    },
    [setLocalSettings],
  );

  const toggle = useCallback(() => {
    setOpen(!open);
  }, [open, setOpen]);

  return (
    <ActivityContext.Provider value={{ open, setOpen, toggle }}>
      {children}
    </ActivityContext.Provider>
  );
}

export function useActivity() {
  const context = useContext(ActivityContext);
  if (context === undefined) {
    throw new Error("useActivity must be used within an ActivityProvider");
  }
  return context;
}
```

- [ ] **Step 2: Verify no TypeScript errors**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 3: Add sidebar variant to TodoList

**Files:**

- Modify: `src/components/workspace/todo-list.tsx`

**Interfaces:**

- Consumes: `Todo` array, optional `variant` prop.
- Produces: same component, now supports `variant="sidebar"`.

- [ ] **Step 1: Add `variant` prop and adjust styles**

```tsx
export function TodoList({
  className,
  todos,
  collapsed: controlledCollapsed,
  hidden = false,
  onToggle,
  variant = "composer",
}: {
  className?: string;
  todos: Todo[];
  collapsed?: boolean;
  hidden?: boolean;
  onToggle?: () => void;
  variant?: "composer" | "sidebar";
}) {
  // ... existing state logic unchanged

  return (
    <div
      className={cn(
        "flex h-fit w-full origin-bottom flex-col overflow-hidden rounded-t-xl border border-b-0 bg-white backdrop-blur-sm transition-all duration-200 ease-out",
        variant === "composer" && "translate-y-4",
        hidden ? "pointer-events-none translate-y-8 opacity-0" : "",
        className,
      )}
    >
      <header
        className={cn(
          "bg-accent flex min-h-8 shrink-0 cursor-pointer items-center justify-between px-4 text-sm transition-all duration-300 ease-out",
        )}
        onClick={handleToggle}
      >
        {/* ... existing header content unchanged */}
      </header>
      <main
        className={cn(
          "bg-accent flex grow px-2 transition-all duration-300 ease-out",
          collapsed ? "h-0 pb-3" : variant === "composer" ? "h-28 pb-4" : "pb-4",
        )}
      >
        {/* ... existing QueueList unchanged */}
      </main>
    </div>
  );
}
```

Only the outer `translate-y-4` and the expanded `h-28` should be conditional on
`variant === "composer"`. The sidebar variant grows to content instead of a
fixed height.

- [ ] **Step 2: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 4: Create SubagentList component

**Files:**

- Create: `src/components/workspace/activity/subagent-list.tsx`

**Interfaces:**

- Consumes: `Subtask[]` array.
- Produces: clickable list items that scroll to `[data-task-id]` markers.

- [ ] **Step 1: Write the component**

```tsx
"use client";

import {
  CheckCircleIcon,
  ClockIcon,
  Loader2Icon,
  PauseCircleIcon,
  XCircleIcon,
} from "lucide-react";

import type { Subtask } from "@/core/tasks/types";
import { cn } from "@/lib/utils";

function statusIcon(status: Subtask["status"]) {
  switch (status) {
    case "completed":
      return <CheckCircleIcon className="size-3.5 text-green-500" />;
    case "failed":
      return <XCircleIcon className="size-3.5 text-red-500" />;
    case "expired":
      return <ClockIcon className="size-3.5 text-red-500" />;
    case "idle":
      return <PauseCircleIcon className="size-3.5 text-yellow-500" />;
    case "in_progress":
    default:
      return <Loader2Icon className="size-3.5 animate-spin" />;
  }
}

export function SubagentList({ tasks }: { tasks: Subtask[] }) {
  const handleClick = (taskId: string) => {
    const element = document.querySelector(`[data-task-id="${taskId}"]`);
    element?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <ul className="flex flex-col gap-1">
      {tasks.map((task) => (
        <li key={task.id}>
          <button
            type="button"
            onClick={() => handleClick(task.id)}
            className={cn(
              "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
            )}
          >
            <span className="mt-0.5 shrink-0">{statusIcon(task.status)}</span>
            <span className="min-w-0 flex-1">
              <span className="font-semibold">[{task.subagent_type}]</span>{" "}
              <span className="text-muted-foreground line-clamp-2">
                {task.description}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 2: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 5: Create ActivityPanel component

**Files:**

- Create: `src/components/workspace/activity/activity-panel.tsx`

**Interfaces:**

- Consumes: `useThread()` (todos), `useSubtaskContext()` (tasks).
- Produces: sidebar panel UI with Todo and Subagent sections.

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { ActivityIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useThread } from "@/components/workspace/messages/context";
import { TodoList } from "@/components/workspace/todo-list";
import { useSubtaskContext } from "@/core/tasks/context";

import { SubagentList } from "./subagent-list";

export function ActivityPanel({ onClose }: { onClose: () => void }) {
  const { thread } = useThread();
  const { tasks } = useSubtaskContext();

  const todos = thread.values.todos ?? [];
  const activeTasks = Object.values(tasks).filter(
    (task) =>
      task.status === "in_progress" ||
      task.status === "idle" ||
      task.status === "completed" ||
      task.status === "failed",
  );

  return (
    <div className="bg-background flex h-full flex-col border-r">
      <header className="flex h-12 shrink-0 items-center justify-between border-b px-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ActivityIcon className="size-4" />
          Activity
        </div>
        <Button size="icon-sm" variant="ghost" onClick={onClose}>
          <XIcon className="size-4" />
        </Button>
      </header>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-4 p-3">
          {todos.length > 0 && (
            <section>
              <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase">
                To-dos
              </h3>
              <TodoList
                todos={todos}
                collapsed={false}
                variant="sidebar"
                className="rounded-lg border"
              />
            </section>
          )}
          {activeTasks.length > 0 && (
            <section>
              <h3 className="text-muted-foreground mb-2 text-xs font-semibold uppercase">
                Subagents
              </h3>
              <SubagentList tasks={activeTasks} />
            </section>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
```

- [ ] **Step 2: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 6: Create ActivityTrigger component and index export

**Files:**

- Create: `src/components/workspace/activity/activity-trigger.tsx`
- Create: `src/components/workspace/activity/index.ts`

**Interfaces:**

- Consumes: `useActivity()`.
- Produces: header button that toggles Activity panel.

- [ ] **Step 1: Write the trigger**

```tsx
"use client";

import { ActivityIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/workspace/tooltip";

import { useActivity } from "./context";

export function ActivityTrigger() {
  const { open, toggle } = useActivity();

  return (
    <Tooltip content={open ? "Hide activity" : "Show activity"}>
      <Button
        className={cn(
          "text-muted-foreground hover:text-foreground",
          open && "text-foreground bg-accent",
        )}
        variant="ghost"
        size="icon-sm"
        onClick={toggle}
      >
        <ActivityIcon />
      </Button>
    </Tooltip>
  );
}
```

- [ ] **Step 2: Add `cn` import**

```tsx
import { cn } from "@/lib/utils";
```

- [ ] **Step 3: Write `index.ts`**

```ts
export { ActivityPanel } from "./activity-panel";
export { ActivityProvider, useActivity } from "./context";
export { ActivityTrigger } from "./activity-trigger";
```

- [ ] **Step 4: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 7: Add `data-task-id` marker to inline SubtaskCard

**Files:**

- Modify: `src/components/workspace/messages/subtask-card.tsx`

**Interfaces:**

- Consumes: existing `taskId` prop.
- Produces: outer element has `data-task-id={taskId}`.

- [ ] **Step 1: Add the marker to the outer `ChainOfThought`**

```tsx
<ChainOfThought
  data-task-id={taskId}
  className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
  open={!collapsed}
>
```

- [ ] **Step 2: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 8: Modify ChatBox to nest Activity + Files vertically

**Files:**

- Modify: `src/components/workspace/chats/chat-box.tsx`

**Interfaces:**

- Consumes: `useActivity()` (open state), `useFileBrowser()` (Files open state),
  existing `useArtifacts()`.
- Produces: left column rendered as vertical Activity-over-Files group.

- [ ] **Step 1: Add imports**

```tsx
import {
  ActivityPanel,
  useActivity,
} from "@/components/workspace/activity";
```

- [ ] **Step 2: Replace layout constants and compute left-column open state**

Replace the existing constants with names that refer to the left column instead
of the file browser:

```tsx
// Horizontal layouts: { "left-column", chat, artifacts } percentages
const LAYOUT_BOTH_CLOSED = { "left-column": 0, chat: 100, artifacts: 0 };
const LAYOUT_LEFT_ONLY = { "left-column": 20, chat: 80, artifacts: 0 };
const LAYOUT_ARTIFACTS_ONLY = { "left-column": 0, chat: 60, artifacts: 40 };
const LAYOUT_BOTH_OPEN = { "left-column": 18, chat: 47, artifacts: 35 };

// Vertical layouts inside the left column: { activity, files }
const VERTICAL_ACTIVITY_ONLY = { activity: 100, files: 0 };
const VERTICAL_FILES_ONLY = { activity: 0, files: 100 };
const VERTICAL_BOTH_OPEN = { activity: 35, files: 65 };
```

- [ ] **Step 3: Consume Activity state and compute flags**

Inside `ChatBox`:

```tsx
const { open: activityOpen, setOpen: setActivityOpen } = useActivity();

const leftColumnOpen = fileBrowserOpen || activityOpen;
```

- [ ] **Step 4: Update layout memo to use `leftColumnOpen`**

```tsx
const layout = useMemo(() => {
  const hasLeft = leftColumnOpen;
  const hasArtifacts = artifactPanelOpen;

  if (hasLeft && hasArtifacts) return LAYOUT_BOTH_OPEN;
  if (hasLeft) return LAYOUT_LEFT_ONLY;
  if (hasArtifacts) return LAYOUT_ARTIFACTS_ONLY;
  return LAYOUT_BOTH_CLOSED;
}, [leftColumnOpen, artifactPanelOpen]);
```

- [ ] **Step 5: Compute inner vertical layout**

```tsx
const verticalLayout = useMemo(() => {
  if (activityOpen && fileBrowserOpen) return VERTICAL_BOTH_OPEN;
  if (activityOpen) return VERTICAL_ACTIVITY_ONLY;
  if (fileBrowserOpen) return VERTICAL_FILES_ONLY;
  return VERTICAL_FILES_ONLY;
}, [activityOpen, fileBrowserOpen]);
```

Add a ref for the nested group:

```tsx
const leftGroupRef = useRef<GroupImperativeHandle>(null);

useEffect(() => {
  if (leftGroupRef.current) {
    leftGroupRef.current.setLayout(verticalLayout);
  }
}, [verticalLayout]);
```

- [ ] **Step 6: Replace the left panel JSX**

Replace the existing `ResizablePanel` with `id="file-browser"` with this nested
structure:

```tsx
{/* ── Left column: Activity (top) + Files (bottom) ── */}
<ResizablePanel
  className={cn(
    "transition-all duration-300 ease-in-out",
    !leftColumnOpen && "opacity-0",
  )}
  defaultSize={0}
  id="left-column"
>
  <div
    className={cn(
      "h-full transition-transform duration-300 ease-in-out",
      leftColumnOpen ? "translate-x-0" : "-translate-x-full",
    )}
  >
    <ResizablePanelGroup
      id={`${resizableIdBase}-left-panels`}
      orientation="vertical"
      defaultLayout={{ activity: 0, files: 100 }}
      resizeTargetMinimumSize={{ coarse: 0, fine: 0 }}
      groupRef={leftGroupRef}
    >
      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !activityOpen && "opacity-0",
        )}
        id="activity"
      >
        <div
          className={cn(
            "h-full transition-transform duration-300 ease-in-out",
            activityOpen ? "translate-y-0" : "-translate-y-full",
          )}
        >
          <ActivityPanel onClose={() => setActivityOpen(false)} />
        </div>
      </ResizablePanel>

      <ResizableHandle
        disabled
        className={cn(
          "opacity-33 hover:opacity-100",
          !(activityOpen && fileBrowserOpen) &&
            "pointer-events-none opacity-0",
        )}
      />

      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !fileBrowserOpen && "opacity-0",
        )}
        id="files"
      >
        <div
          className={cn(
            "h-full transition-transform duration-300 ease-in-out",
            fileBrowserOpen ? "translate-y-0" : "translate-y-full",
          )}
        >
          <FileBrowserPanel
            threadId={threadId}
            onClose={() => setFileBrowserOpen(false)}
          />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  </div>
</ResizablePanel>
```

- [ ] **Step 7: Update handle IDs and panel IDs**

The first `ResizableHandle` (between left column and chat) can keep the same
`id={`${resizableIdBase}-file-browser-separator`}` for persisted layout, or rename
it to `-left-column-separator`. Renaming is clearer but resets user panel sizes;
keep the old id to preserve existing layout cookies:

```tsx
<ResizableHandle
  id={`${resizableIdBase}-file-browser-separator`}
  disabled
  className={cn(!leftColumnOpen && "pointer-events-none opacity-0")}
/>
```

- [ ] **Step 8: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 9: Update chat page to wrap ChatBox and wire header trigger

**Files:**

- Modify: `src/app/workspace/chats/[thread_id]/page.tsx`

**Interfaces:**

- Consumes: `ActivityProvider`, `ActivityTrigger`.
- Produces: chat page no longer renders inline `TodoList`; Activity panel is
  available in the left column.

- [ ] **Step 1: Update imports**

Add:

```tsx
import {
  ActivityProvider,
  ActivityTrigger,
} from "@/components/workspace/activity";
```

Remove:

```tsx
import { TodoList } from "@/components/workspace/todo-list";
```

- [ ] **Step 2: Remove `hasTodos` variable and inline TodoList JSX**

Delete:

```tsx
const hasTodos = (thread.values.todos?.length ?? 0) > 0;
```

Delete the entire block that renders:

```tsx
{hasTodos && (
  <div ...>
    <TodoList className="bg-background/5" todos={...} hidden={false} />
  </div>
)}
```

- [ ] **Step 3: Add `ActivityTrigger` to header**

Next to `FileBrowserTrigger`:

```tsx
<SidebarTrigger className="md:hidden" />
<FileBrowserTrigger />
<ActivityTrigger />
```

- [ ] **Step 4: Wrap `ChatBox` with `ActivityProvider`**

```tsx
<ThreadContext.Provider value={{ ... }}>
  <ActivityProvider>
    <ChatBox threadId={threadId}>{/* existing children */}</ChatBox>
  </ActivityProvider>
</ThreadContext.Provider>
```

- [ ] **Step 5: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 10: Mirror changes in agent chat page and fix its provider stack

**Files:**

- Modify: `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Modify: `src/app/workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx`

**Interfaces:**

- Consumes: same Activity components.
- Produces: agent chat has the same Activity sidebar behavior and a complete
  provider stack.

- [ ] **Step 1: Add `FileBrowserProvider` and `ActivityProvider` to agent chat layout**

```tsx
"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { FileBrowserProvider } from "@/components/workspace/file-browser/context";
import { SubtasksProvider } from "@/core/tasks/context";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <FileBrowserProvider>
          <PromptInputProvider>{children}</PromptInputProvider>
        </FileBrowserProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
```

- [ ] **Step 2: Update agent chat page imports**

Add:

```tsx
import {
  ActivityProvider,
  ActivityTrigger,
} from "@/components/workspace/activity";
```

Remove:

```tsx
import { TodoList } from "@/components/workspace/todo-list";
```

- [ ] **Step 3: Remove `hasTodos` and inline TodoList JSX from agent chat page**

Same as Task 9.

- [ ] **Step 4: Add `ActivityTrigger` to agent chat header**

Place after the agent badge or before it, consistent with the main chat page.

- [ ] **Step 5: Wrap `ChatBox` with `ActivityProvider`**

Same as Task 9.

- [ ] **Step 6: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 11: Add i18n keys

**Files:**

- Modify: `src/core/i18n/locales/en-US.json`
- Modify: `src/core/i18n/locales/zh-CN.json`

**Interfaces:**

- Consumes: existing i18n JSON structure.
- Produces: new `activity` namespace for tooltip text.

- [ ] **Step 1: Add English keys**

```json
{
  "activity": {
    "show": "Show activity",
    "hide": "Hide activity",
    "todos": "To-dos",
    "subagents": "Subagents"
  }
}
```

- [ ] **Step 2: Add Chinese keys**

```json
{
  "activity": {
    "show": "显示活动面板",
    "hide": "隐藏活动面板",
    "todos": "待办事项",
    "subagents": "子代理"
  }
}
```

- [ ] **Step 3: Update `ActivityTrigger` to use i18n**

```tsx
const { t } = useI18n();
// ...
<Tooltip content={open ? t.activity.hide : t.activity.show}>
```

- [ ] **Step 4: Update `ActivityPanel` labels to use i18n**

```tsx
<h3 className="...">{t.activity.todos}</h3>
<h3 className="...">{t.activity.subagents}</h3>
<div className="...">{t.activity.title}</div>
```

Add a `title` key (`"Activity"` / `"活动"`) for the panel header.

- [ ] **Step 5: Run TypeScript check**

Run: `cd frontend && pnpm typecheck`
Expected: no errors.

---

## Task 12: Verify and test

- [ ] **Step 1: Run lint + type check**

Run: `cd frontend && pnpm check`
Expected: no lint or type errors.

- [ ] **Step 2: Run unit tests**

Run: `cd frontend && pnpm test`
Expected: existing tests pass; fix any regressions.

- [ ] **Step 3: Update E2E tests if they assert the old layout**

Search for selectors referencing `TodoList` above the composer or the old panel
IDs in `frontend/tests/e2e/`.

Run: `cd frontend && grep -R "TodoList\|file-browser\|hasTodos" tests/`
Update selectors to match the new DOM (Activity panel on the left).

- [ ] **Step 4: Run E2E tests**

Run: `cd frontend && pnpm test:e2e`
Expected: tests pass or only fail for unrelated reasons.

- [ ] **Step 5: Manual smoke test in dev server**

Run: `cd frontend && pnpm dev`
Verify:

1. Activity panel opens on the left when a todo or running subagent exists.
2. Files panel sits below Activity when both are open.
3. Clicking the header Activity trigger toggles the panel.
4. Clicking a subagent in Activity scrolls to its inline card.
5. Artifacts panel opens on the right without covering the left column.
6. Todos are no longer above the composer input.
7. Refreshing the page restores the last Activity open/close state.

---

## Self-review

- **Spec coverage:**
  - Left Activity + Files stacked layout → Task 8.
  - Todos moved out of composer → Tasks 3, 9, 10.
  - Subagent live status in sidebar with click-to-scroll → Tasks 4, 5, 7.
  - Artifacts remains right-docked → unchanged (Task 8 preserves it).
  - Persistence → Tasks 1, 2.
  - Auto-open behavior → Task 2.
- **Placeholder scan:** No TBD/TODO/fill-in-details; every step has code or exact
  command.
- **Type consistency:** `LocalSettings["activity"]` used in `local.ts`,
  `ActivityProvider`, and `useLocalSettings` setter. `Subtask[]` flows from
  `useSubtaskContext` to `SubagentList`. `useActivity` returns the same shape
  everywhere.

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-07-27-chat-activity-sidebar.md`.

**Recommended execution mode:** Subagent-Driven Development
(`superpowers:subagent-driven-development`) — each task above is small, has a
clear deliverable, and can be reviewed independently.

**Alternative:** Inline execution (`superpowers:executing-plans`) if you prefer
implementing all tasks in this session with checkpoints.
