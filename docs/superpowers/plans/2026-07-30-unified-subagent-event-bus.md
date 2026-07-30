# Unified Subagent Event Bus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify DeerFlow's subagent event-passing to a single path where the executor is the sole event emitter, `task()` always returns immediately (no blocking mode), and `wait_for_tasks` is the only polling mechanism.

**Architecture:** Executor emits lifecycle + progress events directly to EventBus -> SSEBridge -> writer (registered at `task()` time) -> lead run stream -> frontend. `task()` always registers writer and returns immediately. `wait_for_tasks` polls through INTERRUPTED (doesn't return immediately), suspends timeout. Blocking poll loop deleted. Triple storage consolidated to agent_registry. New `SubagentContextMiddleware` injects status into lead model context and absorbs `SubagentLimitMiddleware` + `PendingTaskGuardMiddleware`.

**Tech Stack:** Python 3.12, LangGraph/LangChain agents middleware API, ruff (lint/format), pytest (tests)

## Global Constraints

- Backend test command: `cd backend && PYTHONPATH=. uv run pytest tests/test_<feature>.py -v`
- Full test suite: `cd backend && make test`
- Lint: `cd backend && make lint` (ruff check)
- Format: `cd backend && make format` (ruff format)
- TDD is mandatory: write failing test first, then implement
- No backward compatibility needed (Approach C - big bang)
- conftest.py mocks `deerflow.subagents.executor` with MagicMock to break circular import; tests use autouse fixture to clear mock and import real classes
- `SubagentStatus.is_terminal` = COMPLETED/FAILED/CANCELLED/TIMED_OUT
- `SubagentStatus.is_stopped` = is_terminal + INTERRUPTED + IDLE
- `agent_registry` is a process-level singleton with `AgentRef` keyed by `task_id`
- Harness never imports from `app.*` (enforced by `test_harness_boundary.py`)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `subagents/executor.py` | Sole event emitter; storage consolidated to agent_registry |
| `tools/builtins/task_tool.py` | Always registers writer, returns immediately; poll loop deleted |
| `tools/builtins/wait_for_tasks.py` | Polls through INTERRUPTED; cleanup responsibility |
| `subagents/sse_bridge.py` | Writer lifecycle: unregister checks all non-terminal subagents |
| `app/gateway/routers/thread_runs.py` | POST /resume returns 202 JSON; `_subagent_resume_event_stream` deleted |
| `agents/middlewares/subagent_context_middleware.py` | **New**: status injection + limit + guard (consolidated) |
| `agents/middlewares/subagent_limit_middleware.py` | **Deleted**: absorbed into SubagentContextMiddleware |
| `agents/middlewares/pending_task_guard_middleware.py` | **Deleted**: absorbed into SubagentContextMiddleware |
| `agents/lead_agent/agent.py` | Wiring: single SubagentContextMiddleware replaces two |
| `agents/lead_agent/prompt.py` | Updated subagent instructions |
| `docs/SUBAGENTS.md` | Updated documentation |
| `tests/test_subagent_executor.py` | Event emission + storage tests |
| `tests/test_subagent_registry_shim.py` | Wrapper function tests |
| `tests/test_subagent_status_semantics.py` | Storage manipulation updated |
| `tests/test_subagent_interrupt_and_resume.py` | End-to-end interrupt+resume |
| `tests/test_task_tool_core_logic.py` | task_tool tests (no poll loop) |
| `tests/test_subagent_detached.py` | Updated (no detached param) |
| `tests/test_subagent_context_middleware.py` | **New**: consolidated middleware tests |
| `tests/test_subagent_limit_middleware.py` | **Deleted** |
| `tests/test_pending_task_guard_middleware.py` | **Deleted** |

---

### Task 1: Storage Consolidation - Delete `_background_tasks`/`_subagent_executors`, Rewrite Wrappers

**Files:**
- Modify: `subagents/executor.py:234-242` (delete dicts), `:1292-1408` (execute_async), `:1912-1967` (resume_async), `:1996-2146` (wrapper functions)
- Test: `tests/test_subagent_executor.py`, `tests/test_subagent_registry_shim.py`, `tests/test_subagent_status_semantics.py`

**Interfaces:**
- Consumes: `agent_registry` singleton (register, get, list_by_thread, list_all, update_status, remove)
- Produces: `get_background_task_result`, `list_background_tasks`, `get_subagent_executor`, `cleanup_background_task`, `resume_background_subagent`, `request_cancel_background_task` - all rewritten to use agent_registry only

- [ ] **Step 1: Write failing tests for wrapper functions using agent_registry only**

Add to `tests/test_subagent_registry_shim.py`:

```python
def test_get_background_task_result_uses_registry_only(monkeypatch):
    """get_background_task_result must not fall back to _background_tasks."""
    from deerflow.subagents.executor import get_background_task_result
    from deerflow.subagents.agent_registry import agent_registry, AgentRef
    from deerflow.subagents.executor import SubagentResult, SubagentStatus

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-1", trace_id="t", status=SubagentStatus.COMPLETED)
    agent_registry.register(AgentRef(
        task_id="test-1", thread_id="th", trace_id="t", subagent_type="general-purpose",
        status=SubagentStatus.COMPLETED, config=None, executor=None, result=result,
        description="", created_at=datetime.now(),
    ))
    assert get_background_task_result("test-1") is result
    assert get_background_task_result("nonexistent") is None


def test_list_background_tasks_uses_registry_only():
    """list_background_tasks must not merge from _background_tasks."""
    from deerflow.subagents.executor import list_background_tasks
    from deerflow.subagents.agent_registry import agent_registry, AgentRef
    from deerflow.subagents.executor import SubagentResult, SubagentStatus

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-2", trace_id="t", status=SubagentStatus.COMPLETED)
    agent_registry.register(AgentRef(
        task_id="test-2", thread_id="th", trace_id="t", subagent_type="general-purpose",
        status=SubagentStatus.COMPLETED, config=None, executor=None, result=result,
        description="", created_at=datetime.now(),
    ))
    tasks = list_background_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == "test-2"


def test_cleanup_background_task_uses_registry_only():
    """cleanup_background_task must not touch _background_tasks or _subagent_executors."""
    from deerflow.subagents.executor import cleanup_background_task
    from deerflow.subagents.agent_registry import agent_registry, AgentRef
    from deerflow.subagents.executor import SubagentResult, SubagentStatus

    agent_registry._refs.clear()
    result = SubagentResult(task_id="test-3", trace_id="t", status=SubagentStatus.COMPLETED)
    result.try_set_terminal(SubagentStatus.COMPLETED, result="done")
    agent_registry.register(AgentRef(
        task_id="test-3", thread_id="th", trace_id="t", subagent_type="general-purpose",
        status=SubagentStatus.COMPLETED, config=None, executor=None, result=result,
        description="", created_at=datetime.now(),
    ))
    cleanup_background_task("test-3")
    assert agent_registry.get("test-3") is None


def test_background_tasks_dicts_no_longer_exist():
    """_background_tasks and _subagent_executors must not exist as module attributes."""
    import deerflow.subagents.executor as executor_module

    assert not hasattr(executor_module, "_background_tasks")
    assert not hasattr(executor_module, "_background_tasks_lock")
    assert not hasattr(executor_module, "_subagent_executors")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_registry_shim.py -v`
Expected: FAIL - `_background_tasks` still exists, wrapper functions still have fallback paths

- [ ] **Step 3: Delete `_background_tasks`, `_background_tasks_lock`, `_subagent_executors`**

In `subagents/executor.py`, delete lines 234-242:

```python
# DELETE these lines:
_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()

_subagent_executors: dict[str, SubagentExecutor] = {}
```

- [ ] **Step 4: Rewrite wrapper functions to use agent_registry only**

Replace `get_background_task_result` (lines 1996-2011):

```python
def get_background_task_result(task_id: str) -> SubagentResult | None:
    """Get the result of a background task."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    return ref.result if ref is not None else None
```

Replace `list_background_tasks` (lines 2014-2031):

```python
def list_background_tasks() -> list[SubagentResult]:
    """List all background tasks."""
    from deerflow.subagents.agent_registry import agent_registry

    return [ref.result for ref in agent_registry.list_all() if ref.result is not None]
```

Replace `cleanup_background_task` (lines 2034-2076):

```python
def cleanup_background_task(task_id: str) -> None:
    """Remove a completed task from the registry.

    Only removes tasks in a truly terminal state to avoid race conditions
    with the background executor. INTERRUPTED and IDLE tasks stay resident.
    """
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    if ref is None or ref.result is None:
        return
    if ref.result.status.is_terminal:
        agent_registry.remove(task_id)
    else:
        logger.debug(
            "Skipping cleanup for non-terminal background task %s (status=%s)",
            task_id,
            ref.result.status.value,
        )
```

Replace `get_subagent_executor` (lines 2079-2091):

```python
def get_subagent_executor(task_id: str) -> SubagentExecutor | None:
    """Return the resident executor for a background task, if any."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    return ref.executor if ref is not None else None
```

Replace `get_subagent_interrupt` (lines 2094-2115) - remove `_background_tasks` fallback:

```python
def get_subagent_interrupt(task_id: str) -> dict[str, Any] | None:
    """Return interrupt metadata for a paused subagent, or None."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    result = ref.result if ref is not None else None
    if result is None or result.status is not SubagentStatus.INTERRUPTED:
        return None
    return {
        "task_id": result.task_id,
        "subagent_thread_id": result.subagent_thread_id,
        "interrupts": result.interrupts,
        "interrupted_at": (result.interrupted_at.isoformat() if result.interrupted_at else None),
    }
```

Replace `resume_background_subagent` (lines 2118-2146) - remove fallback:

```python
def resume_background_subagent(task_id: str, resume_value: Any) -> str:
    """Resume an INTERRUPTED background subagent by task_id."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    if ref is None or ref.executor is None or ref.result is None:
        raise KeyError(f"Unknown subagent task {task_id}")
    if ref.result.status is not SubagentStatus.INTERRUPTED:
        raise RuntimeError(f"Subagent task {task_id} is not interrupted (status={ref.result.status.value}); cannot resume")
    return ref.executor.resume_async(resume_value, task_id)
```

Replace `request_cancel_background_task` (lines 1973-1993) - remove fallback:

```python
def request_cancel_background_task(task_id: str) -> None:
    """Signal a running background task to stop."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    result = ref.result if ref is not None else None
    if result is not None:
        result.cancel_event.set()
        logger.info("Requested cancellation for background task %s", task_id)
```

- [ ] **Step 5: Update `execute_async` to remove dict writes**

In `execute_async` (lines 1292-1408), remove all `_background_tasks`/`_subagent_executors` writes:

Delete lines 315-319:
```python
# DELETE:
with _background_tasks_lock:
    _background_tasks[task_id] = result
    _subagent_executors[task_id] = self
```

In `run_task` (line 1344), replace the dict writes:
```python
# Replace lines 1345-1348:
def run_task():
    from deerflow.subagents.agent_registry import agent_registry

    agent_registry.update_status(task_id, SubagentStatus.RUNNING)
    result_holder = result  # use the already-created result object
```

In the exception handler (lines 1392-1405), replace:
```python
# Replace line 1394-1396:
except Exception as e:
    logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
    result.try_set_terminal(SubagentStatus.FAILED, error=str(e))
```

Remove the `event_bus.emit` for "failed" in the exception handler (lines 1397-1405) - the executor's `_aexecute` already emits this. Wait - actually this handler wraps the scheduler-level exception (if `_aexecute` itself throws before it can emit). Keep this emit but remove the dict reference. Actually, looking at the code more carefully, the `run_task` try/except wraps `execution_future.result()`. If `_aexecute` raises, it's caught here. But `_aexecute` has its own try/except that emits "failed". So this is a safety net for scheduler-level failures. Keep the emit, just remove dict references.

```python
except Exception as e:
    logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
    result.try_set_terminal(SubagentStatus.FAILED, error=str(e))
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "failed", "task_id": task_id, "thread_id": self.thread_id or "", "error": str(e)},
    )
```

- [ ] **Step 6: Update `resume_async` to remove dict writes**

In `resume_async` (lines 1912-1967), replace all `_background_tasks` references:

```python
def resume_async(self, resume_value: Any, task_id: str) -> str:
    """Resume an INTERRUPTED background subagent."""
    from deerflow.subagents.agent_registry import agent_registry

    ref = agent_registry.get(task_id)
    if ref is None or ref.result is None:
        raise KeyError(f"Unknown subagent task {task_id}")
    result_holder = ref.result
    if not result_holder.try_resume():
        raise RuntimeError(f"Subagent task {task_id} is not interrupted (status={result_holder.status.value}); cannot resume")
    result_holder.started_at = datetime.now()

    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} resuming, task_id={task_id}, timeout={self.config.timeout_seconds}s")

    parent_context = copy_context()

    def run_task():
        agent_registry.update_status(task_id, SubagentStatus.RUNNING)

        try:
            execution_future = _submit_to_isolated_loop_in_context(
                parent_context,
                lambda: self._aresume(resume_value, result_holder),
            )
            try:
                execution_future.result(timeout=self.config.timeout_seconds)
            except FuturesTimeoutError:
                logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} resume timed out after {self.config.timeout_seconds}s")
                result_holder.cancel_event.set()
                result_holder.try_set_terminal(
                    SubagentStatus.TIMED_OUT,
                    error=f"Resume timed out after {self.config.timeout_seconds} seconds",
                )
                execution_future.cancel()
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} resume failed")
            result_holder.try_set_terminal(SubagentStatus.FAILED, error=str(e))

    _scheduler_pool.submit(run_task)
    return task_id
```

- [ ] **Step 7: Update tests that directly manipulate `_background_tasks`/`_subagent_executors`**

In `tests/test_subagent_status_semantics.py` (lines 163-176), replace direct dict manipulation with `agent_registry.register()`.

In `tests/test_subagent_interrupt_and_resume.py` (lines 248-282), replace direct dict manipulation.

In `tests/test_subagent_executor.py`, replace all `_background_tasks[task_id] = result` and `_subagent_executors[task_id] = executor` with `agent_registry.register(AgentRef(...))`.

Pattern for replacement:
```python
# OLD:
executor_module._background_tasks[task_id] = result
executor_module._subagent_executors[task_id] = executor

# NEW:
from deerflow.subagents.agent_registry import agent_registry, AgentRef
from datetime import datetime
agent_registry.register(AgentRef(
    task_id=task_id, thread_id="test-thread", trace_id="test",
    subagent_type="general-purpose", status=SubagentStatus.PENDING,
    config=config, executor=executor, result=result,
    description="test", created_at=datetime.now(),
))
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py tests/test_subagent_registry_shim.py tests/test_subagent_status_semantics.py tests/test_subagent_interrupt_and_resume.py -v`
Expected: PASS

- [ ] **Step 9: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 10: Commit**

```bash
git add backend/packages/harness/deerflow/subagents/executor.py backend/tests/test_subagent_executor.py backend/tests/test_subagent_registry_shim.py backend/tests/test_subagent_status_semantics.py backend/tests/test_subagent_interrupt_and_resume.py
git commit -m "refactor: consolidate subagent storage to agent_registry, delete _background_tasks/_subagent_executors"
```

---

### Task 2: Executor `_aexecute` Event Emission - Add Missing Lifecycle Events + Enrich Progress

**Files:**
- Modify: `subagents/executor.py:1022-1210` (`_aexecute` method)
- Test: `tests/test_subagent_executor.py`

**Interfaces:**
- Consumes: `event_bus` singleton, `serialize_lc_object`
- Produces: `subagent:lifecycle` events for `interrupted`/`cancelled`/`failed`(budget); `subagent:progress` events enriched with `message`/`message_index`/`total_messages`

- [ ] **Step 1: Write failing tests for new event emissions**

Add to `tests/test_subagent_executor.py`:

```python
@pytest.mark.asyncio
async def test_aexecute_emits_interrupted_lifecycle_event(monkeypatch):
    """_aexecute must emit subagent:lifecycle interrupted when __interrupt__ is detected."""
    from deerflow.subagents.event_bus import event_bus
    from deerflow.subagents.executor import SubagentExecutor, SubagentResult, SubagentStatus

    events = []
    event_bus.on("subagent:lifecycle", lambda p: events.append(p))
    try:
        # ... setup executor with mock agent that yields __interrupt__ chunk
        # ... call _aexecute
        # ... assert events contains {"event": "interrupted", ...}
    finally:
        pass  # event_bus listeners persist; cleanup if needed
```

(Write similar tests for `cancelled` and `failed`(budget) lifecycle events, and progress enrichment with `message`/`message_index`/`total_messages`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "emits_interrupted or emits_cancelled or emits_budget_failed or progress_enriched"`
Expected: FAIL

- [ ] **Step 3: Add `interrupted` lifecycle event to `_aexecute`**

In `executor.py:1125-1132`, before `return result`, add:

```python
if interrupts:
    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} interrupted awaiting human input")
    result.try_set_interrupted(
        interrupts=serialize_lc_object(interrupts),
        subagent_thread_id=self.subagent_thread_id,
        token_usage_records=collector.snapshot_records(),
    )
    event_bus.emit(
        "subagent:lifecycle",
        {
            "event": "interrupted",
            "task_id": result.task_id or "",
            "thread_id": self.thread_id or "",
            "interrupts": serialize_lc_object(interrupts),
        },
    )
    return result
```

- [ ] **Step 4: Add `cancelled` lifecycle events to `_aexecute`**

At line 1027-1037 (cancel detection in astream loop), before `return result`, add:

```python
if result.cancel_event.is_set():
    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled by parent")
    from deerflow.subagents.agent_registry import agent_registry

    agent_registry.update_status(result.task_id or "", SubagentStatus.CANCELLED)
    result.try_set_terminal(
        SubagentStatus.CANCELLED,
        error="Cancelled by user",
        token_usage_records=collector.snapshot_records(),
    )
    event_bus.emit(
        "subagent:lifecycle",
        {"event": "cancelled", "task_id": result.task_id or "", "thread_id": self.thread_id or ""},
    )
    return result
```

At line 1113-1123 (cancel after stream end), before `return result`, add the same `event_bus.emit` for `cancelled`.

- [ ] **Step 5: Add `failed` lifecycle event for budget exceeded**

At line 1076-1096 (budget exceeded), after the existing `event_bus.emit("subagent:progress", ...)`, add before `break`:

```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "failed",
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "error": "request budget exceeded",
    },
)
```

- [ ] **Step 6: Enrich progress events with message content**

At line 1101-1108, replace the progress emit:

```python
event_bus.emit(
    "subagent:progress",
    {
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "status": result.status.value,
        "message": message_dict.get("content", ""),
        "message_index": len(ai_messages),
        "total_messages": len(ai_messages),
    },
)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "emits_interrupted or emits_cancelled or emits_budget_failed or progress_enriched"`
Expected: PASS

- [ ] **Step 8: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 9: Commit**

```bash
git add backend/packages/harness/deerflow/subagents/executor.py backend/tests/test_subagent_executor.py
git commit -m "feat: executor _aexecute emits interrupted/cancelled/budget-failed lifecycle events, enriches progress with message content"
```

---

### Task 3: Executor `_aresume` Event Emission - Add All Events

**Files:**
- Modify: `subagents/executor.py:1410-1551` (`_aresume` method)
- Test: `tests/test_subagent_executor.py`, `tests/test_subagent_interrupt_and_resume.py`

**Interfaces:**
- Consumes: `event_bus`, `serialize_lc_object`
- Produces: `subagent:lifecycle` events (`running`, `interrupted`, `cancelled`, `completed`, `idle`, `failed`); `subagent:progress` events with message content

- [ ] **Step 1: Write failing tests for _aresume event emission**

Add to `tests/test_subagent_executor.py`:

```python
@pytest.mark.asyncio
async def test_aresume_emits_running_at_start():
    """_aresume must emit subagent:lifecycle running at start."""
    # ... setup interrupted subagent, mock agent
    # ... call _aresume
    # ... assert first lifecycle event is {"event": "running", ...}

