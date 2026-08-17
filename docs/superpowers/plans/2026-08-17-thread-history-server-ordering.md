# 线程历史消息服务端有序化与持久化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把线程历史消息的顺序权威与持久化移交服务端：后端线程级消息端点升级为 `{data, has_more}` 分页形状，前端 `useThreadHistory` 重写为线程级 `useInfiniteQuery` 并删除全部压缩抢救机制。

**Architecture:** 后端 `GET /api/threads/{thread_id}/messages` 复用 `trim_run_message_page` 做 limit+1 分页；前端按 `before_seq` 游标向上翻页（`fetchPreviousPage`），页面扁平化后天然全局 seq 升序；压缩发生时仅 `invalidateQueries` 重新拉取，不再本地抢救。刷新持久化由服务端状态自然解决。

**Tech Stack:** FastAPI + pytest（backend）；React 19 + TanStack Query v5 `useInfiniteQuery` + Rstest（frontend）。

**Spec:** `docs/superpowers/specs/2026-08-17-thread-history-server-ordering-design.md`

## Global Constraints

- 后端 TDD 强制：先写/改测试并确认失败，再实现。后端测试命令在 `backend/` 目录下运行。
- 后端代码风格：ruff，行宽 240，双引号；完成后跑 `make lint` / `make format`。
- 前端完成后跑 `pnpm check` 与 `pnpm test`；测试放 `frontend/tests/unit/`，用 `@/` 别名导入源码。
- 遵循 Conventional Commits；**任何 git commit 前需用户确认**。
- 不改 `mergeMessages` 的去重/cutoff 逻辑；superseded run 过滤仍走 `useThreadRuns` + `getSupersededRunIds`。
- 线程级端点仅被测试消费，改响应形状安全；per-run 端点 `/{rid}/messages` 保持不变。

---

### Task 1: 后端 — 线程级消息端点升级为 `{data, has_more}` 分页形状

**Files:**
- Modify: `backend/app/gateway/routers/thread_runs.py:692-756`（`list_thread_messages`）
- Test: `backend/tests/test_thread_messages_feedback.py`（改形状断言 + 新增分页/排序回归）

**Interfaces:**
- Consumes: `trim_run_message_page(rows, *, limit, after_seq) -> tuple[list[dict], bool]`（`backend/app/gateway/pagination.py:6`，已在 thread_runs.py 中导入并用于 per-run 端点）；`event_store.list_messages(thread_id, *, limit, before_seq, after_seq)`。
- Produces: `GET /api/threads/{thread_id}/messages` 响应 `{"data": [...], "has_more": bool}`；`data` 每行含 `seq`、`run_id`、`event_type`、`category`、`content`、`metadata`、`feedback`（最后 AI 消息附着或 null）、AI 消息 `content.additional_kwargs.turn_duration`（如有）。前端 Task 2/3 依赖此形状。

- [ ] **Step 1: 改现有测试适配新形状（先失败）**

`backend/tests/test_thread_messages_feedback.py` 中：
- `test_feedback_attached_to_last_ai_message_per_run`：`data = resp.json()` 改为 `data = resp.json()["data"]`。
- `test_no_feedback_query_when_thread_has_no_ai_message`：`resp.json()[0]["feedback"]` 改为 `resp.json()["data"][0]["feedback"]`。
- 文件内其余 `resp.json()` 当 list 用的断言同样加 `["data"]`（先 grep 该文件全部 `resp.json()` 出现处逐一改）。

- [ ] **Step 2: 新增分页形状与全局排序回归测试（先失败）**

在同一文件追加：

