# Subagent Runtime Optimization - Design Spec

- **Status:** Approved (brainstorming complete, pending implementation plan)
- **Date:** 2026-07-25
- **Topic:** Optimize DeerFlow's subagent runtime to support detached (background) execution,
  cross-turn continuation, structured `yield` output, and decoupled observability - referencing
  the OMP (oh-my-pi) subagent mechanism survey.

## 1. Goal

Close the capability gap between DeerFlow's current subagent runtime and the OMP reference
(`~/CODE/oh-my-pi/docs/subagent-mechanism-survey.md`), focusing on three user-confirmed work
modes:

1. **Parallel investigation** - the lead agent dispatches long-running subagents and continues
   other work, collecting results later (today `task_tool` blocks the lead via 5s polling until
   the subagent terminates - "async" only enables interrupt/cancel, not parallelism).
2. **Cross-turn continuation** - a completed subagent stays revivable so the lead can follow up
   in a later turn ("based on that analysis, look deeper at X"), reusing the same session
   context instead of spawning fresh.
3. **Execution control & observability** - soft/hard request budgets (vs. today's `max_turns`
   only), structured `yield` output with schema validation, and a decoupled event bus so
   multiple subscribers (SSE bridge, logging, metrics, future RPC) can observe subagent
   lifecycle/progress without touching the executor.

Prewalk (dual-model plan/execute switching) is explicitly **out of scope** per user decision.

### Non-goals (v1)

- **No disk persistence / revival** - idle subagents live in process memory only; a gateway
  restart loses them (user-confirmed). The OMP `parked` -> `ensureLive()` JSONL revival path is
  not implemented.
- **No inter-subagent messaging** - no `hub` tool, no peer-to-peer IRC-style coordination.
  Subagents still communicate only through the lead (via `task`/`wait_for_tasks`/`follow_up`).
  The user did not select multi-agent collaboration.
- **No recursive spawning** - the existing hard ban on the `task` tool inside subagents
  (`subagent_enabled=False`, `disallowed_tools=["task"]`) is preserved. OMP's depth-limited
  recursion is not adopted.
- **No prewalk** - no automatic model switching on first edit.
- **No `output` schema editor UI** - frontend changes are limited to rendering new SSE event
  types and the two new tools; a full schema designer is a separate spec.

### Success criteria

1. The lead can call `task(..., detached=True)` and receive a `task_id` immediately, continue
   invoking other tools, then call `wait_for_tasks([ids])` to collect results - all within one
   lead turn.
2. A subagent whose `SubagentConfig.keep_alive=True` transitions to `IDLE` on completion instead
   of being cleaned up; a later `follow_up(task_id, prompt)` revives it with full context
   (same `subagent_thread_id` + checkpointer) across lead turns within one gateway process.
3. A subagent can call the `yield` tool to submit structured output; if `SubagentConfig.output`
   declares a JSON Schema, the terminal payload is validated (with graceful degradation on
   mismatch). Subagents that never call `yield` fall back to the existing last-AIMessage
   extraction - zero behavior change for existing configs.
4. `SubagentConfig.max_requests` enforces a soft budget (wrap-up prompt) and a 1.5x hard stop,
   independent of and co-existing with `max_turns`.
5. An `EventBus` broadcasts lifecycle/progress/budget events; the SSE bridge routes them to the
   correct lead run's stream writer by `thread_id`; a future subscriber (logger/metrics) can tap
   in with zero executor changes.
6. All existing `task` calls (no `detached` arg), existing `SubagentConfig` YAMLs (no new
   fields), and the INTERRUPTED/resume path (Route 甲) behave identically to today.

## 2. Context & constraints

### Existing systems (do not regress)

- **`task` tool** (`packages/harness/deerflow/tools/builtins/task_tool.py`) - the sole delegation
  entry. Currently a `while True` + `await asyncio.sleep(5)` polling loop blocking the lead until
  the subagent reaches a terminal state. Returns a result string consumed by
  `ToolErrorHandlingMiddleware` which stamps `subagent_status` on the `ToolMessage`.
