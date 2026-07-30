# Unified Subagent Event Bus Design

## Status
**Draft** - 2026-07-30

## Problem

DeerFlow's subagent system has two execution modes (blocking and detached) that
follow fundamentally different message-passing paths, resulting in:

1. **Detached subagents lose all messages**: The executor emits progress events
   to the EventBus, but no SSE writer is registered (task_tool only registers
   for blocking mode). SSEBridge drops all events. AI messages accumulate in
   `result.ai_messages` but never reach the frontend until `wait_for_tasks`
   returns the final result.

2. **Detached subagent interrupts are unresumable**: When a detached subagent
   hits a structured interrupt, the executor sets INTERRUPTED but emits no
   lifecycle event. `wait_for_tasks` sees `is_stopped == True` and returns
   immediately with `{status: interrupted, result: null}`. The lead cannot
   resume (follow_up only works on IDLE; resume is an external HTTP endpoint
   whose result is not fed back to the lead's current turn).

3. **Event emission is split across two layers**: The executor emits
   `subagent:progress` (status only, no message content) and some lifecycle
   events (completed/idle/failed). But `task_interrupted`, `task_running`,
   `task_timed_out`, and progress-with-content are emitted only by task_tool's
   blocking poll loop. `_aresume` (resume path) and `_acontinue` (follow_up
   path) emit almost no events at all.

4. **Triple storage with incomplete migration**: `agent_registry`,
   `_background_tasks`, and `_subagent_executors` all store subagent state
   with fallback paths. The docstring says agent_registry "replaces" the other
   two, but they still exist.

5. **Redundant resume SSE stream**: `_subagent_resume_event_stream`
   (thread_runs.py:985-1055) is a separate SSE stream with its own poll loop,
   bypassing the EventBus entirely. Under the current blocking-mode interrupt
   flow (Route 甲), it is redundant - the frontend watches the lead run stream.

## Design Goal

Unify to a **single event-passing path** where the executor is the sole event
emitter, `task()` always returns immediately (no blocking mode), and
`wait_for_tasks` is the only polling mechanism.

## End-State Architecture

```
Lead calls task(description, prompt, subagent_type)
  -> task_tool:
     1. Register SSE writer (get_stream_writer, keyed by thread_id)
     2. executor.execute_async() - starts background task
     3. emit task_started (lifecycle)
     4. Return task_id immediately to lead

Executor _aexecute (background):
  astream loop:
    Each new AI message -> emit subagent:progress {task_id, thread_id, status, message, message_index, total_messages}
    Interrupt detected   -> emit subagent:lifecycle {event: interrupted, task_id, thread_id, interrupts}
    Cancel               -> emit subagent:lifecycle {event: cancelled, task_id, thread_id}
    Budget exceeded      -> emit subagent:lifecycle {event: failed, task_id, thread_id, error}
    Completion           -> emit subagent:lifecycle {event: completed|idle, task_id, thread_id, result?}
    Failure              -> emit subagent:lifecycle {event: failed, task_id, thread_id, error}

EventBus -> SSEBridge -> writer (by thread_id) -> lead run stream -> frontend

Lead calls wait_for_tasks([task_id]):
  -> Register SSE writer (if not already registered)
  -> Poll registry:
     RUNNING     -> continue polling (events flow in real-time via SSEBridge)
     INTERRUPTED -> suspend timeout, continue polling (resume events will flow)
     COMPLETED/FAILED/CANCELLED -> return {status, result, error}
  -> Unregister writer on exit

User answers interrupt -> POST /resume (fire-and-forget, returns 202):
  -> resume_background_subagent -> executor._aresume
  -> _aresume emits events (progress + lifecycle) -> EventBus -> SSEBridge
     -> writer (registered by wait_for_tasks) -> lead stream -> frontend
  -> wait_for_tasks poll detects COMPLETED -> returns result to lead
```

### Key Changes Summary

| Aspect | Current | End-State |
|--------|---------|-----------|
| Event emitter | task_tool poll loop (blocking) / nothing (detached) | **Executor sole emitter** |
| Progress content | `{task_id, thread_id, status}` | `{task_id, thread_id, status, message, message_index, total_messages}` |
| Interrupt event | Only blocking mode poll loop emits | Executor `_aexecute`/`_aresume`/`_acontinue` emit directly |
| Resume events | `_aresume` emits nothing | `_aresume` emits progress + lifecycle |
| Writer registration | Only blocking mode at task() time | **task() always registers** |
| Blocking mode | Exists (~250-line poll loop) | **Eliminated**; unified to task() + wait_for_tasks() |
| wait_for_tasks on INTERRUPTED | Returns immediately | **Keeps polling**, suspends timeout |
| Storage | Triple (agent_registry + _background_tasks + _subagent_executors) | **Unified to agent_registry** |
| POST /resume response | Returns SSE stream | Returns 202 Accepted (fire-and-forget) |
| _subagent_resume_event_stream | Exists (separate poll loop, bypasses EventBus) | **Deleted** |

---

## Detailed Design

### 1. Executor Becomes Sole Event Emitter

File: `subagents/executor.py`

Three async execution methods need event emission changes:

#### 1.1 `_aexecute` (executor.py:1022-1210)

**Progress events** - enrich with message content (line ~1101-1108):

Current:
```python
event_bus.emit("subagent:progress", {
    "task_id": result.task_id or "",
    "thread_id": self.thread_id or "",
    "status": result.status.value,
})
```

New:
```python
event_bus.emit("subagent:progress", {
    "task_id": result.task_id or "",
    "thread_id": self.thread_id or "",
    "status": result.status.value,
    "message": message_dict.get("content", ""),
    "message_index": len(ai_messages),
    "total_messages": len(ai_messages),
})
```

**Lifecycle events** - add missing emissions:

| Location | Line | Current | New |
|----------|------|---------|-----|
| Interrupt | ~1125-1132 | `try_set_interrupted` + return, no event | Add `emit("subagent:lifecycle", {event: "interrupted", task_id, thread_id, interrupts: serialize_lc_object(interrupts)})` before return |
| Cancel | ~1113-1123 | `try_set_terminal(CANCELLED)` + return, no event | Add `emit("subagent:lifecycle", {event: "cancelled", task_id, thread_id})` before return |
| Budget exceeded | ~1076-1096 | `try_set_terminal(FAILED)` + break, only progress | Add `emit("subagent:lifecycle", {event: "failed", task_id, thread_id, error: "request budget exceeded"})` before break |

Already correct (no change needed):
- Completion without keep_alive (~1175-1189): emits `completed` lifecycle event
- Completion with keep_alive (~1150-1171): emits `idle` lifecycle event
- Failure (~1190-1208): emits `failed` lifecycle event

#### 1.2 `_aresume` (executor.py:1410-1551)

**All paths emit zero events.** Add emission for every transition:

**Start of _aresume** (after the try block opens, before astream loop):
```python
event_bus.emit("subagent:lifecycle", {
    "event": "running",
    "task_id": result.task_id or "",
    "thread_id": self.thread_id or "",
})
```

**Progress during astream** (line ~1513-1516, after appending to ai_messages):
```python
event_bus.emit("subagent:progress", {
    "task_id": result.task_id or "",
    "thread_id": self.thread_id or "",
    "status": result.status.value,
    "message": message_dict.get("content", ""),
    "message_index": len(ai_messages),
    "total_messages": len(ai_messages),
})
```

Note: `_aresume` inherits `ai_messages` from `result.ai_messages` (line 1419),
so `message_index` continues incrementing from where `_aexecute` left off.

**Lifecycle events**:

| Location | Line | New |
|----------|------|-----|
| Re-interrupt | ~1525-1532 | `emit("subagent:lifecycle", {event: "interrupted", task_id, thread_id, interrupts})` |
| Cancel (in-loop) | ~1489-1495 | `emit("subagent:lifecycle", {event: "cancelled", task_id, thread_id})` |
| Cancel (post-loop) | ~1518-1524 | `emit("subagent:lifecycle", {event: "cancelled", task_id, thread_id})` |
| Completion | ~1534-1541 | `emit("subagent:lifecycle", {event: "completed", task_id, thread_id, result})` (or `idle` if keep_alive) |
| Failure | ~1543-1549 | `emit("subagent:lifecycle", {event: "failed", task_id, thread_id, error})` |

#### 1.3 `_acontinue` (executor.py:1685-1910) - follow_up continuation

Already emits `revived` at start (line 1708-1715) and `completed`/`idle` at
completion. Same gaps as `_aresume` for the other paths:

**Progress during astream** (line ~1819-1822, after appending to ai_messages):
Add the same progress emit as above. Note: `_acontinue` uses a fresh
`ai_messages = []` (line 1699), so `message_index` starts from 1. This is
correct - follow_up is a new prompt.

**Missing lifecycle events**:

| Location | Line | New |
|----------|------|-----|
| Budget exceeded | ~1824-1832 | `emit("subagent:lifecycle", {event: "failed", task_id, thread_id, error: "request budget exceeded"})` |
| Cancel (in-loop) | ~1791-1800 | `emit("subagent:lifecycle", {event: "cancelled", task_id, thread_id})` |
| Cancel (post-loop) | ~1834-1843 | `emit("subagent:lifecycle", {event: "cancelled", task_id, thread_id})` |
| Interrupt | ~1845-1852 | `emit("subagent:lifecycle", {event: "interrupted", task_id, thread_id, interrupts})` |
| Failure | ~1902-1908 | `emit("subagent:lifecycle", {event: "failed", task_id, thread_id, error})` |

### 2. task_tool: Eliminate Blocking Mode

File: `tools/builtins/task_tool.py`

#### 2.1 Remove `detached` parameter

The `task()` function signature changes from:
```python
async def task(description, prompt, subagent_type="general-purpose", detached=False) -> str
```
to:
```python
async def task(description, prompt, subagent_type="general-purpose") -> str
```

#### 2.2 Remove poll loop (lines 423-648)

Delete the entire blocking poll loop (~250 lines), including:
- 5s polling loop
- Route 甲 interrupt detection and `task_interrupted` SSE emission (588-613)
- Timeout counting and `task_timed_out` emission
- `task_running` / `task_completed` / `task_failed` emission
- Progress-with-content emission (488-499) - responsibility moves to executor

#### 2.3 Unified single path

The `task()` function becomes:

```python
async def task(description, prompt, subagent_type="general-purpose") -> str:
    # 1. Resolve subagent config
    # 2. Create executor
    # 3. Register SSE writer (get_stream_writer, keyed by thread_id)
    # 4. executor.execute_async() - starts background task
    # 5. emit task_started lifecycle event
    # 6. Return task_id immediately
    return f"Started subagent task {task_id}. Use wait_for_tasks to get the result."
```

The writer registration is the critical change: it was previously only done in
the blocking path (line 443). Now it is always done, ensuring detached
subagent events reach the frontend from the moment they are spawned.

### 3. wait_for_tasks: Poll Through INTERRUPTED

File: `tools/builtins/wait_for_tasks.py`

#### 3.1 Don't return immediately for INTERRUPTED

Current behavior (line 66): `is_stopped` predicate returns True for both
INTERRUPTED and IDLE, causing immediate return.

New behavior:
- **INTERRUPTED**: do NOT return. Keep polling. Suspend timeout countdown
  (only RUNNING polls advance the timeout counter, mirroring the original
  task_tool.py:622 behavior).
- **IDLE**: return immediately (same as current - IDLE means the subagent
  completed its turn and is parked for optional follow_up).
- **Terminal** (COMPLETED/FAILED/CANCELLED/TIMED_OUT): return immediately.

Change line 66 from `if status.is_stopped:` to
`if status.is_terminal() or status == SubagentStatus.IDLE:`.

#### 3.2 Remove duplicate lifecycle event emission

Current `wait_for_tasks` (lines 75-84) emits a `subagent:lifecycle` event
when it detects a stopped status. In the new architecture, the executor
already emits these events. **Delete lines 75-84** to avoid duplicate
events reaching the frontend.

The executor is the sole lifecycle event emitter. `wait_for_tasks` only
polls and returns results as a tool return value to the lead model.

#### 3.3 Timeout suspension logic

```python
elapsed = 0
while elapsed < timeout:
    all_done = True
    for task_id in task_ids:
        ref = agent_registry.get(task_id)
        if ref is None:
            results[task_id] = {"status": "not_found", "result": None}
            continue
        if ref.status.is_terminal() or ref.status == SubagentStatus.IDLE:
            results[task_id] = {"status": ref.status.value, "result": ref.result.result, "error": ref.result.error}
        elif ref.status == SubagentStatus.INTERRUPTED:
            all_done = False  # keep polling, don't advance timeout
        else:  # RUNNING
            all_done = False
    if all_done:
        break
    await asyncio.sleep(poll_interval)
    # Only advance timeout if at least one task is RUNNING (not all INTERRUPTED)
    if any(agent_registry.get(tid) and agent_registry.get(tid).status == SubagentStatus.RUNNING for tid in task_ids):
        elapsed += poll_interval
```

#### 3.4 Cleanup responsibility

In the current architecture, `cleanup_background_task` is called from
task_tool's poll loop (lines 470, 519, 554, 570, 586, 667) after a task
reaches terminal state. With the poll loop deleted, this responsibility
moves to `wait_for_tasks`:

After returning results for terminal tasks, `wait_for_tasks` calls
`cleanup_background_task(task_id)` for each. `cleanup_background_task`
already checks `result.status.is_terminal` before removing, so IDLE and
INTERRUPTED tasks are safely left resident.

The `_deferred_cleanup_subagent_task` helper (task_tool.py:111-124) and
`_schedule_deferred_subagent_cleanup` (task_tool.py:137-140) can be moved
into `wait_for_tasks` or kept in task_tool as utilities - they use
`get_background_task_result` and `cleanup_background_task` wrappers which
remain available.

### 4. Storage Consolidation

Files: `subagents/executor.py`, `subagents/agent_registry.py`

#### 4.1 Strategy: keep wrapper functions, delete underlying dicts

The module-level dicts `_background_tasks` (executor.py:235),
`_background_tasks_lock` (executor.py:236), and `_subagent_executors`
(executor.py:242) are deleted. Their read/write sites are migrated to
`agent_registry`.

However, the **public wrapper functions** that wrap these dicts are **kept
as thin wrappers** around `agent_registry` (rewritten implementations).
This avoids a large blast radius across callers:

| Wrapper function | Current impl | New impl |
|-----------------|-------------|----------|
| `get_background_task_result(task_id)` (executor.py:1996) | `_background_tasks.get(task_id)` with registry fallback | `agent_registry.get(task_id).result` or `None` |
| `list_background_tasks()` (executor.py:2014) | Iterates `_background_tasks` + registry merge | Iterates `agent_registry.list_all()` |
| `get_subagent_executor(task_id)` (executor.py:2079) | `_subagent_executors.get(task_id)` | `agent_registry.get(task_id).executor` or `None` |
| `cleanup_background_task(task_id)` (executor.py:2034) | Registry lookup + `_background_tasks`/`_subagent_executors` pop with lock | `agent_registry.get(task_id)` -> check terminal -> `agent_registry.remove(task_id)` |

Callers of these wrappers (task_tool, lifecycle.py, thread_runs.py, tests)
do NOT need to change - only the implementations change.

#### 4.2 Update `execute_async` (executor.py:~1292-1400)

Remove writes to `_background_tasks` and `_subagent_executors`:
- Line 1315-1319: `_background_tasks[task_id] = result` + `_subagent_executors[task_id] = self` -> already registered in `agent_registry` by `execute_async`'s caller; verify and remove redundant writes
- Line 1345-1348: `_background_tasks[task_id].status = RUNNING` -> `agent_registry.update_status(task_id, RUNNING)`
- Line 1394-1395: `task_result = _background_tasks[task_id]` -> `agent_registry.get(task_id).result`

#### 4.3 Update `resume_async` (executor.py:~1912-1970)

Remove writes to `_background_tasks`:
- Line 1928-1929: `_background_tasks.get(task_id)` -> `agent_registry.get(task_id).result`
- Line 1941-1947: `_background_tasks[task_id].status = RUNNING` + submit `_aresume` -> `agent_registry.update_status(task_id, RUNNING)` + submit
- Line 1953-1964: result holder read + error handling -> `agent_registry.get(task_id).result`

#### 4.4 Simplify `resume_background_subagent` (executor.py:2133-2146)

Remove the fallback path (lines 2138-2141):
```python
# DELETE this fallback:
if executor is None or result is None:
    with _background_tasks_lock:
        executor = _subagent_executors.get(task_id)
        result = _background_tasks.get(task_id)
```

Becomes:
```python
ref = agent_registry.get(task_id)
if ref is None or ref.executor is None or ref.result is None:
    raise KeyError(f"Unknown subagent task {task_id}")
if ref.result.status is not SubagentStatus.INTERRUPTED:
    raise RuntimeError(...)
return ref.executor.resume_async(resume_value, task_id)
```

#### 4.5 Simplify `cleanup_background_task` (executor.py:2034-2076)

Remove `_background_tasks_lock` usage and dict pops. Becomes:
```python
def cleanup_background_task(task_id: str) -> None:
    ref = agent_registry.get(task_id)
    if ref is None or ref.result is None:
        return
    if ref.result.status.is_terminal:
        agent_registry.remove(task_id)
    # else: non-terminal, keep resident
```

#### 4.6 Test updates

Tests that directly manipulate `_background_tasks` / `_subagent_executors`
must be updated to use `agent_registry` instead:
- `tests/test_subagent_status_semantics.py` (lines 163-176)
- `tests/test_subagent_interrupt_and_resume.py` (lines 248-282)
- `tests/test_subagent_executor.py` (many lines: 1751, 1771, 1790, 1812, 1830, 1859, 1973, 2026, 2073, 2135-2138, 2144, 2199, 2208, 2228)

Pattern: replace `executor_module._background_tasks[task_id] = result` with
`agent_registry.register(task_id, AgentRef(...))` or equivalent.

### 5. Delete `_subagent_resume_event_stream`

File: `app/gateway/routers/thread_runs.py`

#### 5.1 Remove the SSE stream function (lines 985-1055)

Delete `_subagent_resume_event_stream` entirely. It has its own poll loop that
diffs `result.ai_messages` and emits SSE frames directly via `format_sse()`,
bypassing the EventBus. In the new architecture, resume events flow through
executor -> EventBus -> SSEBridge -> writer (registered by wait_for_tasks) ->
lead stream -> frontend.

#### 5.2 Change POST /resume endpoint

The endpoint at `POST /api/threads/{thread_id}/subagents/{task_id}/resume`
(line ~1058) currently returns an `EventSourceResponse` wrapping
`_subagent_resume_event_stream`. Change it to return a plain JSON response:

```python
@router.post("/threads/{thread_id}/subagents/{task_id}/resume")
async def resume_subagent(thread_id: str, task_id: str, body: ResumeRequest):
    resume_background_subagent(task_id, body.resume)
    return JSONResponse(status_code=202, content={"task_id": task_id, "status": "resumed"})
```

### 6. SSEBridge: Writer Lifecycle Fix + Docstring Update

File: `subagents/sse_bridge.py`

#### 6.1 Fix `unregister_writer` for multi-subagent scenarios

Current `unregister_writer` (lines 51-60) only checks for IDLE subagents
before removing the writer. This breaks multi-subagent scenarios:

```
1. task("A") -> registers writer
2. task("B") -> registers writer (same thread_id, overwrites with same writer)
3. wait_for_tasks(["A"]) -> A done -> unregister_writer -> writer removed
4. B still RUNNING -> events dropped!
```

Fix: check for **any non-terminal** subagents (RUNNING, INTERRUPTED, IDLE):

```python
def unregister_writer(self, thread_id: str) -> None:
    from deerflow.subagents.agent_registry import agent_registry
    refs = agent_registry.list_by_thread(thread_id)
    if any(not ref.status.is_terminal() for ref in refs):
        return  # keep writer alive for active subagents
    with self._lock:
        self._writers.pop(thread_id, None)
```

This replaces the current `list_idle` check with a broader non-terminal check.

#### 6.2 Docstring update

Update the docstring (lines 1-8) to remove the `task(detached=False)`
reference and reflect the new always-register behavior:
- `task()` (always) / `wait_for_tasks` / `follow_up` register their writer
  on entry and unregister on exit.

### 7. Lead Agent Prompt Update

File: `agents/lead_agent/prompt.py`

Update the subagent instructions section:
- Remove the detached/blocking distinction
- `task()` always returns immediately with a task_id
- Must call `wait_for_tasks([task_id])` to get the result
- If `wait_for_tasks` returns `interrupted` status (should not happen in new
  architecture since it polls through), explain the POST /resume flow
- Update examples to show `task()` + `wait_for_tasks()` pattern

### 8. Documentation Update

File: `docs/SUBAGENTS.md`

- Remove Route 甲 / Route 乙 distinction
- Describe the unified event flow
- Update the interrupt + resume section to reflect the new fire-and-forget
  POST /resume + event-bus-driven SSE delivery

---

## Testing Plan

### Unit Tests

1. **Executor event emission** (`tests/test_subagent_executor.py`):
   - Test `_aexecute` emits `interrupted` lifecycle event on interrupt
   - Test `_aexecute` emits `cancelled` lifecycle event on cancel
   - Test `_aexecute` emits progress with `message` content
   - Test `_aresume` emits `running` at start
   - Test `_aresume` emits progress during astream
   - Test `_aresume` emits `completed`/`interrupted`/`failed`/`cancelled`
   - Test `_acontinue` emits progress during astream
   - Test `_acontinue` emits `interrupted`/`cancelled`/`failed` lifecycle events

2. **wait_for_tasks** (`tests/test_wait_for_tasks.py` or `tests/test_task_tool_core_logic.py`):
   - Test polling continues through INTERRUPTED (does not return immediately)
   - Test timeout is suspended during INTERRUPTED
   - Test returns immediately for IDLE/COMPLETED/FAILED/CANCELLED
   - Test no duplicate lifecycle events emitted (executor is sole emitter)
   - Test cleanup_background_task called for terminal tasks after returning
   - Test multi-subagent: wait_for_tasks(["A"]) with B still RUNNING does not
     unregister writer (B's events still flow)

3. **task_tool** (`tests/test_task_tool_core_logic.py`):
   - Test `task()` always returns immediately (no blocking)
   - Test writer is always registered
   - Test `detached` parameter is gone
   - Remove all poll-loop-specific tests (blocking mode no longer exists)

4. **Storage consolidation** (`tests/test_subagent_executor.py`, `tests/test_subagent_registry_shim.py`):
   - Test `get_background_task_result` reads from agent_registry only (no `_background_tasks` fallback)
   - Test `list_background_tasks` reads from agent_registry only
   - Test `get_subagent_executor` reads from agent_registry only
   - Test `cleanup_background_task` removes from agent_registry only
   - Test `resume_background_subagent` uses agent_registry only (no fallback)
   - Verify `_background_tasks` and `_subagent_executors` no longer exist as module attributes
   - Update all tests that directly manipulate `_background_tasks`/`_subagent_executors` to use `agent_registry.register()` instead

5. **Resume endpoint** (`tests/test_thread_runs.py`):
   - Test POST /resume returns 202 JSON, not SSE stream

### Integration Tests

6. **End-to-end interrupt + resume** (`tests/test_subagent_interrupt_and_resume.py`):
   - Spawn subagent via `task()` -> subagent interrupts -> `wait_for_tasks`
     keeps polling -> POST /resume -> subagent completes -> `wait_for_tasks`
     returns result
   - Verify all lifecycle events flow through EventBus -> SSEBridge -> writer

7. **Detached subagent message delivery** (new test):
   - Spawn subagent via `task()` -> don't call wait_for_tasks immediately
   - Verify progress events reach frontend via SSEBridge (writer registered
     at task() time)

---

## Files Changed

| File | Change |
|------|--------|
| `subagents/executor.py` | Add lifecycle+progress emission to `_aexecute` (interrupt/cancel/budget), `_aresume` (all events), `_acontinue` (progress + missing lifecycle); enrich progress with message content; delete `_background_tasks`/`_background_tasks_lock`/`_subagent_executors`; rewrite wrapper functions (`get_background_task_result`, `list_background_tasks`, `get_subagent_executor`, `cleanup_background_task`) to use agent_registry only; simplify `resume_background_subagent`; update `execute_async`/`resume_async` to use agent_registry |
| `tools/builtins/task_tool.py` | Remove `detached` parameter; remove poll loop (~250 lines); always register writer; always return immediately |
| `tools/builtins/wait_for_tasks.py` | Don't return for INTERRUPTED; keep polling; suspend timeout during INTERRUPTED |
| `subagents/sse_bridge.py` | Fix `unregister_writer` to check all non-terminal subagents (not just IDLE); docstring update |
| `app/gateway/routers/thread_runs.py` | Delete `_subagent_resume_event_stream`; change POST /resume to return 202 JSON |
| `agents/lead_agent/prompt.py` | Update subagent instructions (no detached/blocking; task() + wait_for_tasks() pattern) |
| `docs/SUBAGENTS.md` | Update Route 甲/乙 docs to unified flow; remove `_background_tasks` references |
| `tests/test_subagent_executor.py` | Update tests that manipulate `_background_tasks` to use agent_registry; add event emission tests |
| `tests/test_subagent_interrupt_and_resume.py` | Update storage manipulation; add end-to-end interrupt+resume test |
| `tests/test_subagent_status_semantics.py` | Update storage manipulation |
| `tests/test_task_tool_core_logic.py` | Update for removed poll loop and `detached` parameter |
| `tests/test_subagent_detached.py` | Update for removed `detached` parameter |
| `tests/test_subagent_registry_shim.py` | Update wrapper function tests |
| `agents/middlewares/subagent_context_middleware.py` | **New file**: `SubagentStatusMessage` + `SubagentContextMiddleware` (consolidates SubagentLimitMiddleware + PendingTaskGuardMiddleware + status injection via `wrap_model_call`) |
| `agents/middlewares/subagent_limit_middleware.py` | **Deleted**: logic absorbed into `SubagentContextMiddleware._enforce_limit` |
| `agents/middlewares/pending_task_guard_middleware.py` | **Deleted**: logic absorbed into `SubagentContextMiddleware._enforce_guard` |
| `agents/lead_agent/agent.py` | Replace `SubagentLimitMiddleware` + `PendingTaskGuardMiddleware` with single `SubagentContextMiddleware` |
| `tests/test_subagent_context_middleware.py` | **New file**: tests for status injection, limit enforcement, guard enforcement |
| `tests/test_subagent_limit_middleware.py` | **Deleted**: tests moved to `test_subagent_context_middleware.py` |
| `tests/test_pending_task_guard_middleware.py` | **Deleted**: tests moved to `test_subagent_context_middleware.py` |

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| `wait_for_tasks` blocking indefinitely if resume never comes | Timeout is suspended during INTERRUPTED but the lead agent can still be cancelled (cancel_event). The lead prompt instructs calling wait_for_tasks with a reasonable timeout. |
| Subagent completes before `wait_for_tasks` is called | Safe: result stored in `agent_registry`; lifecycle events flow via writer (registered at `task()` time); `wait_for_tasks` returns immediately when called. No data loss. |
| Writer prematurely unregistered in multi-subagent scenarios | Fixed: `unregister_writer` checks all non-terminal subagents, not just IDLE. |
| Frontend expects SSE stream from POST /resume | Frontend update needed: POST /resume returns 202, events arrive via the existing lead run stream (already watched by frontend). |
| `message_index` in `_acontinue` starts from 1 (fresh ai_messages) | This is correct behavior - follow_up is a new prompt, not a continuation of the same message sequence. Frontend can distinguish by the `revived` lifecycle event. |
| Large diff across 7+ files | Acceptable for development phase (no users). All changes are in one logical unit: "unify event bus". |
| Status message at end of context may distract model | Mitigated: message uses XML tags (`<subagent-status>`) for clear delimitation; `hide_from_ui=True` prevents frontend duplication; content is concise (one line per subagent). |
| `_enforce_limit` and `_enforce_guard` ordering in single `after_model` | Safe: mutually exclusive conditions (limit applies when AIMessage HAS task calls; guard applies when AIMessage has NO tool calls). Original reverse-order execution had same effective behavior. |

---

## 9. SubagentContextMiddleware: Lead Agent Awareness

### Problem

In the unified event bus architecture, the lead model's context only updates at
tool call boundaries. SSE events (task_started, task_progress, task_completed,
etc.) flow to the **frontend**, not to the lead model's message context. The
model has no way to know subagent status until it calls `wait_for_tasks` and
gets the result as a tool return value.

This means:
- The model cannot make informed decisions about whether to wait, do other work,
  or spawn more subagents.
- The model must blindly call `wait_for_tasks` without knowing if the subagent
  is still running or already done.
- Parallel subagent orchestration is impaired - the model can't reason about
  which subagents are in flight.

Additionally, two existing lead-agent middlewares (`SubagentLimitMiddleware`
and `PendingTaskGuardMiddleware`) handle subagent-related concerns with
overlapping registry queries and similar patterns. Consolidating them reduces
code duplication and centralizes subagent-awareness logic.

### Design

Create `SubagentContextMiddleware` that consolidates subagent-related
lead-agent middlewares and injects real-time subagent status into the lead
model's context.

#### 9.1 New file: `agents/middlewares/subagent_context_middleware.py`

Contains two classes:
- `SubagentStatusMessage` - custom SystemMessage for status injection
- `SubagentContextMiddleware` - the middleware itself

#### 9.2 `SubagentStatusMessage`

A `SystemMessage` subclass that carries a structured subagent status snapshot.
Built from `agent_registry.list_by_thread(thread_id)`.

```python
class SubagentStatusMessage(SystemMessage):
    """Lead agent context: real-time subagent status snapshot."""

    @classmethod
    def from_refs(cls, refs: list[AgentRef]) -> "SubagentStatusMessage":
        running = [r for r in refs if r.status == SubagentStatus.RUNNING]
        interrupted = [r for r in refs if r.status == SubagentStatus.INTERRUPTED]
        completed = [r for r in refs if r.status == SubagentStatus.COMPLETED]
        idle = [r for r in refs if r.status == SubagentStatus.IDLE]
        failed = [r for r in refs if r.status == SubagentStatus.FAILED]

        content = _format_status_content(running, interrupted, completed, idle, failed)

        return cls(
            content=content,
            additional_kwargs={
                "hide_from_ui": True,
                "subagent_status": True,
                "subagents": _pack_structured(refs),
            },
        )
```

**Content format** (human-readable, XML-tagged for model parsing):

```xml
<subagent-status>
## Running (2)
- task abc123 (general-purpose): Analyzing sales data... [3/~10]
- task def456 (researcher): Searching for papers on... [1/~5]

## Completed (1)
- task ghi789 (analyst): Call wait_for_tasks(["ghi789"]) to retrieve result.

## Interrupted (1)
- task jkl012 (general-purpose): Awaiting user input via POST /resume.

## Idle (1)
- task mno345 (researcher): Parked. Use follow_up to continue.
</subagent-status>
```

**Design decisions:**
- `hide_from_ui: True` - the frontend receives subagent status via SSE events;
  this message is for the model only.
- COMPLETED subagents show **status only** (not results). The model must call
  `wait_for_tasks` to retrieve the actual result. This keeps context clean and
  ensures `wait_for_tasks` handles cleanup (`cleanup_background_task`).
- `additional_kwargs.subagents` carries structured data for debugging and
  potential future use (e.g., middleware self-inspection).
- No `id` is set on the message - it is transient (request-only via
  `wrap_model_call`), never persisted to checkpoint state.

#### 9.3 `SubagentContextMiddleware` - hooks

The middleware implements **two hooks**:

**Hook 1: `awrap_model_call` - status injection (transient)**

```python
async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
    thread_id = request.runtime.context.get("thread_id") if request.runtime.context else None
    if not thread_id:
        return await handler(request)

    refs = agent_registry.list_by_thread(thread_id)
    if not refs:
        return await handler(request)  # no subagents, skip injection

    status_msg = SubagentStatusMessage.from_refs(refs)
    augmented = request.override(messages=[*request.messages, status_msg])
    return await handler(augmented)
```

**Why `wrap_model_call` (transient) instead of `before_model` (persistent)?**
- The status is dynamic - it changes on every model call as subagents progress.
  Persisting it to checkpoint would create stale snapshots in history.
- Transient injection (`request.override`) does not touch `state["messages"]`,
  keeping the checkpoint clean. This matches the pattern used by
  `LoopDetectionMiddleware` and `DanglingToolCallMiddleware`.
- The message is always at the **end** of the request messages (appended last),
  ensuring the model sees it right before generating its response.
- No ID-swap or RemoveMessage complexity needed - the message simply doesn't
  survive past the request.

**Hook 2: `aafter_model` - consolidated limit + guard (absorbed)**

Absorbs the logic from `SubagentLimitMiddleware` and `PendingTaskGuardMiddleware`
into a single `after_model` implementation:

```python
async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
    messages = state.get("messages", [])
    if not messages:
        return None
    last = messages[-1]
    if not isinstance(last, AIMessage):
        return None

    # 1. SubagentLimit logic: truncate excess task tool calls
    update = self._enforce_limit(last)  # returns dict or None

    # 2. PendingTaskGuard logic: prevent premature termination
    if update is None:
        update = self._enforce_guard(state, runtime, last)  # returns dict or None

    return update
```

**`_enforce_limit`** (from `SubagentLimitMiddleware`):
- Count `task` tool_calls in the AIMessage.
- If count > `max_concurrent` (default 3, clamped [2,4]): rebuild AIMessage with
  truncated tool_calls via `clone_ai_message_with_tool_calls(...)`.
- Return `{"messages": [updated_msg]}` or `None`.

**`_enforce_guard`** (from `PendingTaskGuardMiddleware`):
- If AIMessage has **no tool_calls** (wants to terminate) and registry has
  PENDING/RUNNING subagents for this thread:
  - Increment `_count`.
  - Append `SystemMessage(content=reminder)` listing running task_ids.
  - If `_count >= 3`: also set `tool_choice = {"type": "tool", "name": "wait_for_tasks"}`.
  - Return `{"messages": [reminder]}` (+ optional `tool_choice`).
- Otherwise return `None`.

The `_count` counter is instance state on the middleware (same as
`PendingTaskGuardMiddleware._count`). It only increments, never resets
within a run. The middleware instance is created fresh per agent run (in
`build_middlewares`), so `_count` starts at 0 each invocation.

#### 9.4 Wiring changes

File: `agents/lead_agent/agent.py`

**Remove** (lines ~427-437):
```python
if subagent_enabled:
    middlewares.append(SubagentLimitMiddleware(max_concurrent=...))
    middlewares.append(PendingTaskGuardMiddleware())
```

**Replace with**:
```python
if subagent_enabled:
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
    middlewares.append(
        SubagentContextMiddleware(max_concurrent=max_concurrent_subagents)
    )
```

#### 9.5 Files deleted

- `agents/middlewares/subagent_limit_middleware.py` - logic absorbed into
  `SubagentContextMiddleware._enforce_limit`
- `agents/middlewares/pending_task_guard_middleware.py` - logic absorbed into
  `SubagentContextMiddleware._enforce_guard`

#### 9.6 Files kept separate (not consolidated)

| Middleware | Why it stays separate |
|------------|----------------------|
| `YieldReminderMiddleware` | Runs on **subagent** runtime, not lead. Different lifecycle. |
| `ToolErrorHandlingMiddleware` | Runs on **shared** runtime (lead + subagent). Wraps tool calls, not model calls. Different concern (error handling + status stamping). |
| `TokenUsageMiddleware` | Runs on lead, but different concern (token accounting). No overlap with status injection. |

#### 9.7 Interaction with unified event bus

The middleware and the event bus are **complementary layers**:

| Layer | Purpose | Audience |
|-------|---------|----------|
| Event Bus (executor -> SSEBridge -> writer -> stream) | Real-time progress + lifecycle events | Frontend (SSE) |
| `SubagentContextMiddleware` (wrap_model_call) | Status snapshot before each model call | Lead model (context) |

The middleware queries `agent_registry` directly (not the EventBus), so it
reflects the **current** state regardless of whether events have been processed
yet. This is a read-only query - the middleware never modifies subagent state.

### Testing for Section 9

**Unit tests** (`tests/test_subagent_context_middleware.py` - new file):

1. **Status injection**:
   - Test `SubagentStatusMessage.from_refs()` builds correct content for each
     status (RUNNING, COMPLETED, INTERRUPTED, IDLE, FAILED).
   - Test content format includes task_id, subagent type, description.
   - Test COMPLETED shows "call wait_for_tasks" (not result).
   - Test `additional_kwargs` has `hide_from_ui`, `subagent_status`, `subagents`.

2. **`wrap_model_call` injection**:
   - Test no subagents -> handler called with original request (no injection).
   - Test with subagents -> handler called with augmented request (status message
     appended at end).
   - Test injected message is `SubagentStatusMessage` with correct content.
   - Test injected message is NOT persisted to state (request-only).

3. **`after_model` limit enforcement** (absorbed from SubagentLimitMiddleware tests):
   - Test AIMessage with >max_concurrent task calls -> truncated.
   - Test AIMessage with <=max_concurrent task calls -> no change.

4. **`after_model` guard enforcement** (absorbed from PendingTaskGuardMiddleware tests):
   - Test AIMessage with no tool_calls + running subagents -> reminder injected.
   - Test 3rd reminder -> tool_choice forced to wait_for_tasks.
   - Test AIMessage with tool_calls -> no guard (model is doing work).
   - Test no running subagents -> no guard.

5. **Integration with existing tests**:
   - Move/adapt tests from `test_subagent_limit_middleware.py` and
     `test_pending_task_guard_middleware.py` into `test_subagent_context_middleware.py`.
   - Delete the old test files.