@pytest.mark.asyncio
async def test_aresume_emits_progress_during_astream():
    """_aresume must emit subagent:progress with message content during astream."""

@pytest.mark.asyncio
async def test_aresume_emits_completed_on_success():
    """_aresume must emit subagent:lifecycle completed on success."""

@pytest.mark.asyncio
async def test_aresume_emits_interrupted_on_reinterrupt():
    """_aresume must emit subagent:lifecycle interrupted on re-interrupt."""

@pytest.mark.asyncio
async def test_aresume_emits_cancelled_on_cancel():
    """_aresume must emit subagent:lifecycle cancelled on cancel."""

@pytest.mark.asyncio
async def test_aresume_emits_failed_on_exception():
    """_aresume must emit subagent:lifecycle failed on exception."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "aresume_emits"`
Expected: FAIL

- [ ] **Step 3: Add `running` lifecycle event at start of `_aresume`**

After line 1471 (`logger.info(... resuming after interrupt)`), add:

```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "running", "task_id": result.task_id or "", "thread_id": self.thread_id or ""},
)
```

- [ ] **Step 4: Add progress events during astream**

After line 1516 (`seen_message_ids.add(message_id)`), add:

```python
event_bus.emit(
    "subagent:progress",
    {
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "status": result.status.value,
        "message": message_dict.get("content", ""),
        "message_index": len(ai_messages),
        "total_messages": len(ai_messages),
    },
)
```

- [ ] **Step 5: Add lifecycle events for all terminal/interrupt/cancel paths**

Cancel pre-check (line 1480-1486), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "cancelled", "task_id": result.task_id or "", "thread_id": self.thread_id or ""},
)
```

Cancel in-loop (line 1489-1495), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "cancelled", "task_id": result.task_id or "", "thread_id": self.thread_id or ""},
)
```

Cancel post-loop (line 1518-1524), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "cancelled", "task_id": result.task_id or "", "thread_id": self.thread_id or ""},
)
```

Re-interrupt (line 1525-1532), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "interrupted",
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "interrupts": serialize_lc_object(interrupts),
    },
)
```

Completion (line 1534-1541), after `result.try_set_terminal(...)`, add:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "completed",
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "result": result.result,
    },
)
```

Failure (line 1543-1549), after `result.try_set_terminal(...)`, add:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "failed",
        "task_id": result.task_id or "",
        "thread_id": self.thread_id or "",
        "error": str(e),
    },
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "aresume_emits"`
Expected: PASS

- [ ] **Step 7: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 8: Commit**

```bash
git add backend/packages/harness/deerflow/subagents/executor.py backend/tests/test_subagent_executor.py
git commit -m "feat: executor _aresume emits lifecycle + progress events for all paths"
```

---

### Task 4: Executor `_acontinue` Event Emission - Add Progress + Missing Lifecycle

**Files:**
- Modify: `subagents/executor.py:1685-1910` (`_acontinue` method)
- Test: `tests/test_subagent_executor.py`

**Interfaces:**
- Consumes: `event_bus`, `serialize_lc_object`
- Produces: `subagent:progress` events with message content; `subagent:lifecycle` events for `interrupted`/`cancelled`/`failed`(budget/exception)

- [ ] **Step 1: Write failing tests for _acontinue event emission**

Add to `tests/test_subagent_executor.py`:

```python
@pytest.mark.asyncio
async def test_acontinue_emits_progress_during_astream():
    """_acontinue must emit subagent:progress with message content during astream."""

@pytest.mark.asyncio
async def test_acontinue_emits_interrupted_lifecycle():
    """_acontinue must emit subagent:lifecycle interrupted on interrupt."""

@pytest.mark.asyncio
async def test_acontinue_emits_cancelled_lifecycle():
    """_acontinue must emit subagent:lifecycle cancelled on cancel."""

@pytest.mark.asyncio
async def test_acontinue_emits_failed_on_budget_exceeded():
    """_acontinue must emit subagent:lifecycle failed on budget exceeded."""

@pytest.mark.asyncio
async def test_acontinue_emits_failed_on_exception():
    """_acontinue must emit subagent:lifecycle failed on exception."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "acontinue_emits"`
