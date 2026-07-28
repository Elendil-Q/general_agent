# Chat Activity Sidebar - Design Spec

- **Status:** Approved (brainstorming complete, pending implementation plan)
- **Date:** 2026-07-27
- **Topic:** Move the agent todo list and background-running subagent status into a left
  sidebar panel, stacked above the Files panel, while keeping the Artifacts panel docked
  on the right.

## 1. Goal

Redesign the chat workspace layout so that agent work state — the current todo list and
background-running subagents — is visible in a dedicated **Activity panel** on the left
side, stacked above the existing Files panel. This keeps the chat message stream focused
on conversation while giving the user a persistent, glanceable view of ongoing work.

Key user-confirmed decisions:

1. **Activity panel lives on the left**, in the same column as the Files panel.
2. **Activity on top, Files on bottom** inside that left column.
3. **Artifacts remains a docked right panel** (current behavior), not a temporary overlay,
   so it does not block interaction with Files or Activity.
4. **Todo list moves out of the composer area** and into the Activity panel.
5. **Subagent inline cards remain in the message stream** (they are part of conversation
   history); the Activity panel shows live subagent status and supports click-to-scroll
   to the corresponding message card.

## 2. Non-goals

- **No redesign of the workspace navigation sidebar** (`WorkspaceSidebar` on the far left)
  — that global nav stays unchanged.
- **No change to Artifacts rendering internals** — only its spatial relationship/panel
  arrangement is preserved.
- **No new subagent execution semantics** — this is a frontend layout and data-display
  change only; the existing `SubtaskContext`, SSE events, and status contract stay the
  same.
- **No mobile-first redesign** — mobile behavior is limited to sensible fallback
  (collapse/drawer) without introducing new interaction paradigms.
- **No drag-to-reorder todos or manual subagent management** in the Activity panel for
  this iteration.

## 3. Success criteria

1. The chat layout renders as: global nav | left column (Activity + Files) | chat | Artifacts.
2. Activity panel contains a Todos section and a Subagents section.
3. Todos are no longer rendered above the composer input.
4. Activity panel auto-expands when there are active todos or running/idle subagents;
   it can be manually toggled from the chat header.
5. Files panel remains usable below Activity; both panels can be collapsed independently.
6. Artifacts panel opens on the right without covering the left Activity/Files column.
7. Clicking a subagent in the Activity panel scrolls the message list to its inline card.
8. Panel open/closed state and split height are persisted in `useLocalSettings`.

## 4. Context & constraints

### Current layout

The chat page (`src/app/workspace/chats/[thread_id]/page.tsx`) wraps its content in
`<ChatBox threadId>`. `ChatBox` (`src/components/workspace/chats/chat-box.tsx`) renders
a horizontal `ResizablePanelGroup` from `react-resizable-panels` with three panels:

```
[Files] | [Chat] | [Artifacts]
```

- Files panel is toggled by `FileBrowserTrigger` and uses `FileBrowserProvider`.
- Artifacts panel is toggled by `ArtifactTrigger` and uses `ArtifactsProvider`.
- Panel sizes are set imperatively via `groupRef.setLayout()` based on which panels are
  open (`LAYOUT_BOTH_CLOSED`, `LAYOUT_FILE_BROWSER_ONLY`, etc.).

### Current todo rendering

- Data shape: `Todo { content?: string; status?: "pending" | "in_progress" | "completed" }`
  (`src/core/todos/types.ts`).
- Source: `thread.values.todos` from `useThread()` (`AgentThreadState` channel).
- Renderer: `src/components/workspace/todo-list.tsx`, currently shown above the composer
  in the chat page when `hasTodos` is true.

### Current subagent rendering

- Data shape: `Subtask` (`src/core/tasks/types.ts`) with `status`, `subagent_type`,
  `description`, `latestMessage`, `result`, `error`.
- Source: `SubtaskContext` (`src/core/tasks/context.tsx`), populated from inline message
  stream parsing (`message-list.tsx`) and SSE custom events
  (`src/core/threads/custom-events.ts`).
- Inline cards: `SubtaskCard` in the message stream.
- Status contract: `contracts/subagent_status_contract.json` (already consumed).

### Styling & state

- Tailwind CSS v4 with shadcn UI primitives.
- `useLocalSettings` (`src/core/settings/hooks.ts`) persists per-user preferences in
  `localStorage` under key `deerflow.local-settings`.
- `cn()` from `src/lib/utils` for conditional classes.

## 5. Design

### 5.1 Layout

Final layout:

```
[WorkspaceSidebar] | [Activity] | [Chat] | [Artifacts]
                     [Files]
```

Within `ChatBox`, the leftmost `ResizablePanel` becomes a vertical panel group containing
Activity (top) and Files (bottom):

```
ResizablePanelGroup direction="horizontal"
  ResizablePanel (left column)
    ResizablePanelGroup direction="vertical"
      ResizablePanel (Activity)
      ResizableHandle
      ResizablePanel (Files)
  ResizableHandle
  ResizablePanel (chat)
  ResizableHandle
  ResizablePanel (artifacts)
```

Approach selected: **independent top/bottom resizable panels** inside the left column.
This matches the user's "分上下" intuition and gives independent control over Activity
and Files.

### 5.2 Activity panel structure

A new component (e.g., `ActivityPanel` in `src/components/workspace/activity/`) renders
inside the top-left panel. Internally it stacks two collapsible sections:

1. **Todos section**
   - Reuses existing `TodoList` component (`src/components/workspace/todo-list.tsx`).
   - Collapsible header showing count and status summary.
   - Empty state hidden by default; section auto-collapses when no todos.

