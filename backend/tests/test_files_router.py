"""Tests for the files router — thread workspace directory browsing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import files
from deerflow.config.paths import VIRTUAL_PATH_PREFIX


@pytest.fixture
def thread_id():
    return "test-thread-123"


@pytest.fixture
def authed_client():
    """Create a TestClient with stub auth so the router works."""
    app = make_authed_test_app()
    app.include_router(files.router)
    with TestClient(app) as client:
        yield client


class TestListDirectoryTree:
    def test_list_root_entries(self, authed_client, thread_id, tmp_path):
        """Root listing returns workspace, uploads, outputs directories."""
        for d in ["workspace", "uploads", "outputs"]:
            (tmp_path / d).mkdir()

        with (
            patch("app.gateway.routers.files.get_paths") as mock_get_paths,
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
        ):
            mock_get_paths.return_value.sandbox_user_data_dir.return_value = tmp_path

            response = authed_client.get(f"/api/threads/{thread_id}/files/tree")

        assert response.status_code == 200
        data = response.json()
        assert data["current_path"] == VIRTUAL_PATH_PREFIX
        assert data["parent_path"] is None
        assert len(data["entries"]) == 3

        names = [e["name"] for e in data["entries"]]
        assert names == ["workspace", "uploads", "outputs"]
        for entry in data["entries"]:
            assert entry["is_directory"] is True
            assert entry["path"].startswith(VIRTUAL_PATH_PREFIX)

    def test_list_subdirectory_with_files(self, authed_client, thread_id, tmp_path):
        """Listing a subdirectory returns files and subdirs sorted (dirs first)."""
        subdir = tmp_path / "workspace"
        subdir.mkdir()
        (subdir / "README.md").write_text("# Hello")
        (subdir / "script.py").write_text("print('hi')")
        (subdir / "nested").mkdir()

        virtual_path = f"{VIRTUAL_PATH_PREFIX}/workspace"

        with (
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
            patch(
                "app.gateway.routers.files.resolve_thread_virtual_path",
                return_value=subdir,
            ),
        ):
            response = authed_client.get(f"/api/threads/{thread_id}/files/tree?path={virtual_path}")

        assert response.status_code == 200
        data = response.json()
        assert data["current_path"] == virtual_path
        assert data["parent_path"] == VIRTUAL_PATH_PREFIX
        assert len(data["entries"]) == 3

        # Directories first
        assert data["entries"][0]["name"] == "nested"
        assert data["entries"][0]["is_directory"] is True

        # Then files alphabetically
        assert data["entries"][1]["name"] == "README.md"
        assert data["entries"][1]["is_directory"] is False
        assert data["entries"][1]["extension"] == ".md"
        assert data["entries"][1]["size"] is not None

        assert data["entries"][2]["name"] == "script.py"
        assert data["entries"][2]["is_directory"] is False
        assert data["entries"][2]["extension"] == ".py"

    def test_path_traversal_rejected(self, authed_client, thread_id):
        """Path traversal attempts return 403.

        ``resolve_thread_virtual_path`` catches ``ValueError`` from the
        path resolver and re-raises as ``HTTPException(403)``. Mock the
        resolver (not the wrapper) to exercise the full error path.
        """

        traversal_path = f"{VIRTUAL_PATH_PREFIX}/workspace/../../../etc"

        with (
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
            patch(
                "app.gateway.path_utils.get_paths",
            ) as mock_get_paths,
        ):
            mock_get_paths.return_value.resolve_virtual_path.side_effect = ValueError("Access denied: path traversal detected")

            response = authed_client.get(f"/api/threads/{thread_id}/files/tree?path={traversal_path}")

        # resolve_thread_virtual_path converts ValueError to HTTPException(403)
        assert response.status_code == 403

    def test_nonexistent_path(self, authed_client, thread_id, tmp_path):
        """Non-existent directory returns 404."""
        nonexistent = tmp_path / "nonexistent"
        virtual_path = f"{VIRTUAL_PATH_PREFIX}/nonexistent"

        with (
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
            patch(
                "app.gateway.routers.files.resolve_thread_virtual_path",
                return_value=nonexistent,
            ),
        ):
            response = authed_client.get(f"/api/threads/{thread_id}/files/tree?path={virtual_path}")

        assert response.status_code == 404

    def test_path_is_file_not_dir(self, authed_client, thread_id, tmp_path):
        """A file path (not a directory) returns 400."""
        file_path = tmp_path / "somefile.txt"
        file_path.write_text("content")
        virtual_path = f"{VIRTUAL_PATH_PREFIX}/somefile.txt"

        with (
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
            patch(
                "app.gateway.routers.files.resolve_thread_virtual_path",
                return_value=file_path,
            ),
        ):
            response = authed_client.get(f"/api/threads/{thread_id}/files/tree?path={virtual_path}")

        assert response.status_code == 400

    def test_empty_directory(self, authed_client, thread_id, tmp_path):
        """Empty directory returns empty entries list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        virtual_path = f"{VIRTUAL_PATH_PREFIX}/empty"

        with (
            patch("app.gateway.routers.files.get_effective_user_id", return_value="test-user"),
            patch(
                "app.gateway.routers.files.resolve_thread_virtual_path",
                return_value=empty_dir,
            ),
        ):
            response = authed_client.get(f"/api/threads/{thread_id}/files/tree?path={virtual_path}")

        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
