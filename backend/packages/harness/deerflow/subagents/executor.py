"""Subagent execution engine."""

import asyncio
import atexit
import logging
import os
import threading
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deerflow.agents.thread_state import SandboxState, ThreadDataState, ThreadState
from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.runtime.serialization import serialize_lc_object
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
from deerflow.skills.types import Skill
from deerflow.subagents.config import SubagentConfig, resolve_subagent_model_name
from deerflow.subagents.token_collector import SubagentTokenCollector
from deerflow.tracing import build_tracing_callbacks, inject_langfuse_metadata

if TYPE_CHECKING:
    # Imported lazily at runtime inside _build_initial_state: importing
    # tool_search eagerly would run tools/builtins/__init__ -> task_tool ->
    # `from deerflow.subagents import SubagentExecutor`, which re-enters this
    # still-initializing package. Type-only here keeps the annotation precise.
    from deerflow.tools.builtins.tool_search import DeferredToolSetup

logger = logging.getLogger(__name__)


_previous_shutdown_isolated_subagent_loop = globals().get("_shutdown_isolated_subagent_loop")
if callable(_previous_shutdown_isolated_subagent_loop):
    atexit.unregister(_previous_shutdown_isolated_subagent_loop)
    _previous_shutdown_isolated_subagent_loop()


class SubagentStatus(Enum):
    """Status of a subagent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    # Paused on ``interrupt()`` awaiting a ``Command(resume=...)``. Not terminal:
    # the task is still resumable and must not be cleaned up.
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            type(self).COMPLETED,
            type(self).FAILED,
            type(self).CANCELLED,
            type(self).TIMED_OUT,
        }

    @property
    def is_stopped(self) -> bool:
        """Terminal or paused — the poll loop should stop waiting on this status.

        ``INTERRUPTED`` is stopped-but-not-terminal: ``task_tool`` returns a
        pause message to the lead agent, but the result/executor stay resident
        for a later ``resume``.
        """
        return self.is_terminal or self is type(self).INTERRUPTED


@dataclass
class SubagentResult:
    """Result of a subagent execution.

    Attributes:
        task_id: Unique identifier for this execution.
        trace_id: Trace ID for distributed tracing (links parent and subagent logs).
        status: Current status of the execution.
        result: The final result message (if completed).
        error: Error message (if failed).
        started_at: When execution started.
        completed_at: When execution completed.
        ai_messages: List of complete AI messages (as dicts) generated during execution.
    """

    task_id: str
    trace_id: str
    status: SubagentStatus
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None
    token_usage_records: list[dict[str, int | str | None]] = field(default_factory=list)
    usage_reported: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Interrupt-resume state. ``interrupts`` holds the serialized ``__interrupt__``
    # payload (list of ``{value, id}`` dicts); ``subagent_thread_id`` is the
    # API-addressable identity the subagent's own checkpointer is keyed by;
    # ``interrupted_at`` records the pause time (``completed_at`` stays None so
    # cleanup heuristics do not treat an interrupted task as finished).
    interrupts: list[dict[str, Any]] | None = None
    subagent_thread_id: str | None = None
    interrupted_at: datetime | None = None
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.ai_messages is None:
            self.ai_messages = []

    def try_set_terminal(
        self,
        status: SubagentStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
        ai_messages: list[dict[str, Any]] | None = None,
        token_usage_records: list[dict[str, int | str | None]] | None = None,
    ) -> bool:
        """Set a terminal status exactly once.

        Background timeout/cancellation and the execution worker can race on the
        same result holder.  The first terminal transition wins; late terminal
        writes must not change status or payload fields.
        """
        if not status.is_terminal:
            raise ValueError(f"Status {status} is not terminal")

        with self._state_lock:
            if self.status.is_terminal:
                return False

            if result is not None:
                self.result = result
            if error is not None:
                self.error = error
            if ai_messages is not None:
                self.ai_messages = ai_messages
            if token_usage_records is not None:
                self.token_usage_records = token_usage_records
            self.completed_at = completed_at or datetime.now()
            self.status = status
            return True

    def try_set_interrupted(
        self,
        *,
        interrupts: list[dict[str, Any]] | None = None,
        subagent_thread_id: str | None = None,
        token_usage_records: list[dict[str, int | str | None]] | None = None,
    ) -> bool:
        """Transition a RUNNING subagent to INTERRUPTED exactly once.

        Records the serialized ``__interrupt__`` payload and the subagent's own
        thread_id so a later resume can address it. ``completed_at`` is left
        None — an interrupted task is not finished and must not be cleaned up.
        A terminal result refuses the transition (the first terminal state wins).
        """
        with self._state_lock:
            if self.status.is_terminal or self.status is SubagentStatus.INTERRUPTED:
                return False
            if self.status is not SubagentStatus.RUNNING:
                return False
            if interrupts is not None:
                self.interrupts = interrupts
            if subagent_thread_id is not None:
                self.subagent_thread_id = subagent_thread_id
            if token_usage_records is not None:
                self.token_usage_records = token_usage_records
            self.interrupted_at = datetime.now()
            self.status = SubagentStatus.INTERRUPTED
            return True

    def try_resume(self) -> bool:
        """Transition an INTERRUPTED subagent back to RUNNING for a resume.

        Clears the pause marker and the cooperative cancel flag so the new
        ``astream(Command(resume=...))`` run starts clean. Only valid from
        INTERRUPTED; any other status refuses.
        """
        with self._state_lock:
            if self.status is not SubagentStatus.INTERRUPTED:
                return False
            self.interrupted_at = None
            self.interrupts = None
            self.status = SubagentStatus.RUNNING
            self.cancel_event.clear()
            return True


# Global storage for background task results
_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()

# Executors keyed by task_id, kept resident so an INTERRUPTED subagent can be
# resumed later via ``resume_background_subagent``. Removed only when the task
# reaches a truly terminal state (see ``cleanup_background_task``). Guarded by
# ``_background_tasks_lock`` to stay in sync with the result registry.
_subagent_executors: dict[str, "SubagentExecutor"] = {}

# Thread pool for background task scheduling and orchestration
_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-scheduler-")

# Persistent event loop for isolated subagent executions triggered from an
# already-running parent loop. Reusing one long-lived loop avoids creating a
# fresh loop per execution and then closing async resources bound to it.
_isolated_subagent_loop: asyncio.AbstractEventLoop | None = None
_isolated_subagent_loop_thread: threading.Thread | None = None
_isolated_subagent_loop_started: threading.Event | None = None
_isolated_subagent_loop_lock = threading.Lock()


def _run_isolated_subagent_loop(
    loop: asyncio.AbstractEventLoop,
    started_event: threading.Event,
) -> None:
    """Run the persistent isolated subagent loop in a dedicated daemon thread."""
    asyncio.set_event_loop(loop)
    loop.call_soon(started_event.set)
    try:
        loop.run_forever()
    finally:
        started_event.clear()


def _shutdown_isolated_subagent_loop() -> None:
    """Stop and close the persistent isolated subagent loop."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started

    with _isolated_subagent_loop_lock:
        loop = _isolated_subagent_loop
        thread = _isolated_subagent_loop_thread
        _isolated_subagent_loop = None
        _isolated_subagent_loop_thread = None
        _isolated_subagent_loop_started = None

    if loop is None:
        return

    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)

    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)

    thread_stopped = thread is None or not thread.is_alive()
    loop_stopped = not loop.is_running()

    if not loop.is_closed():
        if thread_stopped and loop_stopped:
            loop.close()
        else:
            logger.warning(
                "Skipping close of isolated subagent loop because shutdown did not complete within timeout (thread_alive=%s, loop_running=%s)",
                thread is not None and thread.is_alive(),
                loop.is_running(),
            )