2. **Subagents section**
   - New component or reuse `SubtaskCard` in a compact list form.
   - Shows subagents with statuses `in_progress`, `idle`, and optionally the most recent
     `completed`/`failed` (configurable, default to running + idle).
   - Each item displays `subagent_type`, description truncated to one line, status icon,
     and shimmer/animation while running.
   - Clicking an item scrolls `MessageList` to the corresponding inline `SubtaskCard`
     (use the existing `taskId` as anchor).

Both sections use `ScrollArea` when content exceeds available height.

### 5.3 Toggles & auto-open behavior

- Add an `ActivityTrigger` button in the chat header (next to `FileBrowserTrigger` and
  `ArtifactTrigger`).
- Activity panel auto-expands when `todos.length > 0` or any subagent has status
  `in_progress` / `idle`.
- Activity panel auto-collapses when todos are empty and no subagents are running/idle.
- Manual toggle overrides auto-state until the next stream event changes eligibility.
  This prevents the panel from fighting the user.
- Files panel behavior stays unchanged.

### 5.4 Persistence

Extend `useLocalSettings` (or add a new section to the existing schema in
`src/core/settings/local.ts`) with:

- `activityPanelOpen?: boolean` — manual user preference.
- `activityPanelHeight?: number` — Activity's share of the left column (pixels or
  percentage).
- `filesPanelHeight?: number` — Files' share of the left column.

Default initial sizes: Activity ~35%, Files ~65% of the left column when both are open.

### 5.5 Mobile / narrow viewport

- On narrow screens, collapse the Activity+Files left column into the existing mobile
  pattern: the outer `Sidebar` handles global nav; the Activity/Files panels collapse to
  icon buttons or a Sheet drawer.
- Minimum implementation: on `md` breakpoint and below, the Activity+Files column becomes
  a toggleable overlay/drawer or simply collapses to zero width, leaving chat full-width.

### 5.6 Data flow

```
thread.values.todos ──▶ ActivityPanel ──▶ TodoList
useSubtaskContext().tasks ──▶ ActivityPanel ──▶ SubagentList
```

- `ActivityPanel` consumes `useThread()` for todos.
- `ActivityPanel` consumes `useSubtaskContext()` for subagents.
- `ActivityPanel` uses `useLocalSettings` for open/height state.
- `MessageList` already renders inline `SubtaskCard`s; add stable DOM ids (`data-task-id`)
  so ActivityPanel can scroll to them.

## 6. Implementation notes

### New files / components

| Path | Purpose |
|------|---------|
| `src/components/workspace/activity/activity-panel.tsx` | Main Activity panel shell |
| `src/components/workspace/activity/activity-trigger.tsx` | Header toggle button |
| `src/components/workspace/activity/subagent-list.tsx` | Compact subagent list for sidebar |
| `src/components/workspace/activity/context.tsx` | Activity open state / auto-open logic provider |

### Modified files

| Path | Change |
|------|--------|
| `src/components/workspace/chats/chat-box.tsx` | Replace single Files panel with nested vertical group: Activity + Files |
| `src/app/workspace/chats/[thread_id]/providers.tsx` | Add `ActivityProvider` to the provider stack |
| `src/app/workspace/chats/[thread_id]/page.tsx` | Remove inline `<TodoList>` above composer; keep header trigger |
| `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` | Mirror the TodoList removal and provider setup |
| `src/core/settings/local.ts` | Add Activity panel preference keys |
| `src/components/workspace/messages/message-list.tsx` | Add stable `data-task-id` markers to subagent cards for scroll-to |

### Layout math

`chat-box.tsx` currently computes horizontal layouts with constants like
`LAYOUT_BOTH_CLOSED`. Add a new dimension for Activity:

- Horizontal layout variants now depend on `activityOpen` in addition to `fileBrowserOpen`
  and `artifactsOpen`.
- When the left column is open, its internal vertical split defaults to Activity:Files =
  `35:65`.
- Handles can be enabled for the left-column vertical split if desired; horizontal
  handles remain disabled (programmatic sizing only) unless the user wants resize.

### Animations

- Use the same `transition-all duration-300 ease-in-out` pattern already used by Files
  and Artifacts panels.
- Activity panel content sections can use `AnimatePresence`/height animation or simple
  conditional render.

## 7. Open questions / risks

1. **Subagent scroll-to**: The message list is virtualized or standard? Need to verify
   scroll behavior. Standard `MessageList` uses a scrollable container; `scrollIntoView`
   on the `data-task-id` element should work.
2. **Panel resize handles**: Should the Activity/Files vertical split be draggable? The
   design assumes yes for ergonomic use. If `react-resizable-panels` nested groups behave
   awkwardly in the current shadcn wrapper, fallback to a fixed ratio.
3. **Agent chat mirror**: `agents/[agent_name]/chats/[thread_id]/layout.tsx` currently
   lacks `FileBrowserProvider` even though `ChatBox` uses it. This needs to be fixed as
   part of touching that layout.
4. **Mobile interaction**: If Activity auto-opens on mobile due to running subagents,
   it could be intrusive. Consider disabling auto-open below `md`.

## 8. Acceptance checklist

- [ ] Activity panel renders on the left, above Files panel.
- [ ] Todos no longer appear above the composer.
- [ ] Activity header trigger toggles the panel.
- [ ] Activity auto-opens when todos or running subagents exist.
- [ ] Clicking a subagent in Activity scrolls to its inline card.
- [ ] Artifacts panel opens on the right without blocking left panels.
- [ ] Panel state/height persists across reloads.
- [ ] `pnpm check` passes; E2E/chat tests updated if they assert old layout.