```python
def test_thread_messages_pagination_shape_and_cursor():
    # Store returns limit+1 rows -> has_more True, page trimmed to latest `limit` rows.
    rows = [_human("r1", s) for s in range(1, 4)]  # seqs 1,2,3; limit=2
    app, _ = _make_app(rows, {})

    resp = TestClient(app).get("/api/threads/t1/messages?limit=2&before_seq=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is True
    assert [m["seq"] for m in body["data"]] == [2, 3]


def test_thread_messages_pagination_after_seq_takes_head():
    rows = [_human("r1", s) for s in range(1, 4)]
    app, _ = _make_app(rows, {})

    resp = TestClient(app).get("/api/threads/t1/messages?limit=2&after_seq=0")
    body = resp.json()
    assert body["has_more"] is True
    assert [m["seq"] for m in body["data"]] == [1, 2]


def test_thread_messages_preserve_global_seq_order_across_runs():
    # Interleaved runs: endpoint must not regroup by run — store order (global seq) is authoritative.
    messages = [_human("r1", 1), _ai("r1", 2, "a1"), _human("r2", 3), _ai("r2", 4, "a2")]
    app, _ = _make_app(messages, {})

    resp = TestClient(app).get("/api/threads/t1/messages")
    assert [m["seq"] for m in resp.json()["data"]] == [1, 2, 3, 4]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_thread_messages_feedback.py -v`
Expected: FAIL —— 现有断言把响应当 list（`TypeError`/KeyError），新测试拿到裸 list 没有 `has_more` 键。

- [ ] **Step 4: 实现分页形状**

`backend/app/gateway/routers/thread_runs.py` 的 `list_thread_messages`：

```python
@router.get("/{thread_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_thread_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated displayable messages for a thread (across all runs), with feedback attached.

    Response: { data: [...], has_more: bool }
    """
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages(thread_id, limit=limit + 1, before_seq=before_seq, after_seq=after_seq)
    messages, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)

    # ……feedback 附着与 turn_duration 注入逻辑保持原样（作用于 trim 后的 messages）……

    return {"data": messages, "has_more": has_more}
```

要点：`limit` 加 `ge=1`；先 trim 再附着 feedback/turn_duration（原逻辑逐行不变，仅把返回从 `return messages` 改为 `return {"data": messages, "has_more": has_more}`）。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_thread_messages_feedback.py -v`
Expected: PASS（全部）

- [ ] **Step 6: 全仓引用排查 + 全量回归**

Run: `cd backend && grep -rn "threads/.*'/messages\|/messages\"" tests/ --include=*.py | grep -v runs/`（确认没有其他测试把线程级响应当 list；per-run 端点测试 `test_thread_run_messages_pagination.py` 不受影响、不需改）
Run: `cd backend && make test && make lint && make format`
Expected: 全绿。

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add backend/app/gateway/routers/thread_runs.py backend/tests/test_thread_messages_feedback.py
git commit -m "feat(gateway): paginate thread-level messages endpoint with {data, has_more}"
```

---

### Task 2: 前端 — 类型与线程级分页辅助函数（TDD）

**Files:**
- Modify: `frontend/src/core/threads/types.ts:26-35`（`RunMessage` 增加 `feedback`）
- Modify: `frontend/src/core/threads/hooks.ts`（新增 `buildThreadMessagesUrl`、`getThreadMessagesPreviousPageParam`、`ThreadMessagesPage` 类型、`threadMessagesQueryKey`、`THREAD_MESSAGES_PAGE_SIZE`）
- Test: `frontend/tests/unit/core/threads/message-merge.test.ts`（追加新辅助函数测试）

**Interfaces:**
- Consumes: 后端 Task 1 的 `{data, has_more}` 形状；既有 `runMessagesPageHasMore(result)`（hooks.ts:263）与 `getOldestRunMessageSeq(messages)`（hooks.ts:267）——形状通用，直接复用、不改名。
- Produces（Task 3 依赖的精确签名）:
  - `THREAD_MESSAGES_PAGE_SIZE = 100`
  - `threadMessagesQueryKey(threadId: string): readonly ["thread", string, "messages"]`
  - `type ThreadMessagesPage = { data: RunMessage[]; has_more?: boolean; hasMore?: boolean }`
  - `buildThreadMessagesUrl(baseUrl: string, threadId: string, beforeSeq?: number): string`
  - `getThreadMessagesPreviousPageParam(firstPage: ThreadMessagesPage): number | undefined`
  - `RunMessage.feedback?: { feedback_id: string; rating: string; comment: string | null } | null`

