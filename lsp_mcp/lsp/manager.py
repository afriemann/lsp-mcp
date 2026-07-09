"""Persistent pool of running language server processes."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..config.model import ServerSpec
from .generic_server import GenericLanguageServer

logger = logging.getLogger(__name__)

# Markers that signal a project root when found walking up from a file.
_ROOT_MARKERS = (
    ".git",
    "pyproject.toml",
    "go.mod",
    "package.json",
    "tsconfig.json",
    "Cargo.toml",
)

_IDLE_TTL_SECONDS: float = 900.0  # 15 minutes
_MAX_SERVERS: int = 20
_EVICTION_INTERVAL: float = 60.0  # how often the background sweep runs


def infer_project_root(file_path: str) -> str:
    """
    Walk parent directories of *file_path* looking for a project marker.
    Falls back to the file's own directory if none is found.
    """
    path = Path(file_path).resolve()
    candidate = path.parent if path.is_file() else path
    while True:
        for marker in _ROOT_MARKERS:
            if (candidate / marker).exists():
                return str(candidate)
        parent = candidate.parent
        if parent == candidate:
            # Reached filesystem root without finding a marker
            return str(path.parent if path.is_file() else path)
        candidate = parent


PoolKey = tuple[str, str]  # (server_name, abs_project_root)


@dataclass
class ServerEntry:
    server: GenericLanguageServer
    exit_stack: AsyncExitStack
    lock: asyncio.Lock
    last_used: float
    state: Literal["starting", "ready", "failed"] = "starting"
    start_task: asyncio.Task | None = field(default=None, repr=False)


class ServerManager:
    """
    Owns a pool of running GenericLanguageServer processes, one per
    (server_name, project_root) pair.  Servers are started on demand,
    reused across tool calls, and evicted when idle or when the pool cap
    is exceeded.
    """

    def __init__(
        self,
        idle_ttl: float = _IDLE_TTL_SECONDS,
        max_servers: int = _MAX_SERVERS,
        eviction_interval: float = _EVICTION_INTERVAL,
    ) -> None:
        self._pool: dict[PoolKey, ServerEntry] = {}
        self._pool_lock = asyncio.Lock()
        self._key_locks: dict[PoolKey, asyncio.Lock] = {}
        self._idle_ttl = idle_ttl
        self._max_servers = max_servers
        self._eviction_interval = eviction_interval
        self._eviction_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self, spec: ServerSpec, file_path: str) -> ServerEntry | None:
        """
        Return a ready ``ServerEntry`` for (*spec.name*, inferred root of
        *file_path*), starting the server if absent.

        Returns ``None`` when the server cannot start after one retry.

        Note: crash-retry for *active requests* (i.e. a transport error raised
        during a ``request_*`` call) is not implemented; the dispatch layer
        treats those as tool-level warnings and falls through to the next
        server.  Only startup failures are retried here.
        """
        root = infer_project_root(file_path)
        key: PoolKey = (spec.name, root)

        # Fast path: already ready
        entry = self._pool.get(key)
        if entry is not None and entry.state == "ready":
            entry.last_used = time.monotonic()
            return entry

        # Acquire per-key lock to avoid concurrent double-start
        async with self._key_lock(key):
            entry = self._pool.get(key)
            if entry is not None and entry.state == "ready":
                entry.last_used = time.monotonic()
                return entry

            # (Re-)start the server
            return await self._start(spec, root, key)

    async def aclose(self) -> None:
        """Gracefully close all pool entries and stop the eviction sweep."""
        if self._eviction_task is not None:
            self._eviction_task.cancel()
            try:
                await self._eviction_task
            except asyncio.CancelledError:
                pass
            self._eviction_task = None

        keys = list(self._pool.keys())
        for key in keys:
            await self._evict(key)

    def start_eviction_loop(self) -> None:
        """Start the background eviction sweep (call once from the event loop)."""
        if self._eviction_task is None or self._eviction_task.done():
            self._eviction_task = asyncio.create_task(self._eviction_loop())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key_lock(self, key: PoolKey) -> asyncio.Lock:
        if key not in self._key_locks:
            self._key_locks[key] = asyncio.Lock()
        return self._key_locks[key]

    async def _start(
        self, spec: ServerSpec, root: str, key: PoolKey, *, retry: bool = True
    ) -> ServerEntry | None:
        """Start a new server process for *key*. Retries once on failure."""
        # Remove any stale failed entry
        old = self._pool.pop(key, None)
        if old is not None:
            await self._close_entry(old)

        exit_stack = AsyncExitStack()
        server = GenericLanguageServer(
            command=list(spec.command),
            repository_root_path=root,
        )
        entry = ServerEntry(
            server=server,
            exit_stack=exit_stack,
            lock=asyncio.Lock(),
            last_used=time.monotonic(),
            state="starting",
        )
        self._pool[key] = entry

        try:
            await exit_stack.enter_async_context(server.start_server())
            entry.state = "ready"
            logger.info("Started LSP server %r for root %r", spec.name, root)
            self._ensure_eviction_running()
            await self._enforce_cap()
            return entry
        except Exception as exc:
            logger.warning("Failed to start LSP server %r: %s", spec.name, exc)
            self._pool.pop(key, None)
            await self._close_entry(entry)
            if retry:
                logger.info("Retrying LSP server %r once", spec.name)
                return await self._start(spec, root, key, retry=False)
            entry.state = "failed"
            return None

    async def _close_entry(self, entry: ServerEntry) -> None:
        try:
            await entry.exit_stack.aclose()
        except Exception as exc:
            logger.debug("Error closing server entry: %s", exc)

    async def _evict(self, key: PoolKey) -> None:
        entry = self._pool.pop(key, None)
        if entry is not None:
            await self._close_entry(entry)
            self._key_locks.pop(key, None)

    async def _enforce_cap(self) -> None:
        """Evict LRU entries if the pool exceeds the max size."""
        if len(self._pool) <= self._max_servers:
            return
        # Sort by last_used ascending (oldest first)
        ordered = sorted(self._pool.items(), key=lambda kv: kv[1].last_used)
        to_evict = len(self._pool) - self._max_servers
        for key, _ in ordered[:to_evict]:
            await self._evict(key)

    async def _eviction_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._eviction_interval)
                await self._sweep_idle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Error in eviction loop: %s", exc)

    async def _sweep_idle(self) -> None:
        cutoff = time.monotonic() - self._idle_ttl
        stale = [k for k, v in self._pool.items() if v.last_used < cutoff]
        for key in stale:
            logger.debug("Evicting idle server %r", key)
            await self._evict(key)

    def _ensure_eviction_running(self) -> None:
        if self._eviction_task is None or self._eviction_task.done():
            self._eviction_task = asyncio.create_task(self._eviction_loop())