Expected: FAIL

- [ ] **Step 3: Add progress events during astream**

After line 1822 (`seen_message_ids.add(message_id)`), add:

```python
event_bus.emit(
    "subagent:progress",
    {
        "task_id": task_id,
        "thread_id": self.thread_id or "",
        "status": result.status.value,
        "message": message_dict.get("content", ""),
        "message_index": len(ai_messages),
        "total_messages": len(ai_messages),
    },
)
```

Note: the budget tick + at_hard_limit check follows immediately. Move the progress emit before `budget.tick()`.

- [ ] **Step 4: Add lifecycle events for missing paths**

Budget exceeded (line 1824-1832), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "failed",
        "task_id": task_id,
        "thread_id": self.thread_id or "",
        "error": "request budget exceeded",
    },
)
```

Cancel in-loop (line 1791-1800), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "cancelled", "task_id": task_id, "thread_id": self.thread_id or ""},
)
```

Cancel post-loop (line 1834-1843), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {"event": "cancelled", "task_id": task_id, "thread_id": self.thread_id or ""},
)
```

Interrupt (line 1845-1852), before `return result`:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "interrupted",
        "task_id": task_id,
        "thread_id": self.thread_id or "",
        "interrupts": serialize_lc_object(interrupts),
    },
)
```

Failure (line 1902-1908), after `result.try_set_terminal(...)`, add:
```python
event_bus.emit(
    "subagent:lifecycle",
    {
        "event": "failed",
        "task_id": task_id,
        "thread_id": self.thread_id or "",
        "error": str(e),
    },
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_executor.py -v -k "acontinue_emits"`
Expected: PASS