- [ ] **Step 1: 写失败测试**

在 `frontend/tests/unit/core/threads/message-merge.test.ts` 追加（import 列表加入 `buildThreadMessagesUrl`、`getThreadMessagesPreviousPageParam`、`threadMessagesQueryKey`、`THREAD_MESSAGES_PAGE_SIZE`）：

```ts
test("buildThreadMessagesUrl encodes thread id and optional before_seq", () => {
  const url = buildThreadMessagesUrl("http://localhost:8001/", "thread/a b", 41);
  expect(url).toBe(
    "http://localhost:8001/api/threads/thread%2Fa%20b/messages?before_seq=41",
  );
});

test("buildThreadMessagesUrl omits before_seq when loading the latest page", () => {
  expect(buildThreadMessagesUrl("", "t1")).toBe("/api/threads/t1/messages");
});

test("getThreadMessagesPreviousPageParam returns oldest seq when more pages exist", () => {
  expect(
    getThreadMessagesPreviousPageParam({
      data: [runMessage(30), runMessage(31)],
      has_more: true,
    }),
  ).toBe(30);
});

test("getThreadMessagesPreviousPageParam stops when no more pages exist", () => {
  expect(
    getThreadMessagesPreviousPageParam({ data: [runMessage(1)], has_more: false }),
  ).toBeUndefined();
});

test("getThreadMessagesPreviousPageParam stops when has_more lacks seq values", () => {
  expect(
    getThreadMessagesPreviousPageParam({ data: [runMessage()], has_more: true }),
  ).toBeUndefined();
});

test("threadMessagesQueryKey scopes messages under the thread key", () => {
  expect(threadMessagesQueryKey("t1")).toEqual(["thread", "t1", "messages"]);
});
```

（`runMessage(seq?)` 是该测试文件已有的 helper。）

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && pnpm exec rstest run tests/unit/core/threads/message-merge.test.ts`
Expected: FAIL —— 导入的函数不存在。

- [ ] **Step 3: 实现**

`frontend/src/core/threads/types.ts`：

```ts
export interface RunMessageFeedback {
  feedback_id: string;
  rating: string;
  comment: string | null;
}

export interface RunMessage {
  run_id: string;
  seq?: number;
  content: Message;
  metadata: {
    caller: string;
    [key: string]: unknown;
  };
  created_at: string;
  feedback?: RunMessageFeedback | null;
}
```

`frontend/src/core/threads/hooks.ts`（放在 `getNextRunMessagesBeforeSeq` 附近；`buildRunMessagesUrl` 在 Task 3 才删除，本任务不动）：

```ts
export const THREAD_MESSAGES_PAGE_SIZE = 100;

export function threadMessagesQueryKey(threadId: string) {
  return ["thread", threadId, "messages"] as const;
}

