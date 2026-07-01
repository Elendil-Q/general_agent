"""Regression test: subagent _create_agent() must isolate from the parent run checkpointer.

Historically the subagent compiled with ``checkpointer=False`` because it was
one-shot and never resumed, and inheriting the parent run's *synchronous*
checkpointer (e.g. ``SqliteSaver`` via ``DeerFlowClient``) through
``copy_context()`` + ``ensure_config()`` made LangGraph call the sync saver's
async methods, raising ``NotImplementedError``.

The subagent now owns its own async-capable, loop-safe ``InMemorySaver`` so it
can ``interrupt()`` / ``Command(resume=...)``. The original harm — inheriting
the *parent's* checkpointer — is still guarded: the subagent must use its own
saver, never the parent's (sync or async-main-loop-bound).
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Module names mocked to break circular imports (same set as test_subagent_executor.py)
_MOCKED_MODULE_NAMES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
    "deerflow.skills.storage",
]


def _default_app_config():
    return SimpleNamespace(tool_search=SimpleNamespace(enabled=False))


def _clear_stale_executor_package_attr() -> None:
    subagents_pkg = sys.modules.get("deerflow.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


@pytest.fixture(autouse=True)
def _setup_executor_module():
    """Set up mocked modules and import the real executor (same pattern as test_subagent_executor.py)."""
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")

    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    _clear_stale_executor_package_attr()

    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **kwargs: SimpleNamespace(load_skills=lambda *, enabled_only: [])
    sys.modules["deerflow.skills.storage"] = storage_module

    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor

    executor_module = sys.modules["deerflow.subagents.executor"]
    executor_module.get_app_config = _default_app_config

    yield {
        "SubagentConfig": SubagentConfig,
        "SubagentExecutor": SubagentExecutor,
        "executor_module": executor_module,
    }

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]

    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]


class TestSubagentCheckpointerIsolation:
    """Verify _create_agent() uses the subagent's OWN checkpointer, never the parent's."""

    def test_create_agent_uses_owned_checkpointer(self, _setup_executor_module, monkeypatch: pytest.MonkeyPatch):
        """The subagent compiles with its own InMemorySaver (truthy), not False/None."""
        SubagentConfig = _setup_executor_module["SubagentConfig"]
        SubagentExecutor = _setup_executor_module["SubagentExecutor"]
        executor_module = _setup_executor_module["executor_module"]

        captured_kwargs: dict = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            agent = MagicMock()
            agent.checkpointer = kwargs.get("checkpointer")
            return agent

        def fake_build_subagent_runtime_middlewares(**kwargs):
            return []

        monkeypatch.setattr(executor_module, "create_agent", fake_create_agent)
        mw_module = ModuleType("deerflow.agents.middlewares.tool_error_handling_middleware")
        mw_module.build_subagent_runtime_middlewares = fake_build_subagent_runtime_middlewares
        monkeypatch.setitem(
            sys.modules,
            "deerflow.agents.middlewares.tool_error_handling_middleware",
            mw_module,
        )

        executor = SubagentExecutor(
            config=SubagentConfig(
                name="test",
                description="test",
                system_prompt="You are a test agent.",
            ),
            tools=[],
        )

        executor.model_name = "test-model"
        executor._base_tools = []

        monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: MagicMock())
        monkeypatch.setattr(executor_module, "resolve_subagent_model_name", lambda config, parent, app_config=None: "test-model")

        result = executor._create_agent()

        used = captured_kwargs.get("checkpointer")
        # Must be a real checkpointer (the executor's own InMemorySaver), not False/None.
        assert used is not False, "Expected subagent to use its own checkpointer, got checkpointer=False"
        assert used is not None, "Expected subagent to use its own checkpointer, got checkpointer=None"
        # And it must be the exact instance the executor owns.
        assert used is executor._checkpointer
        assert result.checkpointer is executor._checkpointer

    def test_parent_checkpointer_not_inherited(self, _setup_executor_module, monkeypatch: pytest.MonkeyPatch):
        """A sentinel checkpointer supplied to the executor is NOT used unless explicitly passed as ``checkpointer``.

        The parent run's checkpointer must never leak into the subagent graph.
        The subagent always falls back to its own ``InMemorySaver`` when no
        explicit ``checkpointer`` argument is given.
        """
        SubagentConfig = _setup_executor_module["SubagentConfig"]
        SubagentExecutor = _setup_executor_module["SubagentExecutor"]
        executor_module = _setup_executor_module["executor_module"]

        captured_kwargs: dict = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            agent = MagicMock()
            agent.checkpointer = kwargs.get("checkpointer")
            return agent

        monkeypatch.setattr(executor_module, "create_agent", fake_create_agent)
        mw_module = ModuleType("deerflow.agents.middlewares.tool_error_handling_middleware")
        mw_module.build_subagent_runtime_middlewares = lambda **kwargs: []
        monkeypatch.setitem(
            sys.modules,
            "deerflow.agents.middlewares.tool_error_handling_middleware",
            mw_module,
        )

        parent_sentinel = MagicMock(name="parent_checkpointer")

        # No checkpointer= kwarg -> subagent uses its own InMemorySaver, NOT the sentinel.
        executor = SubagentExecutor(
            config=SubagentConfig(name="test", description="test", system_prompt="x"),
            tools=[],
            thread_id="parent-thread",
            task_id="call_1",
        )
        executor.model_name = "test-model"
        executor._base_tools = []
        monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: MagicMock())
        monkeypatch.setattr(executor_module, "resolve_subagent_model_name", lambda config, parent, app_config=None: "test-model")

        # Even if a parent checkpointer were stashed on the executor, _create_agent
        # must use self._checkpointer (the owned InMemorySaver), never the parent's.
        executor._parent_checkpointer_leak = parent_sentinel

        executor._create_agent()

        used = captured_kwargs.get("checkpointer")
        assert used is not parent_sentinel
        assert used is executor._checkpointer
