"""Tests for subagent YAML file discovery and storage.

Covers:
- SubagentStorage file discovery (public + custom directories)
- Shadow semantics (custom overrides public by name)
- parse_subagent_file validation
- get_or_new_subagent_storage singleton behavior
- SubagentConfig.exclusive_tools round-trip
"""

from pathlib import Path

from deerflow.subagents.storage import (
    SubagentStorage,
    get_or_new_subagent_storage,
    parse_subagent_file,
    reset_subagent_storage,
)

# ---------------------------------------------------------------------------
# parse_subagent_file
# ---------------------------------------------------------------------------


class TestParseSubagentFile:
    def test_valid_minimal(self, tmp_path: Path):
        f = tmp_path / "test.yaml"
        f.write_text("name: test-agent\ndescription: A test agent\n", encoding="utf-8")
        config = parse_subagent_file(f)
        assert config is not None
        assert config.name == "test-agent"
        assert config.description == "A test agent"
        assert config.system_prompt is None
        assert config.tools is None
        assert config.disallowed_tools == ["task"]
        assert config.exclusive_tools is None
        assert config.skills is None
        assert config.model == "inherit"
        assert config.max_turns == 50
        assert config.timeout_seconds == 900
        assert config.workflow is None

    def test_valid_full(self, tmp_path: Path):
        f = tmp_path / "analyst.yaml"
        f.write_text(
            """
name: data-analyst
description: "Data analysis specialist"
system_prompt: |
  You are a data analyst.
tools:
  - bash
  - read_file
disallowed_tools:
  - task
  - ask_clarification
exclusive_tools:
  - "deerflow.tools.custom:plot_tool"
skills:
  - data-analysis
model: qwen3:32b
max_turns: 80
timeout_seconds: 600
workflow: "deerflow.workflows.research:build_graph"
""",
            encoding="utf-8",
        )
        config = parse_subagent_file(f)
        assert config is not None
        assert config.name == "data-analyst"
        assert config.description == "Data analysis specialist"
        assert config.system_prompt == "You are a data analyst.\n"
        assert config.tools == ["bash", "read_file"]
        assert config.disallowed_tools == ["task", "ask_clarification"]
        assert config.exclusive_tools == ["deerflow.tools.custom:plot_tool"]
        assert config.skills == ["data-analysis"]
        assert config.model == "qwen3:32b"
        assert config.max_turns == 80
        assert config.timeout_seconds == 600
        assert config.workflow == "deerflow.workflows.research:build_graph"

    def test_missing_name(self, tmp_path: Path):
        f = tmp_path / "bad.yaml"
        f.write_text("description: no name\n", encoding="utf-8")
        assert parse_subagent_file(f) is None

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        assert parse_subagent_file(f) is None

    def test_invalid_yaml(self, tmp_path: Path):
        f = tmp_path / "invalid.yaml"
        f.write_text("{ not valid yaml", encoding="utf-8")
        assert parse_subagent_file(f) is None

    def test_not_a_mapping(self, tmp_path: Path):
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        assert parse_subagent_file(f) is None


# ---------------------------------------------------------------------------
# SubagentStorage discovery
# ---------------------------------------------------------------------------


class TestSubagentStorageDiscovery:
    def test_empty_root(self, tmp_path: Path):
        storage = SubagentStorage(tmp_path)
        assert storage.load_subagents() == {}
        assert storage.list_names() == []

    def test_single_public_file(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "agent-a.yaml").write_text("name: agent-a\ndescription: Agent A\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        configs = storage.load_subagents()
        assert len(configs) == 1
        assert "agent-a" in configs
        assert configs["agent-a"].description == "Agent A"

    def test_multiple_files(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        custom = tmp_path / "custom"
        custom.mkdir()
        (public / "agent-a.yaml").write_text("name: agent-a\ndescription: Agent A\n", encoding="utf-8")
        (custom / "agent-b.yaml").write_text("name: agent-b\ndescription: Agent B\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        configs = storage.load_subagents()
        assert len(configs) == 2
        assert "agent-a" in configs
        assert "agent-b" in configs

    def test_custom_shadows_public(self, tmp_path: Path):
        """When both public/ and custom/ define the same name, custom wins."""
        public = tmp_path / "public"
        public.mkdir()
        custom = tmp_path / "custom"
        custom.mkdir()
        (public / "agent-a.yaml").write_text("name: agent-a\ndescription: Public Agent A\n", encoding="utf-8")
        (custom / "agent-a.yaml").write_text("name: agent-a\ndescription: Custom Agent A\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        configs = storage.load_subagents()
        assert len(configs) == 1
        assert configs["agent-a"].description == "Custom Agent A"

    def test_yml_extension(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "agent-a.yml").write_text("name: agent-a\ndescription: Agent A yml\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        configs = storage.load_subagents()
        assert "agent-a" in configs

    def test_ignores_non_yaml_files(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "readme.md").write_text("# readme", encoding="utf-8")
        (public / "agent-a.yaml").write_text("name: agent-a\ndescription: Agent A\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        configs = storage.load_subagents()
        assert len(configs) == 1
        assert "readme" not in configs

    def test_load_subagent_by_name(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "agent-a.yaml").write_text("name: agent-a\ndescription: Agent A\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        config = storage.load_subagent("agent-a")
        assert config is not None
        assert config.name == "agent-a"
        assert storage.load_subagent("nonexistent") is None

    def test_missing_description_warns_but_parses(self, tmp_path: Path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "agent-a.yaml").write_text("name: agent-a\n", encoding="utf-8")
        storage = SubagentStorage(tmp_path)
        config = storage.load_subagent("agent-a")
        assert config is not None
        assert config.description == ""


# ---------------------------------------------------------------------------
# get_or_new_subagent_storage singleton
# ---------------------------------------------------------------------------


class TestSubagentStorageSingleton:
    def teardown_method(self):
        reset_subagent_storage()

    def test_explicit_app_config_returns_fresh_instance(self, tmp_path: Path):
        """When app_config is passed explicitly, a fresh storage is returned
        (mirrors get_or_new_chain_storage behaviour)."""
        from deerflow.config.subagents_config import SubagentsAppConfig

        app_config = SubagentsAppConfig(path=str(tmp_path))
        storage1 = get_or_new_subagent_storage(app_config=app_config)
        storage2 = get_or_new_subagent_storage(app_config=app_config)
        # Fresh instances — per-request config must not pollute singleton
        assert storage1 is not storage2
        assert storage1.get_subagents_root_path() == storage2.get_subagents_root_path()

    def test_singleton_without_app_config(self, tmp_path: Path, monkeypatch):
        """Without app_config, the singleton is reused."""
        from deerflow.config.subagents_config import SubagentsAppConfig

        # Mock get_subagents_app_config (imported lazily inside storage.py) to
        # return a predictable config so the singleton is built from a known root.
        subagents_config = SubagentsAppConfig(path=str(tmp_path))
        monkeypatch.setattr(
            "deerflow.config.subagents_config.get_subagents_app_config",
            lambda: subagents_config,
        )

        storage1 = get_or_new_subagent_storage()
        storage2 = get_or_new_subagent_storage()
        assert storage1 is storage2
