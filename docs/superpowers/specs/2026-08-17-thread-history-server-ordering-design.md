# 线程历史消息：服务端有序化与持久化（方案 B）

日期：2026-08-17
状态：已获用户批准（2026-08-17）

## 问题

`frontend/src/core/threads/hooks.ts` 的历史消息拼接存在两个缺陷：

1. **上下文压缩（SummarizationMiddleware）后历史展示顺序混乱**。
2. **压缩抢救归档仅存在于 React 内存态**（`appendedMessages` state + `pendingArchivedMessagesRef`），页面刷新即丢失。

## 根因

- 前端 `useThreadHistory` 按 **run 维度**分页拉取消息（`GET /api/threads/{tid}/runs/{rid}/messages`），再手动 prepend 拼接（`dedupeRunMessagesByIdentity([..._messages, ...prev])`）。顺序依赖 run 列表顺序与拼接方向，是前端重复实现服务端已有的排序能力。
- 压缩触发时，`onUpdateEvent` 把被 `RemoveMessage(ALL)` 移除的旧轮次抢救进 `appendedMessages`，而 `buildVisibleHistoryMessages` 将其**无条件追加到历史末尾**。当该 run 的消息之后经分页进入 `messageRows` 时，`dedupeMessagesByIdentity`"同 id 取最后一次出现"的规则会把抢救块"搬"到历史尾部，与其他消息错位 → 顺序混乱。
- `mergeMessages` 的"历史后缀与 thread.messages 前缀连续重叠"假设在压缩改写 tail 后失效，cutoff 误判导致缺漏/重复。
- 后端事件存储（`run_events`，SQL/JSONL）本就持有**线程级全局 seq 升序**的完整消息记录，且 `RunJournal` 在 run 进行中即落库；后端已有线程级端点 `GET /api/threads/{thread_id}/messages`（`backend/app/gateway/routers/thread_runs.py:692`），返回全局有序、带 feedback 与 turn_duration 的消息，目前仅被测试消费。

## 设计

顺序与持久化的权威移交服务端；前端只渲染，不再重拼顺序、不再本地抢救。

### 1. 后端：线程级消息端点升级为分页形状（小改）

`GET /api/threads/{thread_id}/messages`（`thread_runs.py:692` `list_thread_messages`）：

- 响应从裸 `list[dict]` 改为 `{data: [...], has_more: bool}`，与 per-run 端点一致；复用 `backend/app/gateway/pagination.py::trim_run_message_page` 的 limit+1 探测逻辑。
- 保留 `before_seq`/`after_seq`/`limit` 游标（默认取最新一页，升序返回）、feedback 附着、turn_duration 注入。
- 消费方排查结论：前端、IM channels、`DeerFlowClient` 均未消费此端点，仅测试使用，改形状安全。
- 更新 `backend/tests/test_thread_messages_feedback.py` 与 `backend/tests/test_thread_run_messages_pagination.py`；新增多 run 交错 seq 的全局排序回归测试（TDD，`backend/tests/`，先写测试）。

### 2. 前端：`useThreadHistory` 重写为线程级无限查询

- 删除"逐 run 分页 + prepend"加载器及配套状态机：`loadedRunIdsRef`、`runBeforeSeqRef`、`loadGenerationRef`、`pendingLoadRef`、`loadingRunIdRef`、`indexRef`、`dedupeRunMessagesByIdentity`、`buildRunMessagesUrl`、`findLatestUnloadedRunIndex`、`shouldAutoContinueOnEmptyRun`、`getNextRunMessagesBeforeSeq` 等仅服务于 per-run 加载的辅助函数。
- 改用 `useInfiniteQuery`：
  - queryKey：`["thread", threadId, "messages"]`
  - queryFn：`GET /api/threads/{id}/messages?limit=100[&before_seq={cursor}]`
  - 首页取最新一页；`fetchPreviousPage` 以本页最旧 `seq` 为 `before_seq` 向上翻历史；扁平化后天然全局 seq 升序。
- superseded run（regenerate）过滤不变：仍用 `useThreadRuns` + `getSupersededRunIds` 按 `run_id` 过滤（`buildVisibleHistoryMessages` 保留过滤职责，移除 appendedMessages 入参）。
- middleware caller 过滤（`metadata.caller` 以 `middleware:` 开头）保留在客户端。
- `RunMessage` 类型增加可选 `feedback` 字段（线程级端点行附带）。

### 3. 删除压缩抢救机制（约 200 行）

移除：`appendedMessages`/`appendMessages` state、`pendingArchivedMessagesRef`、`pendingArchiveThreadIdRef`、`summarizedRef`、`computeSummarizationMovedMessages`、`resolvePreservedHistory`、`pruneConfirmedArchivedMessages`，以及 `onUpdateEvent` 中的 summarization 抢救分支。

替代：`onUpdateEvent` 检测到 summarization middleware 更新（`getSummarizationMiddlewareMessages` 保留）时，仅 `queryClient.invalidateQueries(["thread", threadId, "messages"])`。被压缩移除的旧轮次在事件存储中本就有记录，重新拉取即得，无需前端抢救。`onFinish` 同样 invalidate 该 query。

### 4. 合并与持久化语义

- `mergeMessages(history, thread.messages, optimistic)` 基本不动：history 已全局有序；dedupe"同 id 取最后出现"让 live 副本胜出，而 retained tail 时序上本来就在最后，顺序自洽；压缩改写 tail 导致的 cutoff 误判问题随之消失。
- **刷新持久化自然解决**：全部历史为服务端状态，刷新后 TanStack Query 重新拉取即得完整有序历史；不引入 localStorage/IndexedDB。

### 5. 测试

- 后端（TDD 强制）：先更新/新增测试再改实现——形状更新 + 多 run 交错 seq 排序回归；`cd backend && make test`、`make lint`、`make format`。
- 前端：`frontend/tests/unit/` 新增/更新 hooks 测试（含"压缩后刷新：历史完整且有序"回归）；清理引用已删导出函数的旧测试；`pnpm check`、`pnpm test`。

## 风险与边界

- 进行中 run 的事件落库与 invalidate 拉取之间存在毫秒级间隙：live stream 覆盖尾部，`mergeMessages` 去重兜底。
- 多次压缩叠加 regenerate 的组合场景由"服务端全局 seq + run_id 过滤"统一覆盖，不再有前端特判。
- 线程级端点响应形状变更需同步更新所有引用它的后端测试。

## 涉及文件

- `backend/app/gateway/routers/thread_runs.py`（`list_thread_messages`）
- `backend/app/gateway/pagination.py`（复用，可能微调）
- `backend/tests/test_thread_messages_feedback.py`、`backend/tests/test_thread_run_messages_pagination.py`、新增排序回归测试
- `frontend/src/core/threads/hooks.ts`（`useThreadHistory` 重写、抢救机制删除、`onUpdateEvent`/`onFinish` invalidate）
- `frontend/src/core/threads/types.ts`（`RunMessage.feedback?`）
- `frontend/tests/unit/` 对应 hooks 测试

## 文档同步

按仓库文档政策，实现完成后更新 `frontend/AGENTS.md`（Data Flow 中历史加载描述）与 `backend/AGENTS.md`（Thread Runs 端点表中 `/messages` 的响应形状）。
