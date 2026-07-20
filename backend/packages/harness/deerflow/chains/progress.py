"""Persistent chain run progress.

A chain pipeline runs as a top-level LangGraph graph for the turn it is
invoked (``/chain:<name>``). When the run is force-cancelled (Stop button)
or the process crashes, the in-memory run task disappears and the
checkpoint may be left in an indeterminate state. To let the user resume
the chain from where it stopped, each chain run writes a *progress file*
recording which nodes completed and their results.

The file lives outside the sandbox user-data tree (thread-isolated but not
agent-writable) at::

    {base_dir}/users/{user_id}/threads/{thread_id}/chains/{chain_name}/progress.json

All public methods are synchronous and guarded by a per-instance
``threading.Lock``. Callers on the async path wrap them in
``asyncio.to_thread``; the sync ``add_done_callback`` on the run task may
also call them directly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from deerflow.chains.types import Chain, compute_chain_hash
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.time import now_iso

logger = logging.getLogger(__name__)

ChainStatus = Literal["running", "interrupted", "completed", "failed"]
NodeStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class ChainNodeProgress:
    """Progress entry for a single chain node."""

    status: NodeStatus = "pending"
    result: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class ChainProgress:
    """The full persisted progress document for one chain run."""

    chain_name: str
    run_id: str
    thread_id: str
    user_id: str
    status: ChainStatus = "running"
    input: str = ""
    chain_hash: str = ""
    nodes: dict[str, ChainNodeProgress] = field(default_factory=dict)
    terminal_nodes: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def completed_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.status == "completed")

    def total_count(self) -> int:
        return len(self.nodes)

    def completed_nodes(self) -> dict[str, str]:
        """Map of ``{node_name: result}`` for completed nodes."""
        return {name: (n.result or "") for name, n in self.nodes.items() if n.status == "completed"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_name": self.chain_name,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "status": self.status,
            "input": self.input,
            "chain_hash": self.chain_hash,
            "nodes": {name: asdict(node) for name, node in self.nodes.items()},
            "terminal_nodes": list(self.terminal_nodes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChainProgress:
        raw_nodes = data.get("nodes") or {}
        nodes: dict[str, ChainNodeProgress] = {}
        for name, raw in raw_nodes.items():
            if not isinstance(raw, dict):
                continue
            nodes[str(name)] = ChainNodeProgress(
                status=raw.get("status", "pending"),
                result=raw.get("result"),
                started_at=raw.get("started_at"),
                completed_at=raw.get("completed_at"),
            )
        return cls(
            chain_name=str(data.get("chain_name", "")),
            run_id=str(data.get("run_id", "")),
            thread_id=str(data.get("thread_id", "")),
            user_id=str(data.get("user_id", "")),
            status=data.get("status", "running"),
            input=str(data.get("input", "") or ""),
            chain_hash=str(data.get("chain_hash", "") or ""),
            nodes=nodes,
            terminal_nodes=list(data.get("terminal_nodes") or []),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
        )


def _terminal_node_names(chain: Chain) -> list[str]:
    has_downstream: set[str] = set()
    for node in chain.nodes.values():
        has_downstream.update(node.depends_on)
    return sorted(set(chain.nodes) - has_downstream)


class ChainProgressStore:
    """File-backed progress store for a single (thread, chain, user) triple.

    Each instance owns one ``progress.json`` file. The file is written
    atomically (``temp + os.replace``) so a crash mid-write never leaves a
    truncated document. All mutations hold a per-instance lock; the on-disk
    document is reloaded before each write so concurrent instances (e.g. the
    node writer and the run-task done callback) do not clobber each other.
    """

    def __init__(self, thread_id: str, chain_name: str, *, user_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.chain_name = chain_name
        self.user_id = user_id or get_effective_user_id()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @property
    def progress_file(self) -> Path:
        """Host path to this chain's progress file."""
        return get_paths().thread_dir(self.thread_id, user_id=self.user_id) / "chains" / self.chain_name / "progress.json"

    # ------------------------------------------------------------------
    # Low-level IO
    # ------------------------------------------------------------------

    def _read_raw(self) -> dict[str, Any] | None:
        path = self.progress_file
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to read chain progress %s", path, exc_info=True)
            return None
        return data if isinstance(data, dict) else None

    def _atomic_write(self, data: dict[str, Any]) -> None:
        path = self.progress_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> ChainProgress | None:
        """Load the current progress document, or ``None`` if absent."""
        data = self._read_raw()
        if data is None:
            return None
        return ChainProgress.from_dict(data)

    def create(self, chain: Chain, run_id: str, input_text: str) -> ChainProgress:
        """Create a fresh progress document for a new chain run.

        Overwrites any pre-existing progress for this (thread, chain) - a new
        ``/chain:<name>`` invocation always starts from scratch.
        """
        now = now_iso()
        progress = ChainProgress(
            chain_name=chain.name,
            run_id=run_id,
            thread_id=self.thread_id,
            user_id=self.user_id,
            status="running",
            input=input_text,
            chain_hash=compute_chain_hash(chain),
            nodes={name: ChainNodeProgress() for name in chain.nodes},
            terminal_nodes=_terminal_node_names(chain),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._atomic_write(progress.to_dict())
        return progress

    def start_resume(self, run_id: str) -> ChainProgress | None:
        """Prepare an existing progress document for a resume run.

        Completed nodes keep their results; every other node is reset to
        ``pending``. ``run_id`` and ``status`` are updated. Returns the new
        progress, or ``None`` if no progress file exists.
        """
        with self._lock:
            data = self._read_raw()
            if data is None:
                return None
            progress = ChainProgress.from_dict(data)
            progress.run_id = run_id
            progress.status = "running"
            for name, node in progress.nodes.items():
                if node.status != "completed":
                    progress.nodes[name] = ChainNodeProgress()
            progress.updated_at = now_iso()
            self._atomic_write(progress.to_dict())
            return progress

    def mark_node_running(self, run_id: str, node_name: str) -> None:
        with self._lock:
            data = self._read_raw()
            if data is None:
                return
            progress = ChainProgress.from_dict(data)
            if progress.run_id != run_id:
                return
            node = progress.nodes.get(node_name)
            if node is None:
                return
            node.status = "running"
            node.started_at = now_iso()
            progress.updated_at = now_iso()
            self._atomic_write(progress.to_dict())

    def update_node(
        self,
        run_id: str,
        node_name: str,
        result: str,
        *,
        status: NodeStatus = "completed",
    ) -> None:
        """Record a node's result and (optionally) finalise the chain status."""
        with self._lock:
            data = self._read_raw()
            if data is None:
                return
            progress = ChainProgress.from_dict(data)
            if progress.run_id != run_id:
                return
            node = progress.nodes.get(node_name)
            if node is None:
                return
            node.status = status
            node.result = result
            node.completed_at = now_iso()
            progress.updated_at = now_iso()
            if status == "completed":
                progress.status = "completed" if self._all_completed(progress) else progress.status
            self._atomic_write(progress.to_dict())

    def mark_status(self, run_id: str, status: ChainStatus) -> None:
        with self._lock:
            data = self._read_raw()
            if data is None:
                return
            progress = ChainProgress.from_dict(data)
            if progress.run_id != run_id:
                return
            progress.status = status
            progress.updated_at = now_iso()
            self._atomic_write(progress.to_dict())

    def mark_completed_if_done(self, run_id: str) -> None:
        """Flip status to ``completed`` when every node has finished."""
        with self._lock:
            data = self._read_raw()
            if data is None:
                return
            progress = ChainProgress.from_dict(data)
            if progress.run_id != run_id:
                return
            if self._all_completed(progress):
                progress.status = "completed"
                progress.updated_at = now_iso()
                self._atomic_write(progress.to_dict())

    def delete(self) -> bool:
        """Remove the progress file. Returns ``True`` if a file was deleted."""
        with self._lock:
            path = self.progress_file
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False
            except OSError:
                logger.warning("Failed to delete chain progress %s", path, exc_info=True)
                return False

    @staticmethod
    def _all_completed(progress: ChainProgress) -> bool:
        return bool(progress.nodes) and all(n.status == "completed" for n in progress.nodes.values())

    # ------------------------------------------------------------------
    # Thread-wide listing
    # ------------------------------------------------------------------

    @staticmethod
    def list_for_thread(thread_id: str, *, user_id: str | None = None) -> list[ChainProgress]:
        """List every chain progress document under a thread directory."""
        effective_user_id = user_id or get_effective_user_id()
        chains_root = get_paths().thread_dir(thread_id, user_id=effective_user_id) / "chains"
        results: list[ChainProgress] = []
        if not chains_root.exists():
            return results
        for progress_file in sorted(chains_root.glob("*/progress.json")):
            try:
                with progress_file.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                results.append(ChainProgress.from_dict(data))
        return results