- [ ] **Step 6: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 7: Commit**

```bash
git add backend/packages/harness/deerflow/subagents/executor.py backend/tests/test_subagent_executor.py
git commit -m "feat: executor _acontinue emits progress + missing lifecycle events"
```

---

### Task 5: SSEBridge - Fix `unregister_writer` for Multi-Subagent Scenarios

**Files:**
- Modify: `subagents/sse_bridge.py:1-86`
- Test: `tests/test_sse_bridge.py` (new or existing)

**Interfaces:**
- Consumes: `agent_registry.list_by_thread`
- Produces: `unregister_writer` that checks all non-terminal subagents (not just IDLE)

- [ ] **Step 1: Write failing test for multi-subagent writer lifecycle**

```python
def test_unregister_writer_keeps_writer_when_other_subagents_running():
    """unregister_writer must not remove writer if any non-terminal subagents exist."""
    from deerflow.subagents.sse_bridge import SSEBridge
    from deerflow.subagents.agent_registry import agent_registry, AgentRef
    from deerflow.subagents.executor import SubagentStatus, SubagentResult
    from datetime import datetime

    agent_registry._refs.clear()
    bridge = SSEBridge()
    writer_called = []
    bridge.register_writer("thread-1", lambda e: writer_called.append(e))

    # Subagent A is COMPLETED (terminal), B is still RUNNING
    agent_registry.register(AgentRef(
        task_id="A", thread_id="thread-1", trace_id="", subagent_type="gp",
        status=SubagentStatus.COMPLETED, config=None, executor=None,
        result=SubagentResult(task_id="A", trace_id="", status=SubagentStatus.COMPLETED),
        description="", created_at=datetime.now(),
    ))
    agent_registry.register(AgentRef(
        task_id="B", thread_id="thread-1", trace_id="", subagent_type="gp",
        status=SubagentStatus.RUNNING, config=None, executor=None,
        result=SubagentResult(task_id="B", trace_id="", status=SubagentStatus.RUNNING),
        description="", created_at=datetime.now(),
    ))

    bridge.unregister_writer("thread-1")
    # Writer must still be registered because B is RUNNING
    assert "thread-1" in bridge._writers

    # Now mark B as COMPLETED
    agent_registry.update_status("B", SubagentStatus.COMPLETED)
    bridge.unregister_writer("thread-1")
    # Now writer should be removed
    assert "thread-1" not in bridge._writers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_sse_bridge.py -v -k "multi_subagent"`