- **`SubagentExecutor`** (`packages/harness/deerflow/subagents/executor.py:353`) - the runtime
  engine. Holds two module-level registries guarded by `_background_tasks_lock`:
  `_background_tasks: dict[str, SubagentResult]` and `_subagent_executors: dict[str, SubagentExecutor]`,
  plus `_scheduler_pool = ThreadPoolExecutor(max_workers=3)` and a persistent isolated event loop
  on a daemon thread. Accessor functions: `get_background_task_result`, `list_background_tasks`,
  `cleanup_background_task`, `request_cancel_background_task`, `get_subagent_executor`,
  `get_subagent_interrupt`, `resume_background_subagent`.
- **`SubagentConfig`** (`subagents/config.py`) - a 12-field dataclass
  (`name, description, system_prompt, tools, disallowed_tools, exclusive_tools, skills,
  skills_on_demand, model, max_turns, timeout_seconds, workflow`).
- **`SubagentStatus`** (`executor.py:52`) - `PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/
  TIMED_OUT/INTERRUPTED`. `is_terminal` = {COMPLETED, FAILED, CANCELLED, TIMED_OUT};
  `is_stopped` = terminal ∪ {INTERRUPTED}.
- **`SubagentResult`** (`executor.py:85`) - thread-safe (Lock) result container with
  `try_set_terminal` / `try_set_interrupted` / `try_resume`.
- **Checkpointer isolation** - each subagent uses `InMemorySaver()` (loop-safe, sync+async) keyed
  by `subagent_thread_id = f"subagent::{thread_id}::{task_id}"`. Never inherits the parent
  checkpointer.
- **INTERRUPTED/resume (Route 甲)** - `ClarificationMiddleware` calls `interrupt()`; the lead
  stays blocked in `task_tool`; the frontend POSTs
  `/api/threads/{thread_id}/subagents/{task_id}/resume`; `resume_background_subagent` revives.
  This path is preserved unchanged.