atexit.register(_shutdown_isolated_subagent_loop)


def _get_isolated_subagent_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent event loop used by isolated subagent executions."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started
    with _isolated_subagent_loop_lock:
        thread_is_alive = _isolated_subagent_loop_thread is not None and _isolated_subagent_loop_thread.is_alive()
        loop_is_usable = _isolated_subagent_loop is not None and not _isolated_subagent_loop.is_closed() and _isolated_subagent_loop.is_running() and thread_is_alive

        if not loop_is_usable:
            loop = asyncio.new_event_loop()
            started_event = threading.Event()
            thread = threading.Thread(
                target=_run_isolated_subagent_loop,
                args=(loop, started_event),
                name="subagent-persistent-loop",
                daemon=True,
            )
            thread.start()
            if not started_event.wait(timeout=5):
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1)
                loop.close()
                raise RuntimeError("Timed out starting isolated subagent event loop")
            _isolated_subagent_loop = loop
            _isolated_subagent_loop_thread = thread
            _isolated_subagent_loop_started = started_event

        if _isolated_subagent_loop is None:
            raise RuntimeError("Isolated subagent event loop is not initialized")
        return _isolated_subagent_loop


def _submit_to_isolated_loop_in_context(
    context: Context,
    coro_factory: Callable[[], Coroutine[Any, Any, SubagentResult]],
) -> Future[SubagentResult]:
    """Submit a coroutine to the isolated loop while preserving ContextVar state."""
    return context.run(
        lambda: asyncio.run_coroutine_threadsafe(
            coro_factory(),
            _get_isolated_subagent_loop(),
        )
    )


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """Filter tools based on subagent configuration.

    Args:
        all_tools: List of all available tools.
        allowed: Optional allowlist of tool names. If provided, only these tools are included.
        disallowed: Optional denylist of tool names. These tools are always excluded.

    Returns:
        Filtered list of tools.
    """
    filtered = all_tools

    # Apply allowlist if specified
    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]

    # Apply denylist
    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    return filtered