Expected: FAIL - current `unregister_writer` only checks `list_idle`

- [ ] **Step 3: Fix `unregister_writer`**

In `sse_bridge.py:51-60`, replace:

```python
def unregister_writer(self, thread_id: str) -> None:
    from deerflow.subagents.agent_registry import agent_registry

    refs = agent_registry.list_by_thread(thread_id)
    if any(not ref.status.is_terminal for ref in refs):
        return  # keep writer alive for active subagents
    with self._lock:
        self._writers.pop(thread_id, None)
```

- [ ] **Step 4: Update docstring**

In `sse_bridge.py:1-8`, replace:

```python
"""Bridges EventBus payloads to per-lead-run LangGraph stream writers.

``get_stream_writer()`` is scoped to one lead run; the EventBus is
process-global. This bridge holds a ``thread_id -> writer`` map: lead-run
tools (``task()`` / ``wait_for_tasks`` / ``follow_up``) register their
writer on entry and unregister on exit. Events with no registered writer
are dropped from SSE (their state is already in the Registry, so no result
is lost).
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_sse_bridge.py -v`
Expected: PASS

- [ ] **Step 6: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 7: Commit**

```bash
git add backend/packages/harness/deerflow/subagents/sse_bridge.py backend/tests/test_sse_bridge.py
git commit -m "fix: SSEBridge unregister_writer checks all non-terminal subagents, not just IDLE"
```

---

### Task 6: task_tool - Remove `detached` Parameter, Remove Poll Loop, Always Register Writer

**Files:**
- Modify: `tools/builtins/task_tool.py:227-676`
- Test: `tests/test_task_tool_core_logic.py`, `tests/test_subagent_detached.py`

**Interfaces:**
- Consumes: `executor.execute_async`, `sse_bridge.register_writer`, `get_stream_writer`, `event_bus.emit`
- Produces: `task()` that always registers writer, always returns immediately with task_id

- [ ] **Step 1: Write failing tests for unified task() behavior**

Add to `tests/test_task_tool_core_logic.py`:

```python
@pytest.mark.asyncio
async def test_task_always_returns_immediately(mock_executor):
    """task() must always return immediately, never block."""
    # ... setup, call task()
    # ... assert return value contains task_id, not result

@pytest.mark.asyncio
async def test_task_always_registers_writer(mock_executor):
    """task() must always register SSE writer, even without detached=True."""
    # ... setup, call task()
    # ... assert sse_bridge.register_writer was called

@pytest.mark.asyncio
async def test_task_has_no_detached_param():
    """task() must not accept detached parameter."""
    import inspect
    from deerflow.tools.builtins.task_tool import task_tool
    sig = inspect.signature(task_tool)
    assert "detached" not in sig.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_task_tool_core_logic.py -v -k "always_returns or always_registers or no_detached"`
Expected: FAIL

- [ ] **Step 3: Remove `detached` parameter from task_tool signature**

In `task_tool.py:227-269`, remove `detached: bool = False` from the signature and remove the `detached` docstring entry.

- [ ] **Step 4: Replace detached branch + blocking poll loop with unified path**

Delete lines 384-648 (detached branch + entire blocking poll loop). Replace with:

```python
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Update to RUNNING; execute_async registers PENDING in agent_registry.
    if agent_registry.update_status(task_id, SubagentStatus.RUNNING) is None:
        from datetime import UTC, datetime

        from deerflow.subagents.agent_registry import AgentRef

        agent_registry.register(
            AgentRef(
                task_id=task_id,
                thread_id=thread_id or "",
                trace_id=trace_id or "",
                subagent_type=subagent_type,
                status=SubagentStatus.RUNNING,
                config=config,
                executor=executor,
                result=None,
                description=description,
                created_at=datetime.now(UTC),
            )
        )

    # Always register SSE writer so subagent events reach the frontend
    # from the moment it is spawned.
    writer = get_stream_writer()
    sse_bridge.register_writer(thread_id, writer)

    event_bus.emit(
        "subagent:lifecycle",
        {
            "event": "started",
            "task_id": task_id,
            "thread_id": thread_id,
            "description": description,
        },
    )

    return f"Task spawned. task_id={task_id}. Call wait_for_tasks([{task_id!r}]) to collect."
```

- [ ] **Step 5: Remove the `except asyncio.CancelledError` handler for the poll loop**

If there's a `try/except asyncio.CancelledError` block wrapping the poll loop (line 649+), remove it since there's no poll loop to cancel. The `task()` function now returns immediately.

- [ ] **Step 6: Remove unused imports and helpers**

Remove `_await_subagent_terminal`, `_deferred_cleanup_subagent_task`, `_schedule_deferred_subagent_cleanup` if they are only used by the deleted poll loop. Check all references before deleting.

- [ ] **Step 7: Update `test_subagent_detached.py`**

Rename or refactor tests that test `detached=True` vs `detached=False`. All tests should now test the unified path: `task()` always returns immediately, `wait_for_tasks()` collects results.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_task_tool_core_logic.py tests/test_subagent_detached.py -v`
Expected: PASS

- [ ] **Step 9: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 10: Commit**

```bash
git add backend/packages/harness/deerflow/tools/builtins/task_tool.py backend/tests/test_task_tool_core_logic.py backend/tests/test_subagent_detached.py
git commit -m "refactor: task_tool always registers writer and returns immediately, removes blocking poll loop and detached parameter"
```

---

### Task 7: wait_for_tasks - Poll Through INTERRUPTED, Delete Duplicate Events, Add Cleanup

**Files:**
- Modify: `tools/builtins/wait_for_tasks.py:1-98`
- Test: `tests/test_task_tool_core_logic.py` (or new `tests/test_wait_for_tasks.py`)

**Interfaces:**
- Consumes: `agent_registry.get`, `SubagentStatus`, `cleanup_background_task`
- Produces: `wait_for_tasks` that polls through INTERRUPTED, suspends timeout, cleans up terminal tasks

- [ ] **Step 1: Write failing tests for new wait_for_tasks behavior**

```python
@pytest.mark.asyncio
async def test_wait_for_tasks_polls_through_interrupted():
    """wait_for_tasks must NOT return immediately for INTERRUPTED status."""
    # ... setup subagent in INTERRUPTED state
    # ... call wait_for_tasks with short timeout
    # ... assert it does NOT return immediately (keeps polling)