- **`SubagentLimitMiddleware`** (lead agent, #21) truncates excessive `task` tool_calls in
  `after_model` to `MAX_CONCURRENT_SUBAGENTS` (3).
- **status_contract** (`subagents/status_contract.py` + `contracts/subagent_status_contract.json`)
  - frontend/backend shared status values; `test_subagent_status_contract.py` enforces parity.

### OMP reference points adopted

| OMP concept | DeerFlow adaptation |
|-------------|---------------------|
| `EventBus` (pub/sub, lifecycle/progress channels) | `subagents/event_bus.py` - process-level pub/sub |
| `AgentRegistry` (AgentRef state store) | `subagents/agent_registry.py` - replaces `_background_tasks`/`_subagent_executors` dicts |
| `SubagentLifecycleManager` (idle TTL) | `subagents/lifecycle.py` - in-memory TTL (no `parked`/disk revival) |
| detached execution + `hub wait` | `task(detached=True)` + new `wait_for_tasks` tool |
| keep-alive `idle` + revival | `SubagentStatus.IDLE` + new `follow_up` tool |
| soft/hard request budget | `BudgetMonitor` + `SubagentConfig.max_requests` |
| `yield` protocol + `assembleYieldResult` | `subagents/yield_protocol.py` + `yield` tool |
| `output` JSON Schema on agent def | `SubagentConfig.output` field |

### OMP concepts deliberately NOT adopted

- `parked` state + `ensureLive()` disk revival (out of scope: in-memory only).
- `hub` tool / inter-subagent IRC messaging (out of scope: no multi-agent collaboration).
- `spawns` frontmatter / depth-limited recursion (out of scope: hard ban preserved).
- `prewalk` dual-model switching (out of scope per user).
- Two global singletons needing coordination - DeerFlow keeps Registry as the single state
  authority; EventBus is purely transient event broadcast and holds no state.

## 3. Architecture (Approach B - OMP-aligned decoupled infrastructure)

Formalize the existing implicit globals (`_background_tasks` + `_subagent_executors` + scattered
`get_stream_writer()` calls) into modules with single, clear responsibilities.

### 3.1 Module map

```
subagents/
├── event_bus.py          [NEW] EventBus: process-level pub/sub
├── agent_registry.py     [NEW] AgentRegistry: replaces _background_tasks/_subagent_executors
├── lifecycle.py          [NEW] SubagentLifecycleManager: IDLE TTL + cleanup
├── budget.py             [NEW] BudgetMonitor: soft/hard request budget
├── yield_protocol.py     [NEW] YieldCollector + assembleYieldResult + schema validation
├── config.py             [MOD] + keep_alive, max_requests, output fields
├── executor.py           [MOD] emit via EventBus, register with Registry, IDLE transition,
│                               budget hooks, yield integration in _extract_final_result
├── status_contract.py    [MOD] + "idle"/"revived"/"budget_warning"/"budget_exceeded" values
├── token_collector.py    [unchanged]
├── builder.py            [unchanged]
├── builtins/             [unchanged]
└── (middleware) YieldReminderMiddleware added to build_subagent_runtime_middlewares()

tools/builtins/
├── task_tool.py          [MOD] + detached param; thin dispatcher, events via EventBus
├── wait_for_tasks.py     [NEW] blocking collect for detached results
├── follow_up.py          [NEW] revive an IDLE subagent with a new prompt
└── yield_tool.py         [NEW] the yield tool (subagent-side)

agents/middlewares/
└── subagent_limit_middleware.py  [MOD] count detached+blocking against MAX_CONCURRENT_SUBAGENTS

contracts/
└── subagent_status_contract.json [MOD] + new status values
```

### 3.2 State machine extension

```
PENDING ─[worker starts]─> RUNNING
                            │
        ┌───────────────────┼────────────────────┐
        │                   │                    │
  [done + keep_alive]  [interrupt()]        [Exception / timeout / cancel]
        │                   │                    │
        ▼                   ▼                    ▼
      IDLE            INTERRUPTED         FAILED / TIMED_OUT / CANCELLED
        │                   │                    │
  [follow_up]          [resume]                 │
        │                   │                    │
        ▼                   ▼                    ▼
     RUNNING            RUNNING            (terminal -> cleanup)
        │                                        │
  [TTL expiry]                              COMPLETED (keep_alive=False)
        │                                        │
        ▼                                        ▼
   (terminal -> cleanup)                   (terminal -> cleanup)
```

- New `SubagentStatus.IDLE`. `is_terminal` is unchanged (IDLE is NOT terminal). `is_stopped`
  expands to include IDLE (the polling loop in `task_tool`/`wait_for_tasks`/`follow_up` should
  stop waiting when IDLE, since IDLE means "done, awaiting follow-up").
- `SubagentResult.try_set_idle()` - writes IDLE only when the current status is not already
  `is_stopped` (mirrors `try_set_interrupted`'s guard). Records `idle_since`.
- `cleanup_background_task` changes: terminal statuses delete as today; IDLE is NOT deleted by
  `cleanup_background_task` - it is owned by `SubagentLifecycleManager` which drives
  IDLE -> terminal cleanup on TTL expiry or explicit dismiss.

### 3.3 AgentRegistry

Replaces the two module-level dicts with a single state authority. Internal storage still uses a
`threading.Lock` (equivalent to today's `_background_tasks_lock`).

```python
@dataclass
class AgentRef:
    task_id: str
    thread_id: str            # parent thread - SSE routing key
    trace_id: str
    subagent_type: str
    status: SubagentStatus
    config: SubagentConfig
    executor: SubagentExecutor | None   # retained for IDLE/INTERRUPTED revival
    result: SubagentResult | None
    description: str
    created_at: datetime
    idle_since: datetime | None

class AgentRegistry:
    """Process-level singleton. Replaces _background_tasks + _subagent_executors."""
    def register(self, ref: AgentRef) -> None
    def get(self, task_id: str) -> AgentRef | None
    def list_by_thread(self, thread_id: str) -> list[AgentRef]
    def list_idle(self, thread_id: str | None = None) -> list[AgentRef]
    def update_status(self, task_id: str, status: SubagentStatus, **fields) -> None
    def remove(self, task_id: str) -> None
    def on_status_change(self, handler: Callable[[AgentRef], None]) -> Callable  # unsubscribe
```

**Backward-compat shim**: the existing module-level functions
(`get_background_task_result`, `list_background_tasks`, `cleanup_background_task`,
`get_subagent_executor`, `get_subagent_interrupt`, `resume_background_subagent`) become thin
wrappers over the Registry singleton, preserving their signatures so `task_tool.py` and the
Gateway resume endpoint compile unchanged.

### 3.4 EventBus + SSE bridge

```python
class EventBus:
    """Process-level pub/sub. Holds no state - pure event broadcast."""
    def emit(self, channel: str, payload: dict) -> None
    def on(self, channel: str, handler: Callable[[dict], None]) -> Callable  # returns unsubscribe
```

Channels:
- `subagent:lifecycle` - `{event: started|running|idle|revived|completed|failed|cancelled|timed_out, task_id, thread_id, ...}`
- `subagent:progress` - 150ms-throttled `{task_id, thread_id, status, current_tool, tokens, model}`
- `subagent:budget` - `{event: warning|exceeded, task_id, thread_id, requests, limit}`

**SSE bridge** (the key integration point): `get_stream_writer()` is per-lead-run; `EventBus` is
process-global. The bridge maintains `_writers: dict[thread_id, StreamWriter]`:

- A lead run registers its writer when `task(detached=False)` / `wait_for_tasks` / `follow_up`
  is invoked (these are the points where the lead is actively waiting and its writer is valid).
- On event, the bridge routes by `payload["thread_id"]` to the registered writer; maps the
  EventBus payload to the existing SSE event schema (`{"type": "task_completed", ...}`) plus new
  types (`task_idle`, `task_revived`, `budget_warning`, `budget_exceeded`).
- If no writer is registered for a `thread_id` (lead between turns, or detached subagent
  completing while lead is doing other work), the event is dropped from SSE but the state change
  is already persisted in the Registry - `wait_for_tasks` reads final state from Registry on
  collect, so no result is lost.
- The writer is unregistered when the collecting tool returns.

This decoupling means a future logger/metrics subscriber can be added with one
`EventBus.on(...)` call and zero executor changes - the core observability win.

### 3.5 `task` tool new signature

```python
@tool("task")
async def task_tool(
    description: str,
    prompt: str,
    subagent_type: str,
    detached: bool = False,      # NEW: True -> return task_id immediately
    tool_call_id, runtime,
) -> str:
    # detached=False: existing blocking-poll behavior (backward compatible)
    # detached=True:  dispatch -> Registry.register + EventBus.emit(started) -> return
    #                 "Task spawned. task_id=<id>. Call wait_for_tasks([<id>]) to collect."
```

### 3.6 SubagentConfig extensions

```python
keep_alive: bool = False        # COMPLETED -> IDLE instead of cleanup
max_requests: int | None = None # soft budget; None = unlimited (max_turns still applies)
output: dict | None = None      # JSON Schema constraining terminal yield payload
```

All have defaults -> existing YAML configs need no changes.

## 4. Data flow

### 4.1 Flow A - Detached parallel investigation (single lead turn)

```
Lead run (thread=T, writer=W_T)
  │
  ├─ model: task(scout,"搜A",detached=True)
  │    task_tool:
  │      executor.execute_async(prompt, task_id=t1)
  │      Registry.register(AgentRef{t1, thread=T, RUNNING, ...})
  │      EventBus.emit("subagent:lifecycle", {started, t1, T})
  │      return "Task spawned. task_id=t1. Call wait_for_tasks([t1]) to collect."
  │
  ├─ model: task(scout,"搜B",detached=True) -> t2 (same as t1)
  │
  ├─ model: read_file("other.txt")   # lead does other work in parallel
  │
  └─ model: wait_for_tasks(["t1","t2"])
       SSE bridge.register_writer(T, W_T)   # register current writer
       subscribe to EventBus "subagent:lifecycle"
       poll Registry.get(t1/t2) as fallback
       │
       │   [background] t1 subagent completes:
       │     executor -> Registry.update_status(t1, COMPLETED, result=...)
       │             -> EventBus.emit("subagent:lifecycle", {completed, t1, T, result})
       │             -> SSE bridge routes by T -> W_T({"type":"task_completed",...})
       │   [background] t2 subagent completes: same
       │
       └─ both terminal -> return {t1: result, t2: result}
          -> ToolMessage injected -> model continues
```

Key point: during detached execution the lead run stays active (writer W_T valid), so SSE
streams back in real time. `wait_for_tasks` is both the collection point and the writer
registration point. If a detached subagent completes *before* `wait_for_tasks` is called, the
result is already in the Registry - `wait_for_tasks` reads it on collect (no loss).

### 4.2 Flow B - IDLE -> follow_up cross-turn revival

```
Turn 1 (writer W_T1):
  model: task(scout,"分析架构")   # detached=False (default blocking), config.keep_alive=True
    executor blocks polling... subagent completes:
      result.try_set_idle()                      # COMPLETED -> IDLE (no cleanup)
      Registry.update_status(t1, IDLE, idle_since=now)
      EventBus.emit("subagent:lifecycle", {idle, t1, T})
      LifecycleManager.adopt(t1, ttl=7min)       # start TTL timer
    return "Task Succeeded. Result: ..."          # lead gets result, continues
  [turn 1 ends, W_T1 unregistered]

  ... if no follow_up within 7 min:
    LifecycleManager: TTL expires -> Registry.update_status(t1, COMPLETED)
                      -> cleanup_background_task(t1)   # actually deletes

Turn 2 (writer W_T2, same thread T):
  model: follow_up("t1", "基于刚才分析，深入看X")
    follow_up:
      ref = Registry.get(t1)
      assert ref.status == IDLE            # else error "subagent not idle or expired"
      LifecycleManager.cancel_ttl(t1)      # cancel pending cleanup
      executor = ref.executor              # reuse same executor + checkpointer + subagent_thread_id
      executor.continue_with_prompt(prompt)  # IDLE -> RUNNING, fresh astream w/ new HumanMessage on same checkpointer
      EventBus.emit("subagent:lifecycle", {revived, t1, T})
      block-poll until completion:
        -> COMPLETED -> if keep_alive: try_set_idle + LifecycleManager.adopt again
                        else: cleanup
      return result
```

Key point: cross-turn reuse of the same `subagent_thread_id` + `InMemorySaver` checkpointer
preserves full conversation context. Gateway restart loses all IDLE subagents (matches the
"in-memory only" constraint). `follow_up` does NOT use `Command(resume=...)` - that is for
resuming from an `interrupt()` node (INTERRUPTED state). An IDLE subagent has COMPLETED: its
graph run finished. To continue, `follow_up` invokes a fresh `agent.astream()` with a new
`HumanMessage(prompt)`, reusing the same compiled agent + checkpointer + `subagent_thread_id`;
the checkpointer loads the prior conversation history, giving standard LangGraph multi-turn
continuation. The INTERRUPTED/resume path (Route 甲) remains untouched.

### 4.3 Flow C - Budget control

```
executor._aexecute streaming loop:
  after each LLM response:
    monitor = BudgetMonitor(soft=ref.config.max_requests)
    monitor.tick()
    if monitor.at_soft_limit() and not already_warned:
      EventBus.emit("subagent:budget", {warning, t1, T, requests, soft})
      # next loop iteration appends a wrap-up SystemMessage to state["messages"]
    if monitor.at_hard_limit():   # 1.5 * soft
      EventBus.emit("subagent:budget", {exceeded, t1, T, requests, hard})
      result.try_set_terminal(FAILED, error="request budget exceeded (N > hard)")
      return   # break the astream loop
```

`max_turns` and `max_requests` coexist: `max_turns` limits LangGraph super-steps (including
tool-call rounds); `max_requests` limits LLM request count (finer cost control). Whichever
triggers first wins. `max_requests=None` disables the budget path entirely (today's behavior).

### 4.4 Flow D - SSE event routing

```
EventBus (process-level)
   │
  ├─ SSE bridge (subscribes to all 3 channels)
  │     maintains _writers: dict[thread_id, StreamWriter]
  │     on event(payload):
  │       w = _writers.get(payload["thread_id"])
  │       if w: w({"type": _map_event_type(payload), **payload})
  │       else: drop from SSE (state already in Registry - no loss)
  │
  ├─ logger/metrics (future subscriber, zero-intrusion)
  └─ RPC bridge (future)

writer lifecycle:
  task(detached=False) / wait_for_tasks / follow_up invoked -> register_writer(thread_id, writer)
  tool returns -> unregister_writer(thread_id)
```

## 5. Yield protocol

A subagent submits structured output via the `yield` tool, replacing the implicit
"extract from last AIMessage" path. Backward compatible: subagents that never call `yield`
fall back to the existing `_extract_final_result` AIMessage extraction.

### 5.1 The `yield` tool

```python
@tool("yield")
def yield_tool(
    data: Any,                           # payload: any JSON-serializable
    type: str | list[str] | None = None,  # None/str = terminal; list = incremental section
    runtime: Runtime,
) -> str:
    collector = _yield_collector_ctx.get()   # ContextVar, injected by executor
    collector.record(data, type)
    if type is None or isinstance(type, str):
        return "Result submitted. You may stop."
    return f"Section recorded. Continue or submit final."
```

The tool name `yield` is exposed to the LLM as a string; the Python function is named
`yield_tool` (the `yield` keyword only conflicts as a bare statement, not as an identifier
suffix).

### 5.2 Three yield shapes

| `type` | Semantics | Use case |
|--------|-----------|----------|
| omitted / `str` | Terminal result | `data` is the final output; overrides prior incremental |
| `["findings"]` | Incremental section | Same-named sections accumulate into an array (reviewer yields one finding per discovery) |

### 5.3 Assembly: `assembleYieldResult(yields, output_schema)`

Pure function folding the yield sequence into the final payload:

1. Group by `type`: `str`/None -> terminal; `list` -> incremental sections.
2. Incremental sections with the same name accumulate into arrays.
3. Terminal payload has highest priority (overrides incremental).
4. If `output_schema` is present, validate the terminal payload:
   - pass -> `{data, schemaValid: True}`
   - fail -> keep payload but mark `{data, schemaValid: False, schemaErrors: [...]}`
     (graceful degradation - never drops the payload).
5. No terminal yield -> fall back to the last assistant text.

### 5.4 SubagentConfig / SubagentResult extensions

```python
# config.py
output: dict | None = None    # JSON Schema constraining terminal yield payload

# executor.py SubagentResult (lock-protected)
yields: list[YieldEntry] = field(default_factory=list)
yield_assembled: dict | None = None
```

### 5.5 Executor integration

```python
# before _aexecute
collector = YieldCollector(output_schema=config.output)
_yield_collector_ctx.set(collector)   # ContextVar, visible inside copy_context()

# _extract_final_result becomes:
if collector.has_terminal():
    assembled = assembleYieldResult(collector.yields, config.output)
    result.yield_assembled = assembled
    return assembled["data"]   # or JSON string
# fallback: existing AIMessage extraction (unchanged for non-yielding subagents)
```

The `yield` tool writes to the collector as a side effect when invoked - the streaming loop
does not need to parse tool_calls to detect yields.

### 5.6 YieldReminderMiddleware (lightweight)

Added to `build_subagent_runtime_middlewares()`, enabled only when `config.output` is set:

- `after_model`: check whether the last AIMessage contains a `yield` tool_call.
- If not -> inject a SystemMessage reminder: "When the task is nearly complete, call `yield`
  to submit your result."
- At most 3 reminders; the 3rd attaches a `tool_choice` forcing `yield`.

### 5.7 Backward compatibility

- The `yield` tool is added to all subagent tool sets, but **not calling it falls back to
  AIMessage extraction** -> existing subagent behavior is unchanged.
- `output` defaults to None -> no schema validation.
- The existing `_extract_final_result` logic is preserved as the fallback path.

## 6. Backward compatibility

| Dimension | Compatibility | Notes |
|-----------|---------------|-------|
| `task` tool signature | compatible | `detached` defaults False; existing call behavior unchanged (blocking poll) |
| `_background_tasks`/`_subagent_executors` module accessors | compatible | preserved as Registry singleton thin wrappers; `get_background_task_result` etc. signatures unchanged |
| SubagentConfig YAML | compatible | new fields `keep_alive`/`max_requests`/`output` have defaults; existing configs need no changes |
| SubagentStatus enum | compatible | adds IDLE; `is_terminal` unchanged (IDLE not terminal); `is_stopped` expands to include IDLE. Existing `is_terminal`-dependent logic unaffected |
| INTERRUPTED/resume path (Route 甲) | unchanged | resume endpoint, `task_interrupted` SSE, frontend form all preserved |
| `_extract_final_result` | compatible | yield path added as preferred; AIMessage extraction retained as fallback |
| `yield` tool presence | compatible | added to all subagents but non-invocation falls back to AIMessage extraction |
| status_contract.json | frontend change | adds `idle`/`revived`/`budget_warning`/`budget_exceeded` values; frontend must recognize new SSE event types |
| SubagentLimitMiddleware | needs extension | currently counts only blocking `task` calls; must count detached too (shared `_scheduler_pool` 3 workers). Detached does not block the lead but occupies a worker |
| Frontend UI | frontend change | rendering for `wait_for_tasks`/`follow_up` tools; IDLE subagent list/status display; budget warning UI |
| Test fixtures | test change | `contracts/subagent_status_contract.json` extended; `test_subagent_status_contract.py` updated |

**Non-breaking principle**: all backend API endpoints (`/api/threads/{id}/subagents/{task_id}/resume`
etc.) keep their signatures; new capabilities are opt-in. The only hard requirement is frontend
recognition of new SSE event types and the two new tools' UI - a coordinated frontend change.

## 7. Testing

Follows backend TDD requirements. Each new module/behavior gets a test file. Existing tests must
not regress.

| Test file | Contract covered |
|-----------|------------------|
| `test_agent_registry.py` | register/get/list_by_thread/list_idle/update_status/remove; concurrent register+update under 100 threads (no deadlock, no lost state); status-change event firing |
| `test_event_bus.py` | emit/on/unsubscribe; multiple independent subscribers; channel isolation; handler exception does not poison other subscribers |
| `test_subagent_lifecycle.py` | IDLE TTL expiry -> cleanup; `follow_up` resets TTL; explicit dismiss -> cleanup; TTL-vs-cancel race (`try_set_terminal` wins) |
| `test_budget_monitor.py` | soft budget triggers wrap-up injection; 1.5x hard stop; `max_requests=None` unlimited; coexistence with `max_turns` (first-trigger-wins) |
| `test_subagent_detached.py` | `detached=True` returns task_id immediately; `wait_for_tasks` collects single/multiple; mixed complete/timeout; empty-list semantics |
| `test_subagent_follow_up.py` | IDLE -> follow_up -> RUNNING -> COMPLETED; cross-turn context preserved (same `subagent_thread_id`); follow_up on non-IDLE errors; follow_up after TTL expiry errors "expired" |
| `test_subagent_idle_state.py` | SubagentStatus.IDLE semantics; `try_set_idle` only when not `is_stopped`; `is_stopped` includes IDLE; `is_terminal` excludes IDLE |
| `test_sse_bridge.py` | thread_id routing correct; writer register/unregister; no-writer event dropped from SSE but state persisted in Registry; concurrent lead runs (different thread_ids) |
| `test_yield_protocol.py` | incremental accumulation; terminal override; schema validation pass/fail degradation; no-yield fallback to AIMessage; multiple same-name sections merge |
| `test_yield_tool.py` | tool writes to collector; terminal/incremental return-message distinction |
| `test_yield_reminder_middleware.py` | no-yield -> reminder injected; 3rd reminder forces `tool_choice`; yield present -> no reminder |
| `test_subagent_status_contract.py` (extended) | new values `idle`/`revived`/`budget_warning`/`budget_exceeded` parsed identically front/back |
| `test_subagent_limit_middleware.py` (extended) | detached+blocking total concurrency <= MAX_CONCURRENT_SUBAGENTS; truncation logic |

**Boundaries & invariants**:
- Registry concurrency: 100 threads concurrent register+update, no deadlock, no lost state.
- IDLE TTL: second-level precision on cleanup trigger; `follow_up` arriving at the instant of
  TTL expiry (`cancel_ttl` must precede `cleanup`).
- Budget: `max_requests=0` semantics = immediate termination (not unlimited).
- Detached + cancel: lead cancellation propagates cooperative cancel to detached subagent;
  token usage reported upward.

## 8. Phase split

All designed features are in scope for this implementation cycle (the user elevated Yield to
Phase 1). "Phase 2" below denotes explicitly deferred future work, not part of this spec.

### In scope (this spec)

- EventBus + AgentRegistry + SubagentLifecycleManager + BudgetMonitor modules.
- `task(detached=True)` + `wait_for_tasks` + `follow_up` tools.
- `SubagentStatus.IDLE` + in-memory TTL cleanup (no disk revival).
- `SubagentConfig` extensions: `keep_alive`, `max_requests`, `output`.
- Yield protocol: `yield` tool + `YieldCollector` + `assembleYieldResult` + schema validation
  + `YieldReminderMiddleware`.
- SSE bridge routing EventBus events to per-run writers by `thread_id`.
- Frontend: new SSE event types + `wait_for_tasks`/`follow_up` tool rendering + budget warning UI.
- Contract fixture + test extensions.

### Deferred (future specs, explicitly out of scope)

- **Disk persistence / revival**: `parked` state + `ensureLive()` from JSONL/session files.
  Requires a persistent loop-safe checkpointer and a session reviver.
- **Inter-subagent messaging**: `hub` tool + peer roster + IRC-style coordination.
- **Depth-limited recursive spawning**: `spawns` frontmatter + `task.maxRecursionDepth`.
- **Prewalk**: dual-model plan/execute switching.
- **`output` schema designer UI**: a full frontend editor for JSON Schema.

## 9. Open questions (to resolve during implementation planning)

1. **`wait_for_tasks` timeout**: should it have its own timeout, or inherit the longest
   `timeout_seconds` among the awaited subagents? Lean: inherit max + 60s buffer (mirrors
   today's polling-timeout formula).
2. **IDLE TTL default**: 7 minutes (OMP default) vs. DeerFlow's existing patterns. Lean: 7 min,
   configurable via `subagents.idle_ttl_seconds` global.
3. **`follow_up` on a subagent whose TTL already expired mid-call**: return a clear error and
   suggest re-spawning via `task`. No silent re-spawn.
4. **Budget wrap-up injection point**: append to `state["messages"]` as a HumanMessage vs. a
   SystemMessage. Lean: SystemMessage (does not pollute the conversation as a user turn).
5. **Multiple concurrent `follow_up` on the same IDLE subagent**: reject the second with
   "subagent already revived/running". No queueing.
