"""Tests for subagent exclusive_tools loading.

Covers:
- SubagentConfig.exclusive_tools field presence
- SubagentExecutor loads exclusive tools via resolve_variable
- Exclusive tools are merged with inherited tools
- Name conflicts are handled (exclusive skipped if inherited has same name)
- Failed exclusive tool resolution is logged but non-fatal

Note: conftest.py mocks deerflow.subagents.executor to break circular imports.
This file uses delayed import via fixture to test the real implementation.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Module names that need to be mocked to break circular imports (mirrors test_subagent_executor.py)
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


def _clear_stale_executor_package_attr() -> None:
    subagents_pkg = sys.modules.get("deerflow.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


@pytest.fixture()
def real_executor():
    """Yield the real SubagentExecutor class, bypassing conftest's mock."""
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
    executor_module.get_app_config = lambda: SimpleNamespace(tool_search=SimpleNamespace(enabled=False))

    yield {"SubagentConfig": SubagentConfig, "SubagentExecutor": SubagentExecutor}

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]

    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    else:
        sys.modules.pop("deerflow.subagents.executor", None)
    _clear_stale_executor_package_attr()


def _make_tool(name: str):
    """Create a minimal tool stand-in with the given name."""
    tool = SimpleNamespace()
    tool.name = name
    tool.func = lambda x: x
    tool.coroutine = None
    return tool


# ---------------------------------------------------------------------------
# SubagentConfig.exclusive_tools field
# ---------------------------------------------------------------------------


class TestSubagentConfigExclusiveTools:
    def test_default_is_none(self):
        from deerflow.subagents.config import SubagentConfig

        config = SubagentConfig(name="test", description="test")
        assert config.exclusive_tools is None

    def test_exclusive_tools_list(self):
        from deerflow.subagents.config import SubagentConfig

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=["module.path:tool_a", "module.path:tool_b"],
        )
        assert config.exclusive_tools == ["module.path:tool_a", "module.path:tool_b"]


# ---------------------------------------------------------------------------
# SubagentExecutor exclusive_tools loading
# ---------------------------------------------------------------------------


class TestSubagentExecutorExclusiveTools:
    def test_no_exclusive_tools(self, real_executor):
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        config = SubagentConfig(name="test", description="test")
        inherited = [_make_tool("bash"), _make_tool("read_file")]
        executor = SubagentExecutor(config=config, tools=inherited)
        assert len(executor.tools) == 2
        assert {t.name for t in executor.tools} == {"bash", "read_file"}

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_exclusive_tools_loaded(self, mock_resolve, real_executor):
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        exclusive_tool = _make_tool("plot_tool")
        mock_resolve.return_value = exclusive_tool

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=["deerflow.tools.custom:plot_tool"],
        )
        inherited = [_make_tool("bash")]
        executor = SubagentExecutor(config=config, tools=inherited)

        assert len(executor.tools) == 2
        names = {t.name for t in executor.tools}
        assert names == {"bash", "plot_tool"}
        from langchain.tools import BaseTool

        mock_resolve.assert_called_once_with("deerflow.tools.custom:plot_tool", BaseTool)

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_multiple_exclusive_tools(self, mock_resolve, real_executor):
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        def side_effect(path, _expected_type):
            if "plot" in path:
                return _make_tool("plot_tool")
            if "stat" in path:
                return _make_tool("stat_tool")
            raise ValueError("unknown tool")

        mock_resolve.side_effect = side_effect

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=[
                "deerflow.tools.custom:plot_tool",
                "deerflow.tools.custom:stat_tool",
            ],
        )
        inherited = [_make_tool("bash")]
        executor = SubagentExecutor(config=config, tools=inherited)

        assert len(executor.tools) == 3
        names = {t.name for t in executor.tools}
        assert names == {"bash", "plot_tool", "stat_tool"}

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_exclusive_tool_skipped_on_name_conflict(self, mock_resolve, real_executor, caplog):
        """When an exclusive tool has the same name as an inherited tool, skip it."""
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        duplicate_tool = _make_tool("bash")
        mock_resolve.return_value = duplicate_tool

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=["deerflow.tools.custom:bash"],
        )
        inherited = [_make_tool("bash")]
        executor = SubagentExecutor(config=config, tools=inherited)

        assert len(executor.tools) == 1
        assert executor.tools[0].name == "bash"
        assert "name conflicts" in caplog.text

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_failed_exclusive_tool_is_non_fatal(self, mock_resolve, real_executor, caplog):
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        mock_resolve.side_effect = ImportError("No module named 'missing'")

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=["missing.module:tool"],
        )
        inherited = [_make_tool("bash")]
        executor = SubagentExecutor(config=config, tools=inherited)

        assert len(executor.tools) == 1
        assert executor.tools[0].name == "bash"
        assert "Failed to load exclusive tool" in caplog.text

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_exclusive_tools_not_affected_by_tools_allowlist(self, mock_resolve, real_executor):
        """Exclusive tools bypass the tools/dislallowed_tools filtering."""
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        exclusive_tool = _make_tool("exclusive_tool")
        mock_resolve.return_value = exclusive_tool

        config = SubagentConfig(
            name="test",
            description="test",
            tools=["bash"],  # Allowlist only bash
            exclusive_tools=["module.path:exclusive_tool"],
        )
        inherited = [_make_tool("bash"), _make_tool("read_file")]
        executor = SubagentExecutor(config=config, tools=inherited)

        # tools allowlist filters inherited to just bash
        # exclusive_tools adds exclusive_tool on top
        assert len(executor.tools) == 2
        names = {t.name for t in executor.tools}
        assert names == {"bash", "exclusive_tool"}

    @patch("deerflow.subagents.executor.resolve_variable")
    def test_async_exclusive_tool_gets_sync_wrapper(self, mock_resolve, real_executor):
        """Async-only exclusive tools get func wrapper attached."""
        SubagentConfig = real_executor["SubagentConfig"]
        SubagentExecutor = real_executor["SubagentExecutor"]

        async_tool = MagicMock()
        async_tool.name = "async_tool"
        async_tool.func = None
        async_tool.coroutine = MagicMock()
        mock_resolve.return_value = async_tool

        config = SubagentConfig(
            name="test",
            description="test",
            exclusive_tools=["module.path:async_tool"],
        )
        executor = SubagentExecutor(config=config, tools=[])

        assert len(executor.tools) == 1
        assert executor.tools[0].name == "async_tool"
        assert executor.tools[0].func is not None