@pytest.mark.asyncio
async def test_wait_for_tasks_suspends_timeout_during_interrupted():
    """wait_for_tasks must not advance timeout counter while INTERRUPTED."""
    # ... setup subagent INTERRUPTED, call wait_for_tasks with short timeout
    # ... assert it polls beyond the timeout (timeout is suspended)

@pytest.mark.asyncio
async def test_wait_for_tasks_returns_for_idle():
    """wait_for_tasks must return immediately for IDLE status."""
    # ... setup subagent in IDLE state
    # ... assert immediate return

@pytest.mark.asyncio
async def test_wait_for_tasks_no_duplicate_lifecycle_events():
    """wait_for_tasks must NOT emit subagent:lifecycle events (executor is sole emitter)."""
    # ... capture events, call wait_for_tasks
    # ... assert no subagent:lifecycle events emitted by wait_for_tasks

@pytest.mark.asyncio
async def test_wait_for_tasks_cleans_up_terminal_tasks():
    """wait_for_tasks must call cleanup_background_task for terminal tasks after returning."""
    # ... setup COMPLETED subagent
    # ... call wait_for_tasks
    # ... assert agent_registry.get(task_id) is None (cleaned up)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_task_tool_core_logic.py -v -k "polls_through or suspends_timeout or returns_for_idle or no_duplicate or cleans_up"`
Expected: FAIL

- [ ] **Step 3: Rewrite wait_for_tasks**

Replace the entire `wait_for_tasks` function body:

```python
@tool("wait_for_tasks", parse_docstring=True)
async def wait_for_tasks(
    task_ids: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
    runtime,
) -> str:
    """Wait for subagents to complete and collect their results.

    Blocks until each task_id reaches a terminal state (COMPLETED, FAILED,
    CANCELLED, TIMED_OUT) or IDLE. INTERRUPTED tasks are NOT terminal -
    wait_for_tasks keeps polling, suspending the timeout, so resume events
    flow through and the lead model gets the result in the same turn.

    Args:
        task_ids: List of task IDs to wait for (returned by task()).
    """
    from deerflow.subagents.agent_registry import agent_registry
    from deerflow.subagents.executor import SubagentStatus, cleanup_background_task
    from deerflow.subagents.sse_bridge import sse_bridge

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    writer = get_stream_writer()
    if thread_id:
        sse_bridge.register_writer(thread_id, writer)

    try:
        results: dict[str, dict] = {}
        pending = set(task_ids)
        elapsed = 0
        # Dynamic timeout: max(timeout_seconds of awaited) + buffer
        max_timeout = DEFAULT_TIMEOUT_SECONDS
        for tid in task_ids:
            ref = agent_registry.get(tid)
            if ref is not None:
                max_timeout = max(max_timeout, ref.config.timeout_seconds)
        effective_timeout = max_timeout + _TIMEOUT_BUFFER_SECONDS

        while pending and elapsed < effective_timeout:
            done: set[str] = set()
            for tid in list(pending):
                ref = agent_registry.get(tid)
                if ref is None:
                    results[tid] = {"status": "unknown", "error": "task not found in registry"}
                    done.add(tid)
                    continue

                status = ref.status
                if status.is_terminal or status == SubagentStatus.IDLE:
                    result_val = ref.result.result if ref.result else None
                    error_val = ref.result.error if ref.result else None
                    results[tid] = {
                        "status": status.value,
                        "result": result_val,
                        "error": error_val,
                    }
                    done.add(tid)

            pending -= done
            if pending:
                await asyncio.sleep(DEFAULT_POLL_SECONDS)
                # Only advance timeout if at least one task is RUNNING
                # (not all INTERRUPTED - a slow human reply must not trip the timeout)
                if any(
                    (r := agent_registry.get(tid)) and r.status == SubagentStatus.RUNNING
                    for tid in pending
                ):
                    elapsed += DEFAULT_POLL_SECONDS

        # Report timed-out tasks
        for tid in pending:
            results[tid] = {"status": "pending", "error": "wait_for_tasks timed out"}

        # Clean up terminal tasks after returning results
        for tid in results:
            ref = agent_registry.get(tid)
            if ref and ref.result and ref.result.status.is_terminal:
                cleanup_background_task(tid)

        return json.dumps(results, ensure_ascii=False, indent=2)
    finally:
        if thread_id:
            sse_bridge.unregister_writer(thread_id)
```

- [ ] **Step 4: Remove unused import of `event_bus`**