export type ThreadMessagesPage = {
  data: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

export function buildThreadMessagesUrl(
  baseUrl: string,
  threadId: string,
  beforeSeq?: number,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/messages`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  if (beforeSeq !== undefined) {
    url.searchParams.set("before_seq", String(beforeSeq));
  }
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
}

export function getThreadMessagesPreviousPageParam(
  firstPage: ThreadMessagesPage,
): number | undefined {
  if (!runMessagesPageHasMore(firstPage)) {
    return undefined;
  }
  return getOldestRunMessageSeq(firstPage.data) ?? undefined;
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && pnpm exec rstest run tests/unit/core/threads/message-merge.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add frontend/src/core/threads/types.ts frontend/src/core/threads/hooks.ts frontend/tests/unit/core/threads/message-merge.test.ts
git commit -m "feat(frontend): add thread-level messages pagination helpers"
```

---

### Task 3: 前端 — `useThreadHistory` 重写为 useInfiniteQuery + 删除抢救机制

**Files:**
- Modify: `frontend/src/core/threads/hooks.ts`（`useThreadHistory` 1724-1921 重写；`useThreadStream` 调用点 729-738、`onUpdateEvent` 876-899、refs 1101-1107、reset effect 1119-1131、prune effect 1136-1141、merge 输入 1547-1556、`onFinish` 1056-1070；删除死代码 168-184、231-304 部分、385-475）
- Test: `frontend/tests/unit/core/threads/message-merge.test.ts`（删除已删函数的测试；更新 `buildVisibleHistoryMessages` 签名测试）

**Interfaces:**
- Consumes: Task 2 的 `threadMessagesQueryKey` / `buildThreadMessagesUrl` / `getThreadMessagesPreviousPageParam` / `ThreadMessagesPage` / `THREAD_MESSAGES_PAGE_SIZE`；既有 `useThreadRuns`、`getSupersededRunIds`、`dedupeMessagesByIdentity`。
- Produces: `useThreadHistory(threadId, { enabled?, pendingSupersededRunIds? })` 返回 `{ runs, messages, loading, hasMore, loadMore }` —— **不再有 `appendMessages`**。`useThreadStream` 对外返回值（`isHistoryLoading` / `hasMoreHistory` / `loadMoreHistory`）签名不变。

- [ ] **Step 1: 更新/删除测试以反映新契约（先失败）**

`frontend/tests/unit/core/threads/message-merge.test.ts`：
- 从 import 移除并删除对应测试：`findLatestUnloadedRunIndex`（338-363 行附近 3 个）、`buildRunMessagesUrl`（313-336，3 个）、`getNextRunMessagesBeforeSeq`（301-311，2 个）、`shouldAutoContinueOnEmptyRun` + `MAX_CONSECUTIVE_EMPTY_RUN_LOADS`（510-535 及 536-628 两个模拟测试，含 #3352 回归——per-run 加载器已删，回归场景随之失效）、`computeSummarizationMovedMessages`（grep 该文件定位）、`resolvePreservedHistory`（629-669 附近 3 个，#3825 回归被服务端 invalidate 取代）、`pruneConfirmedArchivedMessages`（grep 定位）。
- `buildVisibleHistoryMessages filters superseded runs but keeps regenerated run`（408 起）：改为两参调用 `buildVisibleHistoryMessages(rows, supersededRunIds)`，去掉第三个 `appendedMessages` 实参。
- 保留不动：`mergeMessages`（34-135）、`getSummarizationMiddlewareMessages`（136-191）、`getVisibleOptimisticMessages` 系列、`getSupersededRunIds`、`removeSetItems`、`runMessagesPageHasMore`、`getOldestRunMessageSeq`。
- 追加新回归测试（压缩后顺序由服务端保证的纯函数层锚点）：

```ts
test("thread messages pages flattened oldest-page-first stay in global seq order", () => {
  // useInfiniteQuery fetchPreviousPage prepends older pages: pages[0] is the
  // oldest loaded page; flatMap preserves ascending global seq (#3825 follow-up).
  const pages = [
    { data: [runMessage(1), runMessage(2)] },
    { data: [runMessage(3), runMessage(4)] },
  ];
  const rows = pages.flatMap((p) => p.data);
  expect(rows.map((r) => r.seq)).toEqual([1, 2, 3, 4]);
});
```

Run: `cd frontend && pnpm exec rstest run tests/unit/core/threads/message-merge.test.ts`
Expected: FAIL —— `buildVisibleHistoryMessages` 仍是三参（新两参调用不报错，但源码还没改前删掉 import 会 fail；以编译/导入错误为失败信号即可）。

- [ ] **Step 2: 重写 `useThreadHistory`（hooks.ts 1719-1921 整体替换）**

```ts
type ThreadHistoryOptions = {
  enabled?: boolean;
  pendingSupersededRunIds?: ReadonlySet<string>;
};

export function useThreadHistory(
  threadId: string,
  { enabled = true, pendingSupersededRunIds }: ThreadHistoryOptions = {},
) {
  const runs = useThreadRuns(threadId, { enabled });
  const query = useInfiniteQuery({
    queryKey: threadMessagesQueryKey(threadId),
    queryFn: async ({ pageParam }) => {
      const url = buildThreadMessagesUrl(
        getBackendBaseURL(),
        threadId,
        pageParam,
      );
      const result: ThreadMessagesPage = await fetch(url, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
      }).then((res) => res.json());
      return result;
    },
    initialPageParam: undefined as number | undefined,
    getPreviousPageParam: getThreadMessagesPreviousPageParam,
    enabled: enabled && Boolean(threadId),
    refetchOnWindowFocus: false,
  });

  const supersededRunIds = useMemo(() => {
    return getSupersededRunIds(runs.data, pendingSupersededRunIds);
  }, [pendingSupersededRunIds, runs.data]);

  const messages = useMemo(() => {
    const rows = (query.data?.pages ?? []).flatMap((page) =>
      page.data.filter((m) => !m.metadata?.caller?.startsWith("middleware:")),
    );
    return buildVisibleHistoryMessages(rows, supersededRunIds);
  }, [query.data, supersededRunIds]);

  const hasThreadId = Boolean(threadId);
  const isRunsLoading =
    enabled &&
    hasThreadId &&
    (runs.isLoading || (runs.isFetching && !runs.data));
  const isRunsUnresolved =
    enabled && hasThreadId && !runs.data && !runs.isError;

  return {
    runs: runs.data,
    messages,
    loading: query.isLoading || isRunsLoading || isRunsUnresolved,
    hasMore: enabled && hasThreadId && Boolean(query.hasPreviousPage),
    loadMore: () => {
      void query.fetchPreviousPage();
    },
  };
}
```

同时把 `buildVisibleHistoryMessages`（217-229）改为两参：

```ts
export function buildVisibleHistoryMessages(
  messageRows: RunMessage[],
  supersededRunIds: ReadonlySet<string>,
) {
  const visibleRows = messageRows.filter(
    (message) => !supersededRunIds.has(message.run_id),
  );
  return dedupeMessagesByIdentity(visibleRows.map((message) => message.content));
}
```

- [ ] **Step 3: 删除 per-run 加载器死代码与抢救机制**

hooks.ts 中删除：
- `dedupeRunMessagesByIdentity`（168-184）
- `findLatestUnloadedRunIndex`（231-242）、`MAX_CONSECUTIVE_EMPTY_RUN_LOADS` + `shouldAutoContinueOnEmptyRun`（244-255）、`RunMessagesPageResponse` 类型 + `getNextRunMessagesBeforeSeq`（257-286）、`buildRunMessagesUrl`（288-304）
- 保留 `runMessagesPageHasMore`（263-265）与 `getOldestRunMessageSeq`（267-277）——Task 2 的新 helper 复用它们
- `computeSummarizationMovedMessages`（376-409 含 docstring）、`resolvePreservedHistory`（411-452 含 docstring）、`pruneConfirmedArchivedMessages`（454-475 含 docstring）
- `useThreadStream` 内：`pendingArchivedMessagesRef` / `pendingArchiveThreadIdRef` / `summarizedRef` 三个 ref（1096-1107 含注释）及 `summarizedRef.current ??= new Set()`（1115）；thread-switch reset effect 中的对应三行（1123-1125）；prune effect 整段（1133-1141 含注释）
- merge 输入处的 rescue 覆盖（1542-1551 含注释）：删 `rescueBuffer`/`effectiveHistory`，`mergeMessages(visibleHistory, persistedMessages, visibleOptimisticMessages)` 直接用 `visibleHistory`

- [ ] **Step 4: `useThreadStream` 接线改动**

1. 调用点（729-738）：destructure 去掉 `appendMessages`：

```ts
  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
  } = useThreadHistory(onStreamThreadId ?? "", {
    enabled: !isMock,
    pendingSupersededRunIds,
  });
```

2. `onUpdateEvent` 压缩分支（876-899）替换为 invalidate（保留 `messagesRef.current = []` —— 1532 行按长度更新的 guard 依赖它在压缩后重新同步）：

```ts
    onUpdateEvent(data) {
      const _messages = getSummarizationMiddlewareMessages(data);
      if (_messages && _messages.length >= 2) {
        // Summarization rewrote the live list (RemoveMessage(ALL) + summary +
        // retained tail). The removed turns remain in the server-side event
        // store, so refetch the server-ordered history instead of rescuing
        // them into React state (supersedes the #3825 archive buffer).
        messagesRef.current = [];
        if (threadIdRef.current && !isMock) {
          void queryClient.invalidateQueries({
            queryKey: threadMessagesQueryKey(threadIdRef.current),
          });
        }
      }
      // ……后续 title 更新逻辑不变……
    },
```

3. `onFinish`（1060-1070）：在 `["thread", threadIdRef.current]` 之外显式补一行（前缀匹配已覆盖，此处为可读性）：

```ts
        void queryClient.invalidateQueries({
          queryKey: threadMessagesQueryKey(threadIdRef.current),
        });
```

- [ ] **Step 5: 运行测试与静态检查**

Run: `cd frontend && pnpm exec rstest run tests/unit/core/threads/message-merge.test.ts && pnpm test && pnpm check`
Expected: 全 PASS；`pnpm check`（lint + typecheck）无错误。若有其他文件 import 了已删导出（先 `grep -rn "findLatestUnloadedRunIndex\|buildRunMessagesUrl\|resolvePreservedHistory\|computeSummarizationMovedMessages\|pruneConfirmedArchivedMessages\|shouldAutoContinueOnEmptyRun\|getNextRunMessagesBeforeSeq\|appendMessages" src/ tests/` 确认清零），一并清理。

- [ ] **Step 6: 手动验证（可选但推荐）**

`make dev` 起一个长线程触发 summarization（或调低 `summarization` 阈值），刷新页面：历史完整、按时间升序、无错位；"加载更早消息"向上翻页正常。

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add frontend/src/core/threads/hooks.ts frontend/tests/unit/core/threads/message-merge.test.ts
git commit -m "refactor(frontend): load thread history from server-ordered messages endpoint"
```

---

### Task 4: 文档同步与最终验证

**Files:**
- Modify: `frontend/AGENTS.md`（Data Flow 一节）
- Modify: `backend/AGENTS.md`（Thread Runs 端点表）
- 检查: `backend/docs/API.md`（若记录该端点形状则同步）

**Interfaces:**
- Consumes: Task 1-3 的全部产出。
- Produces: 无代码接口。

- [ ] **Step 1: 更新文档**

- `backend/AGENTS.md` Thread Runs 行：`GET /../messages` 描述改为 "paginated thread messages across runs `{data, has_more}` with feedback attached"。
- `frontend/AGENTS.md` Data Flow 增补一句：线程历史消息经 `useThreadHistory`（TanStack `useInfiniteQuery`，`GET /api/threads/{id}/messages`，`before_seq` 游标向上翻页）从服务端按全局 seq 有序加载；压缩后通过 invalidate 重新拉取，无本地归档。
- `grep -n "messages" backend/docs/API.md` 检查是否需同步响应形状。

- [ ] **Step 2: 全量最终验证**

Run: `cd backend && make test && make lint && make format`
Run: `cd frontend && pnpm check && pnpm test`
Expected: 全部通过。

- [ ] **Step 3: Commit（需用户确认）**

```bash
git add frontend/AGENTS.md backend/AGENTS.md backend/docs/API.md
git commit -m "docs: document server-ordered thread history messages"
```

---

## Self-Review 记录

- **Spec 覆盖**：spec §1→Task 1；§2→Task 2+3；§3→Task 3 Step 3/4；§4（mergeMessages 不动、持久化自然解决）→Task 3；§5 测试→各 Task TDD 步骤；文档同步→Task 4。无缺口。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`ThreadMessagesPage`、`threadMessagesQueryKey`、`getThreadMessagesPreviousPageParam`、`buildVisibleHistoryMessages`（两参）在 Task 2 定义、Task 3 消费，签名一致；`useThreadHistory` 返回值去掉 `appendMessages` 与 Task 3 Step 4 的 destructure 一致。
- **已知边界**：per-run 端点与其测试 `test_thread_run_messages_pagination.py`、`_run_message_pagination_helpers.py` 保持不变；`removeSetItems` 虽仅被测试引用但保留（超出本计划范围）。