class SubagentExecutor:
    """Executor for running subagents."""

    def __init__(
        self,
        config: SubagentConfig,
        tools: list[BaseTool],
        app_config: AppConfig | None = None,
        parent_model: str | None = None,
        sandbox_state: SandboxState | None = None,
        thread_data: ThreadDataState | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        oauth_provider: str | None = None,
        oauth_id: str | None = None,
        run_id: str | None = None,
        clarification_interrupt_enabled: bool | None = None,
        task_id: str | None = None,
        checkpointer: Any | None = None,
    ):
        """Initialize the executor.

        Args:
            config: Subagent configuration.
            tools: List of all available tools (will be filtered).
            app_config: Resolved AppConfig. When None, ``_create_agent`` falls
                back to ``get_app_config()`` (matches the lead-agent factory's
                pattern).
            parent_model: The parent agent's model name for inheritance.
            sandbox_state: Sandbox state from parent agent.
            thread_data: Thread data from parent agent.
            thread_id: Thread ID for sandbox operations.
            trace_id: Trace ID from parent for distributed tracing.
            user_id: User ID captured from the parent tool's runtime context.
                When None, the tracing layer falls back to DEFAULT_USER_ID.
            user_role: Authenticated user's role, propagated so GuardrailMiddleware
                on the subagent can apply role-aware policy to delegated calls.
            oauth_provider: External identity provider, when authenticated via SSO.
            oauth_id: Subject id at the external identity provider.
            run_id: Parent run id, so delegated guardrail decisions attribute to
                the same run as the lead agent.
            task_id: The dispatching tool_call_id. Used to derive a deterministic,
                API-addressable ``subagent_thread_id`` so an interrupted subagent
                can be resumed by ``task_id``.
            checkpointer: Optional checkpointer for the subagent graph. Defaults
                to a fresh ``InMemorySaver`` owned by this executor — never the
                parent/Gateway checkpointer (cross-loop async savers break on the
                isolated subagent loop). The saver is what makes
                ``interrupt()``/``Command(resume=...)`` work.
        """
        self.config = config
        self.app_config = app_config
        self.parent_model = parent_model
        # Resolve eagerly only when it does not require loading config.yaml; otherwise defer
        # to _create_agent (which already loads app_config) so unit tests can construct
        # executors without a config file present.
        if config.model != "inherit" or parent_model is not None or app_config is not None:
            self.model_name: str | None = resolve_subagent_model_name(config, parent_model, app_config=app_config)
        else:
            self.model_name = None
        self.sandbox_state = sandbox_state
        self.thread_data = thread_data
        self.thread_id = thread_id
        # Generate trace_id if not provided (for top-level calls)
        self.trace_id = trace_id or str(uuid.uuid4())[:8]
        self.user_id = user_id
        # Guardrail attribution propagated from the parent runtime context.
        self.user_role = user_role
        self.oauth_provider = oauth_provider
        self.oauth_id = oauth_id
        self.run_id = run_id
        # Whether structured clarification interrupts are enabled for this run.
        # Mirrors the parent (lead) runtime's ``clarification_interrupt_enabled``
        # flag so ClarificationMiddleware on the subagent takes the structured
        # interrupt path (web) or the free goto=END path (IM) consistently with
        # the lead agent. Without this the subagent always falls back to free.
        self.clarification_interrupt_enabled = clarification_interrupt_enabled

        # Independent thread identity for the subagent's own checkpointer. The
        # parent thread_id stays in effect for sandbox/file isolation (context);
        # this one keys the subagent checkpoint so interrupt/resume state is
        # isolated from the parent thread's checkpoint.
        if thread_id and task_id:
            self.subagent_thread_id = f"subagent::{thread_id}::{task_id}"
        else:
            self.subagent_thread_id = f"subagent::{uuid.uuid4()}"
        # The subagent owns its own async-capable, loop-safe checkpointer. It must
        # NOT inherit the parent run's checkpointer (sync savers raise on async
        # methods) nor the Gateway's main-loop-bound async saver (breaks on the
        # isolated subagent loop). InMemorySaver implements both sync + async APIs.
        self._checkpointer: Any = checkpointer if checkpointer is not None else InMemorySaver()
        # Compiled agent is cached so a resume reuses the exact same graph +
        # checkpointer instance against the existing checkpoint.
        self._agent: Any | None = None

        self._base_tools = _filter_tools(
            tools,
            config.tools,
            config.disallowed_tools,
        )
        self.tools = self._base_tools

        logger.info(f"[trace={self.trace_id}] SubagentExecutor initialized: {config.name} with {len(self.tools)} tools")

    def _create_agent(
        self,
        tools: list[BaseTool] | None = None,
        *,
        deferred_setup: "DeferredToolSetup | None" = None,
    ):
        """Create the agent instance.

        ``deferred_setup`` (assembled in ``_build_initial_state``) carries the
        deferred MCP tool names + catalog hash so the subagent gets the same
        DeferredToolFilterMiddleware the lead agent has. ``None`` is a no-op.
        """
        app_config = self.app_config or get_app_config()
        if self.model_name is None:
            self.model_name = resolve_subagent_model_name(self.config, self.parent_model, app_config=app_config)
        model = create_chat_model(
            name=self.model_name,
            thinking_enabled=False,
            app_config=app_config,
            attach_tracing=False,
        )

        from deerflow.agents.middlewares.tool_error_handling_middleware import (
            build_subagent_runtime_middlewares,
        )

        # Reuse shared middleware composition with lead agent.
        middlewares = build_subagent_runtime_middlewares(
            app_config=app_config,
            model_name=self.model_name,
            lazy_init=True,
            deferred_setup=deferred_setup,
        )

        # system_prompt is included in initial state messages (see _build_initial_state)
        # to avoid multiple SystemMessages which some LLM APIs don't support.
        return create_agent(
            model=model,
            tools=tools if tools is not None else self.tools,
            middleware=middlewares,
            system_prompt=None,
            state_schema=ThreadState,
            checkpointer=self._checkpointer,
        )

    async def _load_skills(self) -> list[Skill]:
        """Load enabled skill metadata based on config.skills."""
        if self.config.skills is not None and len(self.config.skills) == 0:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} skills=[] — skipping skill loading")
            return []

        try:
            from deerflow.skills.storage import get_or_new_skill_storage

            storage_kwargs = {"app_config": self.app_config} if self.app_config is not None else {}
            storage = await asyncio.to_thread(get_or_new_skill_storage, **storage_kwargs)
            # Use asyncio.to_thread to avoid blocking the event loop (LangGraph ASGI requirement)
            all_skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded {len(all_skills)} enabled skills from disk")
        except Exception:
            logger.exception(f"[trace={self.trace_id}] Failed to load skills for subagent {self.config.name}")
            raise

        if not all_skills:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} no enabled skills found")
            return []

        # Filter by config.skills whitelist
        if self.config.skills is not None:
            allowed = set(self.config.skills)
            return [s for s in all_skills if s.name in allowed]
        return all_skills

    def _apply_skill_allowed_tools(self, skills: list[Skill]) -> list[BaseTool]:
        return filter_tools_by_skill_allowed_tools(self._base_tools, skills)

    async def _load_skill_messages(self, skills: list[Skill]) -> list[SystemMessage]:
        """Load skill content as conversation items based on config.skills.

        Aligned with Codex's pattern: each subagent loads its own skills
        per-session and injects them as conversation items (developer messages),
        not as system prompt text. The config.skills whitelist controls which
        skills are loaded:
        - None: load all enabled skills
        - []: no skills
        - ["skill-a", "skill-b"]: only these skills

        Returns:
            List of SystemMessages containing skill content.
        """
        if not skills:
            return []

        # Read each skill's SKILL.md content and create conversation items
        messages = []
        for skill in skills:
            try:
                content = await asyncio.to_thread(skill.skill_file.read_text, encoding="utf-8")
                content = content.strip()
                if content:
                    messages.append(SystemMessage(content=f'<skill name="{skill.name}">\n{content}\n</skill>'))
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded skill: {skill.name}")
            except Exception:
                logger.debug(
                    f"[trace={self.trace_id}] Failed to read skill {skill.name}",
                    exc_info=True,
                )

        return messages

    async def _build_initial_state(self, task: str) -> tuple[dict[str, Any], list[BaseTool], "DeferredToolSetup"]:
        """Build the initial state for agent execution.

        Args:
            task: The task description.

        Returns:
            ``(state, final_tools, deferred_setup)``. ``final_tools`` is the
            policy-filtered tool list with the ``tool_search`` tool appended when
            deferral applies; ``deferred_setup`` is consumed by ``_create_agent``
            so the agent build and the injected ``<available-deferred-tools>``
            section share one catalog/hash.
        """
        # Lazy import: see the TYPE_CHECKING note at the top of this module -
        # importing tool_search runs tools/builtins/__init__, which would
        # re-enter this package during its own initialization.
        from deerflow.tools.builtins.tool_search import (
            assemble_deferred_tools,
            get_deferred_tools_prompt_section,
        )

        # Load skills as conversation items (Codex pattern)
        skills = await self._load_skills()
        filtered_tools = self._apply_skill_allowed_tools(skills)
        # Assemble deferred tool_search AFTER policy filtering (fail-closed),
        # mirroring the lead path so subagents stop binding full MCP schemas.
        # The generated tool_search helper is intentionally not subject to the
        # subagent's name-level allow/deny (config.tools / disallowed_tools):
        # its catalog is built from the already-filtered list, so it can never
        # surface a tool the policy denied. This matches the lead agent.
        enabled = (self.app_config or get_app_config()).tool_search.enabled
        final_tools, deferred_setup = assemble_deferred_tools(filtered_tools, enabled=enabled)
        skill_messages = await self._load_skill_messages(skills)

        # Combine system_prompt and skills into a single SystemMessage.
        # Some LLM APIs reject multiple SystemMessages with
        # "System message must be at the beginning."
        system_parts: list[str] = []
        if self.config.system_prompt:
            system_parts.append(self.config.system_prompt)
        for skill_msg in skill_messages:
            system_parts.append(skill_msg.content)
        # Name the deferred MCP tools in the prompt; their schemas stay withheld
        # until tool_search promotes them. Empty set -> "" -> appends nothing.
        deferred_section = get_deferred_tools_prompt_section(deferred_names=deferred_setup.deferred_names)
        if deferred_section:
            system_parts.append(deferred_section)

        messages: list[Any] = []
        if system_parts:
            messages.append(SystemMessage(content="\n\n".join(system_parts)))

        # Then the actual task
        messages.append(HumanMessage(content=task))

        state: dict[str, Any] = {
            "messages": messages,
        }

        # Pass through sandbox and thread data from parent
        if self.sandbox_state is not None:
            state["sandbox"] = self.sandbox_state
        if self.thread_data is not None:
            state["thread_data"] = self.thread_data

        return state, final_tools, deferred_setup

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task asynchronously.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        if result_holder is not None:
            # Use the provided result holder (for async execution with real-time updates)
            result = result_holder
        else:
            # Create a new result for synchronous execution
            task_id = str(uuid.uuid4())[:8]
            result = SubagentResult(
                task_id=task_id,
                trace_id=self.trace_id,
                status=SubagentStatus.RUNNING,
                started_at=datetime.now(),
            )
        ai_messages = result.ai_messages
        if ai_messages is None:
            ai_messages = []
            result.ai_messages = ai_messages
        # O(1) duplicate detection for streamed AI messages. ``stream_mode="values"``
        # re-yields the full state every super-step, so the same trailing message is
        # re-examined on each chunk; an id-keyed set keeps that check O(1) instead of
        # rescanning the append-only ``ai_messages`` list (O(n) per chunk -> O(n^2)
        # over a run, which reaches max_turns=150 for deep-research subagents).
        seen_message_ids: set[str] = {mid for msg in ai_messages if (mid := msg.get("id"))}

        collector: SubagentTokenCollector | None = None
        try:
            state, final_tools, deferred_setup = await self._build_initial_state(task)
            agent = self._create_agent(final_tools, deferred_setup=deferred_setup)
            # Cache the compiled agent so a later resume reuses the exact same
            # graph + checkpointer instance against the existing checkpoint.
            self._agent = agent

            # Token collector for subagent LLM calls
            collector_caller = f"subagent:{self.config.name}"
            collector = SubagentTokenCollector(caller=collector_caller)

            # Build config with thread_id for sandbox access and recursion limit
            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
                "callbacks": [collector],
                "tags": [collector_caller],
            }

            # Inject tracing callbacks at the graph level so a single subagent run
            # produces one trace with all node / LLM / tool calls as child spans.
            # This mirrors the lead agent pattern: graph-level tracing paired with
            # attach_tracing=False on the model avoids double-counted traces.
            tracing_callbacks = build_tracing_callbacks()
            if tracing_callbacks:
                existing_callbacks = list(run_config.get("callbacks") or [])
                run_config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

            # Normalize subagent name for tracing so it matches the lead-agent
            # naming shape (lowercase, hyphens only). Inline because there is no
            # shared helper — runtime/runs/naming.py only handles lead-agent runs.
            if self.config.name:
                normalized_name = self.config.name.strip().lower().replace("_", "-")
                assistant_id = f"subagent:{normalized_name}"
            else:
                assistant_id = "subagent"

            # Inject Langfuse trace-attribute metadata so the subagent trace
            # links to the parent thread and carries the correct session/user IDs.
            inject_langfuse_metadata(
                run_config,
                thread_id=self.thread_id,
                user_id=self.user_id,
                assistant_id=assistant_id,
                model_name=self.model_name,
                environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
            )

            context: dict[str, Any] = {}
            # Split thread identity: the checkpointer (configurable.thread_id)
            # keys the subagent's OWN checkpoint so interrupt/resume state is
            # isolated from the parent thread; the runtime context keeps the
            # PARENT thread_id so sandbox/file tools keep writing to the parent's
            # workspace.
            run_config["configurable"] = {"thread_id": self.subagent_thread_id}
            if self.thread_id:
                context["thread_id"] = self.thread_id
            if self.app_config is not None:
                context["app_config"] = self.app_config
            # Propagate guardrail attribution so delegated tool calls are
            # evaluated with the parent run's identity (role-aware policy,
            # audit). user_id reuses the resolved tracing id; on every
            # authenticated/IM path this equals the parent context value.
            context["user_id"] = self.user_id
            context["user_role"] = self.user_role
            context["oauth_provider"] = self.oauth_provider
            context["oauth_id"] = self.oauth_id
            context["run_id"] = self.run_id
            context["is_subagent"] = True
            # Forward the structured-clarification gate so the subagent's
            # ClarificationMiddleware takes the same path (interrupt vs free)
            # as the lead agent. Set only when truthy: IM/absent stays unset
            # and the middleware falls back to the free goto=END path.
            if self.clarification_interrupt_enabled:
                context["clarification_interrupt_enabled"] = self.clarification_interrupt_enabled

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution with max_turns={self.config.max_turns}")

            # Use stream instead of invoke to get real-time updates
            # This allows us to collect AI messages as they are generated
            final_state = None
            # Captured ``__interrupt__`` payload (if the subagent called
            # ``interrupt()``). With ``stream_mode="values"`` LangGraph yields a
            # final chunk carrying ``__interrupt__`` then ends the stream.
            interrupts: Any = None

            # Pre-check: bail out immediately if already cancelled before streaming starts
            if result.cancel_event.is_set():
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled before streaming")
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            async for chunk in agent.astream(state, config=run_config, context=context, stream_mode="values"):  # type: ignore[arg-type]
                # Cooperative cancellation: check if parent requested stop.
                # Note: cancellation is only detected at astream iteration boundaries,
                # so long-running tool calls within a single iteration will not be
                # interrupted until the next chunk is yielded.
                if result.cancel_event.is_set():
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled by parent")
                    result.try_set_terminal(
                        SubagentStatus.CANCELLED,
                        error="Cancelled by user",
                        token_usage_records=collector.snapshot_records(),
                    )
                    return result

                final_state = chunk

                # Detect an interrupt: LangGraph surfaces ``__interrupt__`` (a
                # tuple of Interrupt objects) on the values chunk that pauses
                # execution. Capture it; the stream will end right after.
                if isinstance(chunk, dict):
                    chunk_interrupts = chunk.get("__interrupt__")
                    if chunk_interrupts:
                        interrupts = chunk_interrupts

                # Extract AI messages from the current state
                messages = chunk.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    # Check if this is a new AI message
                    if isinstance(last_message, AIMessage):
                        # Convert message to dict for serialization
                        message_dict = last_message.model_dump()
                        # Only add if it's not already in the list (avoid duplicates)
                        # Check by comparing message IDs if available, otherwise compare full dict
                        message_id = message_dict.get("id")
                        if message_id:
                            is_duplicate = message_id in seen_message_ids
                        else:
                            # id-less messages can't be keyed; fall back to a full-dict compare
                            is_duplicate = message_dict in ai_messages

                        if not is_duplicate:
                            ai_messages.append(message_dict)
                            if message_id:
                                seen_message_ids.add(message_id)
                            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} captured AI message #{len(ai_messages)}")

            # Stream ended. Determine why: cancel takes precedence over an
            # interrupt (an explicit user stop wins over a pause), then an
            # interrupt pauses for human input, otherwise the run completed.
            if result.cancel_event.is_set():
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled after stream end")
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            if interrupts:
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} interrupted awaiting human input")
                result.try_set_interrupted(
                    interrupts=serialize_lc_object(interrupts),
                    subagent_thread_id=self.subagent_thread_id,
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed async execution")
            token_usage_records = collector.snapshot_records()
            final_result = self._extract_final_result(final_state)

            result.try_set_terminal(
                SubagentStatus.COMPLETED,
                result=final_result,
                token_usage_records=token_usage_records,
            )

        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
            result.try_set_terminal(
                SubagentStatus.FAILED,
                error=str(e),
                token_usage_records=(collector.snapshot_records() if collector is not None else None),
            )

        return result

    def _execute_in_isolated_loop(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute the subagent on the persistent isolated event loop.

        This method is used by the sync ``execute()`` path when the caller is
        already running inside an event loop. Because ``execute()`` is a sync
        API, this path blocks the caller while the actual coroutine runs on the
        long-lived isolated loop. Reusing that loop keeps shared async clients
        from being tied to a short-lived loop that gets closed per execution.
        """
        future: Future[SubagentResult] | None = None
        parent_context = copy_context()
        try:
            future = _submit_to_isolated_loop_in_context(
                parent_context,
                lambda: self._aexecute(task, result_holder),
            )
            return future.result(timeout=self.config.timeout_seconds)
        except FuturesTimeoutError:
            if result_holder is not None:
                result_holder.cancel_event.set()
            if future is not None:
                future.cancel()
            raise
        except Exception:
            if future is None:
                logger.debug(
                    f"[trace={self.trace_id}] Failed to submit subagent {self.config.name} to the isolated event loop",
                    exc_info=True,
                )
            else:
                logger.debug(
                    f"[trace={self.trace_id}] Subagent {self.config.name} failed while executing on the isolated event loop",
                    exc_info=True,
                )
            raise

    def execute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task synchronously (wrapper around async execution).

        This method runs the async execution in a new event loop, allowing
        asynchronous tools (like MCP tools) to be used within the thread pool.

        When called from within an already-running event loop (e.g., when the
        parent agent is async), this method synchronously waits on the
        persistent isolated loop to avoid event loop conflicts with shared
        async primitives like httpx clients.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                logger.debug(f"[trace={self.trace_id}] Subagent {self.config.name} detected running event loop, using isolated loop")
                return self._execute_in_isolated_loop(task, result_holder)

            # Standard path: no running event loop, use asyncio.run
            return asyncio.run(self._aexecute(task, result_holder))
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} execution failed")
            # Create a result with error if we don't have one
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.RUNNING,
                )
            result.try_set_terminal(SubagentStatus.FAILED, error=str(e))
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """Start a task execution in the background.

        Args:
            task: The task description for the subagent.
            task_id: Optional task ID to use. If not provided, a random UUID will be generated.

        Returns:
            Task ID that can be used to check status later.
        """
        # Use provided task_id or generate a new one
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]

        # Create initial pending result
        result = SubagentResult(
            task_id=task_id,
            trace_id=self.trace_id,
            status=SubagentStatus.PENDING,
        )

        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution, task_id={task_id}, timeout={self.config.timeout_seconds}s")

        with _background_tasks_lock:
            _background_tasks[task_id] = result
            # Keep the executor resident so an INTERRUPTED subagent can be resumed
            # in-place (same agent + checkpointer + subagent_thread_id).
            _subagent_executors[task_id] = self

        parent_context = copy_context()

        # Submit to scheduler pool
        def run_task():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING
                _background_tasks[task_id].started_at = datetime.now()
                result_holder = _background_tasks[task_id]

            try:
                # Submit execution directly to the persistent isolated loop so the
                # background path does not create a temporary loop via execute().
                execution_future = _submit_to_isolated_loop_in_context(
                    parent_context,
                    lambda: self._aexecute(task, result_holder),
                )
                try:
                    # Wait for execution with timeout
                    execution_future.result(timeout=self.config.timeout_seconds)
                except FuturesTimeoutError:
                    logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} execution timed out after {self.config.timeout_seconds}s")
                    # Signal cooperative cancellation and cancel the future
                    result_holder.cancel_event.set()
                    result_holder.try_set_terminal(
                        SubagentStatus.TIMED_OUT,
                        error=f"Execution timed out after {self.config.timeout_seconds} seconds",
                    )
                    execution_future.cancel()
            except Exception as e:
                logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
                with _background_tasks_lock:
                    task_result = _background_tasks[task_id]
                task_result.try_set_terminal(SubagentStatus.FAILED, error=str(e))

        _scheduler_pool.submit(run_task)
        return task_id

    async def _aresume(self, resume_value: Any, result_holder: SubagentResult) -> SubagentResult:
        """Resume an INTERRUPTED subagent with a ``Command(resume=...)`` value.

        Reuses the cached compiled agent (and thus its checkpointer) and the
        same ``subagent_thread_id`` so LangGraph resumes from the paused
        checkpoint. The astream loop and interrupt/cancel/completion detection
        mirror ``_aexecute`` — a resumed run may itself interrupt again.
        """
        result = result_holder
        ai_messages = result.ai_messages if result.ai_messages is not None else []
        result.ai_messages = ai_messages
        seen_message_ids: set[str] = {mid for msg in ai_messages if (mid := msg.get("id"))}

        collector: SubagentTokenCollector | None = None
        try:
            agent = self._agent
            if agent is None:
                raise RuntimeError("Cannot resume: subagent has no cached compiled agent (was it interrupted?)")

            collector_caller = f"subagent:{self.config.name}"
            collector = SubagentTokenCollector(caller=collector_caller)

            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
                "callbacks": [collector],
                "tags": [collector_caller],
                "configurable": {"thread_id": self.subagent_thread_id},
            }
            tracing_callbacks = build_tracing_callbacks()
            if tracing_callbacks:
                existing_callbacks = list(run_config.get("callbacks") or [])
                run_config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

            if self.config.name:
                normalized_name = self.config.name.strip().lower().replace("_", "-")
                assistant_id = f"subagent:{normalized_name}"
            else:
                assistant_id = "subagent"
            inject_langfuse_metadata(
                run_config,
                thread_id=self.thread_id,
                user_id=self.user_id,
                assistant_id=assistant_id,
                model_name=self.model_name,
                environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
            )

            context: dict[str, Any] = {}
            if self.thread_id:
                context["thread_id"] = self.thread_id
            if self.app_config is not None:
                context["app_config"] = self.app_config
            context["user_id"] = self.user_id
            context["user_role"] = self.user_role
            context["oauth_provider"] = self.oauth_provider
            context["oauth_id"] = self.oauth_id
            context["run_id"] = self.run_id
            context["is_subagent"] = True
            if self.clarification_interrupt_enabled:
                context["clarification_interrupt_enabled"] = self.clarification_interrupt_enabled

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} resuming after interrupt")

            # Resume input: Command(resume=...) tells LangGraph to resume from
            # the paused checkpoint, feeding ``resume_value`` back to ``interrupt()``.
            graph_input = Command(resume=resume_value)

            final_state = None
            interrupts: Any = None

            if result.cancel_event.is_set():
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            async for chunk in agent.astream(graph_input, config=run_config, context=context, stream_mode="values"):  # type: ignore[arg-type]
                if result.cancel_event.is_set():
                    result.try_set_terminal(
                        SubagentStatus.CANCELLED,
                        error="Cancelled by user",
                        token_usage_records=collector.snapshot_records(),
                    )
                    return result

                final_state = chunk
                if isinstance(chunk, dict):
                    chunk_interrupts = chunk.get("__interrupt__")
                    if chunk_interrupts:
                        interrupts = chunk_interrupts

                messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
                if messages:
                    last_message = messages[-1]
                    if isinstance(last_message, AIMessage):
                        message_dict = last_message.model_dump()
                        message_id = message_dict.get("id")
                        if message_id:
                            is_duplicate = message_id in seen_message_ids
                        else:
                            is_duplicate = message_dict in ai_messages
                        if not is_duplicate:
                            ai_messages.append(message_dict)
                            if message_id:
                                seen_message_ids.add(message_id)

            if result.cancel_event.is_set():
                result.try_set_terminal(
                    SubagentStatus.CANCELLED,
                    error="Cancelled by user",
                    token_usage_records=collector.snapshot_records(),
                )
                return result
            if interrupts:
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} re-interrupted after resume")
                result.try_set_interrupted(
                    interrupts=serialize_lc_object(interrupts),
                    subagent_thread_id=self.subagent_thread_id,
                    token_usage_records=collector.snapshot_records(),
                )
                return result

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed after resume")
            token_usage_records = collector.snapshot_records()
            final_result = self._extract_final_result(final_state)
            result.try_set_terminal(
                SubagentStatus.COMPLETED,
                result=final_result,
                token_usage_records=token_usage_records,
            )

        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} resume failed")
            result.try_set_terminal(
                SubagentStatus.FAILED,
                error=str(e),
                token_usage_records=(collector.snapshot_records() if collector is not None else None),
            )

        return result

    def _extract_final_result(self, final_state: Any) -> str:
        """Extract the final result string from the subagent's final state.

        Pulled out of ``_aexecute`` so the resume path reuses the same logic.
        """
        if final_state is None:
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no final state")
            return "No response generated"

        messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} final messages count: {len(messages)}")

        last_ai_message = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_message = msg
                break

        if last_ai_message is not None:
            content = last_ai_message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts: list[str] = []
                pending_str_parts: list[str] = []
                for block in content:
                    if isinstance(block, str):
                        pending_str_parts.append(block)
                    elif isinstance(block, dict):
                        if pending_str_parts:
                            text_parts.append("".join(pending_str_parts))
                            pending_str_parts.clear()
                        text_val = block.get("text")
                        if isinstance(text_val, str):
                            text_parts.append(text_val)
                if pending_str_parts:
                    text_parts.append("".join(pending_str_parts))
                return "\n".join(text_parts) if text_parts else "No text content in response"
            return str(content)

        if messages:
            last_message = messages[-1]
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no AIMessage found, using last message: {type(last_message)}")
            raw_content = last_message.content if hasattr(last_message, "content") else str(last_message)
            if isinstance(raw_content, str):
                return raw_content
            if isinstance(raw_content, list):
                parts: list[str] = []
                pending: list[str] = []
                for block in raw_content:
                    if isinstance(block, str):
                        pending.append(block)
                    elif isinstance(block, dict):
                        if pending:
                            parts.append("".join(pending))
                            pending.clear()
                        text_val = block.get("text")
                        if isinstance(text_val, str):
                            parts.append(text_val)
                if pending:
                    parts.append("".join(pending))
                return "\n".join(parts) if parts else "No text content in response"
            return str(raw_content)

        logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no messages in final state")
        return "No response generated"

    def resume_async(self, resume_value: Any, task_id: str) -> str:
        """Resume an INTERRUPTED background subagent.

        Mirrors ``execute_async``: transitions the result INTERRUPTED→RUNNING,
        submits ``_aresume`` to the persistent isolated loop, and returns the
        ``task_id`` so the caller can poll the same ``SubagentResult`` for the
        resumed run's progress.

        Args:
            resume_value: The value to feed back to ``interrupt()`` via
                ``Command(resume=...)``.
            task_id: The task_id of the interrupted subagent.

        Returns:
            The same ``task_id``.
        """
        with _background_tasks_lock:
            result_holder = _background_tasks.get(task_id)
            if result_holder is None:
                raise KeyError(f"Unknown subagent task {task_id}")
            if not result_holder.try_resume():
                raise RuntimeError(f"Subagent task {task_id} is not interrupted (status={result_holder.status.value}); cannot resume")
            result_holder.started_at = datetime.now()

        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} resuming, task_id={task_id}, timeout={self.config.timeout_seconds}s")

        parent_context = copy_context()

        def run_task():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING

            try:
                execution_future = _submit_to_isolated_loop_in_context(
                    parent_context,
                    lambda: self._aresume(resume_value, _background_tasks[task_id]),
                )
                try:
                    execution_future.result(timeout=self.config.timeout_seconds)
                except FuturesTimeoutError:
                    logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} resume timed out after {self.config.timeout_seconds}s")
                    with _background_tasks_lock:
                        holder = _background_tasks[task_id]
                    holder.cancel_event.set()
                    holder.try_set_terminal(
                        SubagentStatus.TIMED_OUT,
                        error=f"Resume timed out after {self.config.timeout_seconds} seconds",
                    )
                    execution_future.cancel()
            except Exception as e:
                logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} resume failed")
                with _background_tasks_lock:
                    _background_tasks[task_id].try_set_terminal(SubagentStatus.FAILED, error=str(e))

        _scheduler_pool.submit(run_task)
        return task_id


MAX_CONCURRENT_SUBAGENTS = 3


def request_cancel_background_task(task_id: str) -> None:
    """Signal a running background task to stop.

    Sets the cancel_event on the task, which is checked cooperatively
    by ``_aexecute`` during ``agent.astream()`` iteration.  This allows
    subagent threads — which cannot be force-killed via ``Future.cancel()``
    — to stop at the next iteration boundary.

    Args:
        task_id: The task ID to cancel.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is not None:
            result.cancel_event.set()
            logger.info("Requested cancellation for background task %s", task_id)


def get_background_task_result(task_id: str) -> SubagentResult | None:
    """Get the result of a background task.

    Args:
        task_id: The task ID returned by execute_async.

    Returns:
        SubagentResult if found, None otherwise.
    """
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def list_background_tasks() -> list[SubagentResult]:
    """List all background tasks.

    Returns:
        List of all SubagentResult instances.
    """
    with _background_tasks_lock:
        return list(_background_tasks.values())


def cleanup_background_task(task_id: str) -> None:
    """Remove a completed task from background tasks.

    Should be called by task_tool after it finishes polling and returns the result.
    This prevents memory leaks from accumulated completed tasks.

    Only removes tasks that are in a truly terminal state
    (COMPLETED/FAILED/CANCELLED/TIMED_OUT) to avoid race conditions with the
    background executor still updating the task entry, and to keep
    INTERRUPTED subagents resident for a later resume.

    Args:
        task_id: The task ID to remove.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            # Nothing to clean up; may have been removed already.
            logger.debug("Requested cleanup for unknown background task %s", task_id)
            return

        # Only clean up tasks that are truly terminal. INTERRUPTED is NOT
        # terminal — the task is paused and must stay resident (result +
        # executor) so ``resume_background_subagent`` can resume it. Do not
        # key off ``completed_at`` alone: an interrupted task never sets it,
        # but a future field change must not accidentally make cleanup greedy.
        if result.status.is_terminal:
            del _background_tasks[task_id]
            _subagent_executors.pop(task_id, None)
            logger.debug("Cleaned up background task: %s", task_id)
        else:
            logger.debug(
                "Skipping cleanup for non-terminal background task %s (status=%s)",
                task_id,
                (result.status.value if hasattr(result.status, "value") else result.status),
            )


def get_subagent_executor(task_id: str) -> SubagentExecutor | None:
    """Return the resident executor for a background task, if any.

    Returns ``None`` for unknown or already-cleaned-up tasks. The executor is
    kept resident specifically so an INTERRUPTED subagent can be resumed.
    """
    with _background_tasks_lock:
        return _subagent_executors.get(task_id)


def get_subagent_interrupt(task_id: str) -> dict[str, Any] | None:
    """Return interrupt metadata for a paused subagent, or ``None`` if not paused.

    Used by the resume endpoint to (a) verify the task is actually interrupted
    and (b) surface the interrupt question to the caller. Returns ``None`` when
    the task is unknown or not in the INTERRUPTED state.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
    if result is None or result.status is not SubagentStatus.INTERRUPTED:
        return None
    return {
        "task_id": result.task_id,
        "subagent_thread_id": result.subagent_thread_id,
        "interrupts": result.interrupts,
        "interrupted_at": (result.interrupted_at.isoformat() if result.interrupted_at else None),
    }


def resume_background_subagent(task_id: str, resume_value: Any) -> str:
    """Resume an INTERRUPTED background subagent by ``task_id``.

    Looks up the resident executor and delegates to
    ``SubagentExecutor.resume_async``. The caller polls the same
    ``SubagentResult`` (via ``get_background_task_result``) for the resumed
    run's progress.

    Raises:
        KeyError: Unknown task_id.
        RuntimeError: The task is not currently INTERRUPTED.

    Returns:
        The same ``task_id``.
    """
    with _background_tasks_lock:
        executor = _subagent_executors.get(task_id)
        result = _background_tasks.get(task_id)
    if executor is None or result is None:
        raise KeyError(f"Unknown subagent task {task_id}")
    if result.status is not SubagentStatus.INTERRUPTED:
        raise RuntimeError(f"Subagent task {task_id} is not interrupted (status={result.status.value}); cannot resume")
    return executor.resume_async(resume_value, task_id)