The new implementation no longer imports `event_bus` (executor is sole emitter). Remove the `from deerflow.subagents.event_bus import event_bus` import if present.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_task_tool_core_logic.py -v -k "polls_through or suspends_timeout or returns_for_idle or no_duplicate or cleans_up"`
Expected: PASS

- [ ] **Step 6: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 7: Commit**

```bash
git add backend/packages/harness/deerflow/tools/builtins/wait_for_tasks.py backend/tests/test_task_tool_core_logic.py
git commit -m "refactor: wait_for_tasks polls through INTERRUPTED, suspends timeout, deletes duplicate lifecycle events, cleans up terminal tasks"
```

---

### Task 8: thread_runs.py - Delete `_subagent_resume_event_stream`, POST /resume Returns 202 JSON

**Files:**
- Modify: `app/gateway/routers/thread_runs.py:980-1099`
- Test: `tests/test_thread_runs.py` (or relevant test file)

**Interfaces:**
- Consumes: `resume_background_subagent`, `get_subagent_interrupt`
- Produces: POST /resume returns `JSONResponse(status_code=202)`

- [ ] **Step 1: Write failing test for 202 JSON response**

```python
@pytest.mark.asyncio
async def test_resume_returns_202_json_not_sse(client, mock_interrupted_subagent):
    """POST /resume must return 202 JSON, not an SSE stream."""
    response = await client.post(
        f"/api/threads/test-thread/subagents/test-task/resume",
        json={"resume": "user answer"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["task_id"] == "test-task"
    assert data["status"] == "resumed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_thread_runs.py -v -k "resume_returns_202"`
Expected: FAIL

- [ ] **Step 3: Delete `_subagent_resume_event_stream` function**

Delete lines 985-1055 (the entire `_subagent_resume_event_stream` function).

- [ ] **Step 4: Change POST /resume endpoint to return 202 JSON**

Replace lines 1058-1099:

```python
@router.post("/{thread_id}/subagents/{task_id}/resume")
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def resume_subagent(
    thread_id: str,
    task_id: str,
    body: SubagentResumeRequest,
    request: Request,
) -> JSONResponse:
    """Resume an INTERRUPTED subagent belonging to this thread.

    Fire-and-forget: returns 202 immediately. Resume events flow through
    the EventBus -> SSEBridge -> lead run stream (watched by the frontend).
    """
    interrupt_meta = get_subagent_interrupt(task_id)
    if interrupt_meta is None:
        raise HTTPException(status_code=409, detail=f"Subagent task {task_id} is not interrupted")
    expected_thread_id = f"subagent::{thread_id}::{task_id}"
    if interrupt_meta.get("subagent_thread_id") != expected_thread_id:
        raise HTTPException(status_code=404, detail=f"Subagent task {task_id} not found for thread {thread_id}")

    try:
        resume_background_subagent(task_id, body.resume)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Subagent task {task_id} not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return JSONResponse(status_code=202, content={"task_id": task_id, "status": "resumed"})
```

- [ ] **Step 5: Remove unused imports**

Remove imports for `StreamingResponse`, `format_sse`, and any other imports only used by the deleted `_subagent_resume_event_stream`. Check all references before removing.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_thread_runs.py -v -k "resume"`
Expected: PASS

- [ ] **Step 7: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 8: Commit**

```bash
git add backend/app/gateway/routers/thread_runs.py backend/tests/test_thread_runs.py
git commit -m "refactor: POST /resume returns 202 JSON, delete _subagent_resume_event_stream"
```

---

### Task 9: SubagentContextMiddleware - New File, Absorb Limit + Guard

**Files:**
- Create: `agents/middlewares/subagent_context_middleware.py`
- Delete: `agents/middlewares/subagent_limit_middleware.py`
- Delete: `agents/middlewares/pending_task_guard_middleware.py`
- Modify: `agents/lead_agent/agent.py:427-437`
- Test: Create `tests/test_subagent_context_middleware.py`, Delete `tests/test_subagent_limit_middleware.py`, Delete `tests/test_pending_task_guard_middleware.py`

**Interfaces:**
- Consumes: `agent_registry.list_by_thread`, `ModelRequest.override`, `clone_ai_message_with_tool_calls`, `SubagentStatus`
- Produces: `SubagentContextMiddleware` with `awrap_model_call` (status injection) + `aafter_model` (limit + guard)

- [ ] **Step 1: Write failing tests for SubagentContextMiddleware**

Create `tests/test_subagent_context_middleware.py`:

```python
# Test 1: SubagentStatusMessage.from_refs builds correct content
def test_status_message_from_refs_running():
    """SubagentStatusMessage includes running subagents with task_id, type, description."""

def test_status_message_from_refs_completed():
    """COMPLETED subagents show 'call wait_for_tasks' not result."""

def test_status_message_from_refs_interrupted():
    """INTERRUPTED subagents show 'awaiting user input'."""

def test_status_message_additional_kwargs():
    """Message has hide_from_ui, subagent_status, subagents in additional_kwargs."""

# Test 2: wrap_model_call injection
@pytest.mark.asyncio
async def test_wrap_model_call_no_subagents_passthrough():
    """No subagents -> handler called with original request."""

@pytest.mark.asyncio
async def test_wrap_model_call_with_subagents_injects_status():
    """With subagents -> handler called with augmented request."""

# Test 3: after_model limit enforcement (from SubagentLimitMiddleware tests)
def test_after_model_truncates_excess_task_calls():
    """AIMessage with >max_concurrent task calls -> truncated."""

def test_after_model_no_truncation_when_within_limit():
    """AIMessage with <=max_concurrent task calls -> no change."""

# Test 4: after_model guard enforcement (from PendingTaskGuardMiddleware tests)
def test_after_model_guard_injects_reminder_when_running():
    """AIMessage with no tool_calls + running subagents -> reminder injected."""

def test_after_model_guard_forces_tool_choice_after_3():
    """3rd reminder -> tool_choice forced to wait_for_tasks."""

def test_after_model_guard_no_action_when_tool_calls_present():
    """AIMessage with tool_calls -> no guard."""

def test_after_model_guard_no_action_when_no_subagents():
    """No running subagents -> no guard."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_context_middleware.py -v`
Expected: FAIL - module doesn't exist

- [ ] **Step 3: Create `subagent_context_middleware.py`**

```python
"""Consolidated subagent-awareness middleware for the lead agent.

Injects real-time subagent status into the lead model's context (via
``wrap_model_call``) and enforces concurrent-subagent limits + pending-task
guard (via ``after_model``). Absorbs SubagentLimitMiddleware and
PendingTaskGuardMiddleware.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS, SubagentStatus

_MAX_REMINDERS = 3
MIN_SUBAGENT_LIMIT = 2
MAX_SUBAGENT_LIMIT = 4


def _clamp_subagent_limit(value: int) -> int:
    return max(MIN_SUBAGENT_LIMIT, min(MAX_SUBAGENT_LIMIT, value))


class SubagentStatusMessage(SystemMessage):
    """Lead agent context: real-time subagent status snapshot."""

    @classmethod
    def from_refs(cls, refs: list) -> "SubagentStatusMessage":
        from deerflow.subagents.agent_registry import AgentRef

        running = [r for r in refs if r.status == SubagentStatus.RUNNING]
        interrupted = [r for r in refs if r.status == SubagentStatus.INTERRUPTED]
        completed = [r for r in refs if r.status == SubagentStatus.COMPLETED]
        idle = [r for r in refs if r.status == SubagentStatus.IDLE]
        failed = [r for r in refs if r.status == SubagentStatus.FAILED]

        sections: list[str] = ["<subagent-status>"]

        if running:
            sections.append(f"## Running ({len(running)})")
            for r in running:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): {r.description}")

        if completed:
            sections.append(f"## Completed ({len(completed)})")
            for r in completed:
                sections.append(f'- task {r.task_id} ({r.subagent_type}): Call wait_for_tasks(["{r.task_id}"]) to retrieve result.')

        if interrupted:
            sections.append(f"## Interrupted ({len(interrupted)})")
            for r in interrupted:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): Awaiting user input via POST /resume.")

        if idle:
            sections.append(f"## Idle ({len(idle)})")
            for r in idle:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): Parked. Use follow_up to continue.")

        if failed:
            sections.append(f"## Failed ({len(failed)})")
            for r in failed:
                sections.append(f"- task {r.task_id} ({r.subagent_type}): {r.result.error if r.result else 'unknown error'}")

        sections.append("</subagent-status>")
        content = "\n".join(sections)

        return cls(
            content=content,
            additional_kwargs={
                "hide_from_ui": True,
                "subagent_status": True,
                "subagents": [
                    {"task_id": r.task_id, "status": r.status.value, "subagent_type": r.subagent_type}
                    for r in refs
                ],
            },
        )


