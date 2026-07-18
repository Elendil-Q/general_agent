"""Test configuration for the backend test suite.

Sets up sys.path and pre-mocks modules that would cause circular import
issues when unit-testing lightweight config/registry code in isolation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Make 'app' and 'deerflow' importable from any working directory
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Break the circular import chain that exists in production code:
#   deerflow.subagents.__init__
#     -> .executor (SubagentExecutor, SubagentResult)
#       -> deerflow.agents.thread_state
#         -> deerflow.agents.__init__
#           -> lead_agent.agent
#             -> subagent_limit_middleware
#               -> deerflow.subagents.executor  <-- circular!
#
# By injecting a mock for deerflow.subagents.executor *before* any test module
# triggers the import, __init__.py's "from .executor import ..." succeeds
# immediately without running the real executor module.
_executor_mock = MagicMock()
_executor_mock.SubagentExecutor = MagicMock
_executor_mock.SubagentResult = MagicMock
_executor_mock.SubagentStatus = MagicMock
_executor_mock.MAX_CONCURRENT_SUBAGENTS = 3
_executor_mock.get_background_task_result = MagicMock()

sys.modules["deerflow.subagents.executor"] = _executor_mock


@pytest.fixture()
def provisioner_module():
    """Load docker/provisioner/app.py as an importable test module.

    Shared by test_provisioner_kubeconfig and test_provisioner_pvc_volumes so
    that any change to the provisioner entry-point path or module name only
    needs to be updated in one place.
    """
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docker" / "provisioner" / "app.py"
    spec = importlib.util.spec_from_file_location("provisioner_app_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Auto-set user context for every test unless marked no_auto_user
# ---------------------------------------------------------------------------
#
# Repository methods read ``user_id`` from a contextvar by default
# (see ``deerflow.runtime.user_context``). Without this fixture, every
# pre-existing persistence test would raise RuntimeError because the
# contextvar is unset. The fixture sets a default test user on every
# test; tests that explicitly want to verify behaviour *without* a user
# context should mark themselves ``@pytest.mark.no_auto_user``.


@pytest.fixture(autouse=True)
def _reset_skill_storage_singleton():
    """Reset the SkillStorage singleton between tests to prevent cross-test contamination."""
    try:
        from deerflow.skills.storage import reset_skill_storage
    except ImportError:
        yield
        return
    reset_skill_storage()
    try:
        yield
    finally:
        reset_skill_storage()


@pytest.fixture(autouse=True)
def _restore_title_config_singleton():
    """Reset ``_title_config`` to its pristine default after every test.

    ``AppConfig.from_file()`` writes the on-disk ``title`` block into the
    module-level singleton (``config/app_config.py`` calls
    ``load_title_config_from_dict``). Any test that loads the real
    ``config.yaml`` therefore leaves the singleton in a state that
    ``test_title_middleware_core_logic.py`` does not expect; that suite
    relies on the pristine ``TitleConfig()`` default (``enabled=True``).
    We restore the default after every test so test files stay
    independent regardless of order.
    """
    try:
        from deerflow.config.title_config import reset_title_config
    except ImportError:
        yield
        return

    try:
        yield
    finally:
        reset_title_config()


@pytest.fixture(autouse=True)
def _auto_user_context(request):
    """Inject a default ``test-user-autouse`` into the contextvar.

    Opt-out via ``@pytest.mark.no_auto_user``. Uses lazy import so that
    tests which don't touch the persistence layer never pay the cost
    of importing runtime.user_context.
    """
    if request.node.get_closest_marker("no_auto_user"):
        yield
        return

    try:
        from deerflow.runtime.user_context import (
            reset_current_user,
            set_current_user,
        )
    except ImportError:
        yield
        return

    user = SimpleNamespace(id="test-user-autouse", email="test@local")
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.fixture(autouse=True)
def _force_auth_enabled(monkeypatch):
    """Force ``DEER_FLOW_AUTH_DISABLED=0`` so auth/CSRF tests exercise the
    auth-ENABLED path regardless of the local gitignored ``.env``.

    ``app/gateway/auth_disabled.py`` short-circuits ``authenticate()`` to a
    synthetic ``'default'`` user whenever ``DEER_FLOW_AUTH_DISABLED=1`` — a
    convenience for local dev. Without this fixture, any environment whose
    ``.env`` opts into that mode (the shipped default) reports ~33 spurious
    auth/CSRF failures on every ``make test``.

    Tests that exercise the auth-DISABLED path override this directly with
    ``monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")``; monkeypatch undoes
    in LIFO order, so the test's value wins during the test body.
    """
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "0")
    yield


@pytest.fixture(autouse=True)
def _restore_named_logger_levels():
    """Snapshot and restore the ``deerflow``/``app`` logger levels (and root).

    Gateway lifespan startup calls ``apply_logging_level(config.log_level)``,
    which sets the ``deerflow`` and ``app`` logger levels (e.g. to WARNING).
    Tests that drive the real gateway via ``TestClient(app)`` (notably
    ``test_replay_golden.py``) therefore leave these levels mutated, and any
    later test relying on ``caplog`` to capture INFO logs from ``deerflow.*``
    loggers sees an empty ``caplog.text`` because the record is filtered at the
    named logger before it can propagate to root. Snapshot before / restore
    after every test so order-dependent isolation failures disappear.

    Compatible with ``test_logging_level_from_config.py``, which has its own
    ``setup_method``/``teardown_method``: this fixture's teardown runs first
    (outer yield), restoring the pre-test snapshot; the test's own teardown
    then restores the value it captured at setup.
    """
    import logging

    root = logging.root
    snap = {
        "root_level": root.level,
        "deerflow_level": logging.getLogger("deerflow").level,
        "app_level": logging.getLogger("app").level,
    }
    try:
        yield
    finally:
        logging.getLogger("deerflow").setLevel(snap["deerflow_level"])
        logging.getLogger("app").setLevel(snap["app_level"])
        root.setLevel(snap["root_level"])
