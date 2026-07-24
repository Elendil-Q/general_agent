"""Tests for per-user subagent path helpers on Paths."""

from deerflow.config.paths import get_paths


def test_user_subagents_dir():
    paths = get_paths()
    p = paths.user_subagents_dir("default")
    assert p == paths.user_dir("default") / "subagents"


def test_user_subagent_dir_is_flat_file_path():
    paths = get_paths()
    p = paths.user_subagent_dir("default", "Deep-Researcher")
    # flat-file: the path is the .yaml file itself, name lower-cased
    assert p == paths.user_subagents_dir("default") / "deep-researcher.yaml"