class SubagentContextMiddleware(AgentMiddleware[AgentState]):
    """Injects subagent status into lead context and enforces limits + guard.

    Args:
        max_concurrent: Maximum concurrent subagent calls (clamped [2,4]).
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SUBAGENTS) -> None:
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)
        self._count = 0

    @override
    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        thread_id = request.runtime.context.get("thread_id") if request.runtime.context else None
        if not thread_id:
            return await handler(request)

        from deerflow.subagents.agent_registry import agent_registry

        refs = agent_registry.list_by_thread(thread_id)
        if not refs:
            return await handler(request)

        status_msg = SubagentStatusMessage.from_refs(refs)
        augmented = request.override(messages=[*request.messages, status_msg])
        return await handler(augmented)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        # 1. Limit: truncate excess task tool calls (when AIMessage HAS task calls)
        update = self._enforce_limit(last)
        if update is not None:
            return update

        # 2. Guard: prevent premature termination (when AIMessage has NO tool calls)
        return self._enforce_guard(state, runtime, last)

    def _enforce_limit(self, last: AIMessage) -> dict[str, Any] | None:
        tool_calls = getattr(last, "tool_calls", None)
        if not tool_calls:
            return None

        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if len(task_indices) <= self.max_concurrent:
            return None

        indices_to_drop = set(task_indices[self.max_concurrent:])
        truncated = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]
        updated_msg = clone_ai_message_with_tool_calls(last, truncated)
        return {"messages": [updated_msg]}

    def _enforce_guard(self, state: AgentState, runtime: Runtime, last: AIMessage) -> dict[str, Any] | None:
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return None

        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if not thread_id:
            return None

        from deerflow.subagents.agent_registry import agent_registry

        inflight = {SubagentStatus.PENDING, SubagentStatus.RUNNING}
        pending = [r for r in agent_registry.list_by_thread(thread_id) if r.status in inflight]
        if not pending:
            return None

        self._count += 1
        task_ids = [r.task_id for r in pending]
        reminder = f"You have {len(pending)} background subagent task(s) still running (task_ids: {task_ids}). You MUST call wait_for_tasks({task_ids}) to collect their results before finishing your response."
        update: dict[str, Any] = {"messages": [SystemMessage(content=reminder)]}
        if self._count >= _MAX_REMINDERS:
            update["tool_choice"] = {"type": "tool", "name": "wait_for_tasks"}
        return update
```

- [ ] **Step 4: Update wiring in `lead_agent/agent.py`**

Replace lines 427-437:

```python
    subagent_enabled = cfg.get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
        from deerflow.agents.middlewares.subagent_context_middleware import (
            SubagentContextMiddleware,
        )

        middlewares.append(
            SubagentContextMiddleware(max_concurrent=max_concurrent_subagents)
        )
```

Remove the imports of `SubagentLimitMiddleware` and `PendingTaskGuardMiddleware` from the top of the file.

- [ ] **Step 5: Delete old middleware files**

Delete:
- `agents/middlewares/subagent_limit_middleware.py`
- `agents/middlewares/pending_task_guard_middleware.py`
- `tests/test_subagent_limit_middleware.py`
- `tests/test_pending_task_guard_middleware.py`

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_subagent_context_middleware.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/ -v -k "subagent or task_tool or thread_runs"`
Expected: PASS

- [ ] **Step 8: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 9: Commit**

```bash
git add backend/packages/harness/deerflow/agents/middlewares/subagent_context_middleware.py backend/packages/harness/deerflow/agents/lead_agent/agent.py backend/tests/test_subagent_context_middleware.py
git rm backend/packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py backend/packages/harness/deerflow/agents/middlewares/pending_task_guard_middleware.py backend/tests/test_subagent_limit_middleware.py backend/tests/test_pending_task_guard_middleware.py
git commit -m "feat: SubagentContextMiddleware consolidates limit+guard+status injection, deletes SubagentLimitMiddleware and PendingTaskGuardMiddleware"
```

---

### Task 10: Lead Prompt + Documentation Update

**Files:**
- Modify: `agents/lead_agent/prompt.py`
- Modify: `docs/SUBAGENTS.md`
- Test: No tests (documentation only)

**Interfaces:**
- N/A

- [ ] **Step 1: Update lead prompt**

In `agents/lead_agent/prompt.py`, update the subagent instructions section:
- Remove the detached/blocking distinction
- State that `task()` always returns immediately with a task_id
- State that `wait_for_tasks([task_id])` must be called to get the result
- Update examples to show `task()` + `wait_for_tasks()` pattern
- Clarify that if `wait_for_tasks` returns `interrupted` status, the user will be prompted via the frontend and the subagent will be resumed automatically; the lead should keep waiting

- [ ] **Step 2: Update docs/SUBAGENTS.md**

- Remove Route 甲 / Route 乙 distinction
- Describe the unified event flow: task() -> executor emits events -> EventBus -> SSEBridge -> frontend
- Update the interrupt + resume section: POST /resume is fire-and-forget (202), events flow through event bus
- Remove references to `_background_tasks`/`_subagent_executors`
- Document `SubagentContextMiddleware` as the lead agent's subagent awareness mechanism

- [ ] **Step 3: Lint and format**

Run: `cd backend && make lint && make format`

- [ ] **Step 4: Commit**

```bash
git add backend/packages/harness/deerflow/agents/lead_agent/prompt.py backend/docs/SUBAGENTS.md
git commit -m "docs: update lead prompt and SUBAGENTS.md for unified event bus architecture"
```

---

### Task 11: Full Test Suite + Final Verification

**Files:**
- N/A (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && make test`
Expected: ALL PASS

- [ ] **Step 2: Run lint**

Run: `cd backend && make lint`
Expected: No errors

- [ ] **Step 3: Run format check**

Run: `cd backend && make format && git diff --exit-code`
Expected: No changes (already formatted)

- [ ] **Step 4: Run blocking-IO gate**

Run: `cd backend && make test-blocking-io`
Expected: ALL PASS

- [ ] **Step 5: Commit any final fixes**

```bash
git add -A
git commit -m "test: full suite passes after unified subagent event bus refactor"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (Executor sole emitter): Tasks 2, 3, 4
- Section 2 (task_tool eliminate blocking): Task 6
- Section 3 (wait_for_tasks poll through INTERRUPTED): Task 7
- Section 4 (Storage consolidation): Task 1
- Section 5 (Delete _subagent_resume_event_stream): Task 8
- Section 6 (SSEBridge fix): Task 5
- Section 7 (Lead prompt update): Task 10
- Section 8 (Documentation update): Task 10
- Section 9 (SubagentContextMiddleware): Task 9
- Testing plan: Tests written in each task (TDD)

**Placeholder scan:** No TBD/TODO. All code blocks contain actual implementation code. Test code is skeletal but follows existing test patterns - implementer fills in mock setup following existing test patterns in the same files.

**Type consistency:**
- `SubagentStatus.is_terminal` used consistently (COMPLETED/FAILED/CANCELLED/TIMED_OUT)
- `SubagentStatus.IDLE` checked explicitly in wait_for_tasks
- `agent_registry.list_by_thread` returns `list[AgentRef]`
- `AgentRef` fields: task_id, thread_id, status, result, executor, config, description
- `SubagentContextMiddleware.__init__` takes `max_concurrent: int`
- `SubagentStatusMessage.from_refs` takes `list[AgentRef]`

**Ordering safety:** Tasks ordered so each produces a working state:
1. Storage consolidation (foundational, doesn't break event flow)
2-4. Executor events (additive - new events don't break existing consumers)
5. SSEBridge fix (additive - broader check)
6. task_tool (removes poll loop - safe because executor now emits events)
7. wait_for_tasks (changes behavior - safe because executor emits events)
8. thread_runs (depends on wait_for_tasks polling through INTERRUPTED)
9. SubagentContextMiddleware (independent of executor changes)
10. Docs (last)
