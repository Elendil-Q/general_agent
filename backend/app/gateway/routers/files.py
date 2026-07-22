"""Files router — browse thread workspace directory tree."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway.path_utils import resolve_thread_virtual_path
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/files", tags=["files"])


class FileEntry(BaseModel):
    """A single file or directory entry in a directory listing."""

    name: str
    path: str
    is_directory: bool
    size: int | None = None
    modified: float | None = None
    extension: str | None = None


class DirectoryListing(BaseModel):
    """Response model for a directory listing."""

    current_path: str
    parent_path: str | None = None
    entries: list[FileEntry]


@router.get("/tree", response_model=DirectoryListing)
async def list_directory_tree(
    thread_id: str,
    request: Request,
    path: str = Query(default="", description="Virtual path to list (e.g. /mnt/user-data/workspace)"),
) -> DirectoryListing:
    """List directory entries for browsing the thread workspace.

    When ``path`` is empty, returns the three root directories
    (workspace/, uploads/, outputs/) under ``/mnt/user-data``.

    When ``path`` is a valid virtual directory path, returns its
    children sorted with directories first, then alphabetically.
    """
    user_id = get_effective_user_id()

    # Root listing: return the three standard user-data subdirectories
    if not path:
        base = get_paths().sandbox_user_data_dir(thread_id, user_id=user_id)
        entries: list[FileEntry] = []
        for dir_name in ["workspace", "uploads", "outputs"]:
            dir_path = base / dir_name
            entry = FileEntry(
                name=dir_name,
                path=f"{VIRTUAL_PATH_PREFIX}/{dir_name}",
                is_directory=True,
            )
            if dir_path.exists():
                st = dir_path.stat()
                entry.modified = st.st_mtime
            entries.append(entry)

        return DirectoryListing(
            current_path=VIRTUAL_PATH_PREFIX,
            parent_path=None,
            entries=entries,
        )

    # Sub-directory listing — resolve and validate the virtual path
    actual_path = resolve_thread_virtual_path(thread_id, path)

    if not actual_path.exists():
        raise HTTPException(status_code=404, detail="Directory not found")

    if not actual_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = []
    try:
        with os.scandir(actual_path) as it:
            for dirent in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
                is_dir = dirent.is_dir(follow_symlinks=False)
                virtual_entry_path = f"{path.rstrip('/')}/{dirent.name}"
                file_entry = FileEntry(
                    name=dirent.name,
                    path=virtual_entry_path,
                    is_directory=is_dir,
                )
                try:
                    st = dirent.stat(follow_symlinks=False)
                    file_entry.modified = st.st_mtime
                    if not is_dir:
                        file_entry.size = st.st_size
                        file_entry.extension = Path(dirent.name).suffix or None
                except OSError:
                    pass  # stat can fail on broken symlinks etc.
                entries.append(file_entry)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    parent = str(Path(path).parent) if path != VIRTUAL_PATH_PREFIX else None
    return DirectoryListing(
        current_path=path,
        parent_path=parent if parent else None,
        entries=entries,
    )
