"""
registry.py — WorkerRegistry for background process management.

Handlers spawn background processes via the registry. Workers communicate
through multiprocessing Queues (inbox for commands, outbox for results).
Workers are automatically stopped when their owning thread is pruned.

Worker function contract:
    def my_worker(inbox: Queue, outbox: Queue, **kwargs):
        while True:
            try:
                msg = inbox.get(timeout=1.0)
                if msg is None:  # Sentinel = stop
                    break
            except Empty:
                pass
            do_work()

Usage from handlers:
    registry = get_worker_registry()
    worker_id = registry.spawn(thread_id, listener_name, target_fn, kwargs={...})
    messages = registry.receive(worker_id)
    registry.stop(worker_id)
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkerHandle:
    """Wraps a background worker process with its communication channels."""

    worker_id: str
    thread_id: str
    listener_name: str
    process: multiprocessing.Process
    inbox: multiprocessing.Queue    # Messages TO worker
    outbox: multiprocessing.Queue   # Messages FROM worker
    started_at: float               # time.time()


@dataclass
class WorkerStatus:
    """Status snapshot of a worker."""

    worker_id: str
    alive: bool
    pid: Optional[int]
    uptime: float
    listener_name: str
    thread_id: str


class WorkerRegistry:
    """
    Registry for background worker processes.

    Thread-safe. Manages lifecycle of multiprocessing.Process workers
    that handlers can interact with via non-blocking message queues.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: Dict[str, WorkerHandle] = {}
        self._by_thread: Dict[str, List[str]] = {}

    def spawn(
        self,
        thread_id: str,
        listener_name: str,
        target: Callable,
        *,
        kwargs: Dict[str, Any] | None = None,
    ) -> str:
        """
        Spawn a background worker process.

        Args:
            thread_id: Owning thread (worker is stopped when thread dies)
            listener_name: Who spawned this worker
            target: Worker function — called as target(inbox, outbox, **kwargs)
            kwargs: Additional keyword arguments passed to target

        Returns:
            worker_id (UUID string)
        """
        worker_id = str(uuid.uuid4())
        inbox: multiprocessing.Queue = multiprocessing.Queue()
        outbox: multiprocessing.Queue = multiprocessing.Queue()

        worker_kwargs = kwargs or {}
        process = multiprocessing.Process(
            target=target,
            args=(inbox, outbox),
            kwargs=worker_kwargs,
            daemon=True,
            name=f"worker-{listener_name}-{worker_id[:8]}",
        )
        process.start()

        handle = WorkerHandle(
            worker_id=worker_id,
            thread_id=thread_id,
            listener_name=listener_name,
            process=process,
            inbox=inbox,
            outbox=outbox,
            started_at=time.time(),
        )

        with self._lock:
            self._workers[worker_id] = handle
            self._by_thread.setdefault(thread_id, []).append(worker_id)

        logger.info(
            f"Worker {worker_id[:8]}... spawned for {listener_name} "
            f"on thread {thread_id[:8]}... (pid={process.pid})"
        )
        return worker_id

    def send(self, worker_id: str, message: Any) -> None:
        """
        Send a message to worker's inbox (non-blocking).

        Raises:
            KeyError: If worker_id not found
        """
        with self._lock:
            handle = self._workers.get(worker_id)
            if handle is None:
                raise KeyError(f"Worker {worker_id} not found")
        handle.inbox.put_nowait(message)

    def receive(self, worker_id: str) -> List[Any]:
        """
        Drain worker's outbox (non-blocking).

        Returns all available messages without waiting.

        Raises:
            KeyError: If worker_id not found
        """
        with self._lock:
            handle = self._workers.get(worker_id)
            if handle is None:
                raise KeyError(f"Worker {worker_id} not found")

        messages: List[Any] = []
        while True:
            try:
                messages.append(handle.outbox.get_nowait())
            except Empty:
                break
        return messages

    def status(self, worker_id: str) -> WorkerStatus:
        """
        Get current status of a worker.

        Raises:
            KeyError: If worker_id not found
        """
        with self._lock:
            handle = self._workers.get(worker_id)
            if handle is None:
                raise KeyError(f"Worker {worker_id} not found")

        return WorkerStatus(
            worker_id=worker_id,
            alive=handle.process.is_alive(),
            pid=handle.process.pid,
            uptime=time.time() - handle.started_at,
            listener_name=handle.listener_name,
            thread_id=handle.thread_id,
        )

    def stop(self, worker_id: str, *, join_timeout: float = 5.0) -> None:
        """
        Gracefully stop a worker.

        Sends None sentinel to inbox, waits for join, terminates if stuck.

        Raises:
            KeyError: If worker_id not found
        """
        with self._lock:
            handle = self._workers.pop(worker_id, None)
            if handle is None:
                raise KeyError(f"Worker {worker_id} not found")
            # Remove from thread index
            thread_workers = self._by_thread.get(handle.thread_id, [])
            if worker_id in thread_workers:
                thread_workers.remove(worker_id)
            if not thread_workers:
                self._by_thread.pop(handle.thread_id, None)

        self._stop_process(handle, join_timeout=join_timeout)

    def cleanup_for_thread(self, thread_id: str, *, join_timeout: float = 5.0) -> int:
        """
        Stop all workers for a thread.

        Called by pump on thread death. Returns count of workers stopped.
        """
        with self._lock:
            worker_ids = self._by_thread.pop(thread_id, [])
            handles = []
            for wid in worker_ids:
                handle = self._workers.pop(wid, None)
                if handle:
                    handles.append(handle)

        for handle in handles:
            self._stop_process(handle, join_timeout=join_timeout)

        return len(handles)

    def shutdown_all(self, *, join_timeout: float = 5.0) -> int:
        """
        Stop all workers. Called by pump.shutdown().

        Returns count of workers stopped.
        """
        with self._lock:
            handles = list(self._workers.values())
            self._workers.clear()
            self._by_thread.clear()

        for handle in handles:
            self._stop_process(handle, join_timeout=join_timeout)

        if handles:
            logger.info(f"WorkerRegistry shutdown: stopped {len(handles)} worker(s)")
        return len(handles)

    def _stop_process(self, handle: WorkerHandle, *, join_timeout: float = 5.0) -> None:
        """Stop a single worker process: sentinel → join → terminate."""
        if not handle.process.is_alive():
            return

        try:
            handle.inbox.put_nowait(None)  # Sentinel
        except Exception:
            pass

        handle.process.join(timeout=join_timeout)

        if handle.process.is_alive():
            logger.warning(
                f"Worker {handle.worker_id[:8]}... did not stop gracefully, terminating"
            )
            handle.process.terminate()
            handle.process.join(timeout=2.0)

        logger.debug(f"Worker {handle.worker_id[:8]}... stopped")

    @property
    def worker_count(self) -> int:
        """Total number of active workers."""
        with self._lock:
            return len(self._workers)


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[WorkerRegistry] = None
_registry_lock = threading.Lock()


def get_worker_registry() -> WorkerRegistry:
    """Get the global WorkerRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = WorkerRegistry()
    return _registry


def reset_worker_registry() -> None:
    """Reset the global WorkerRegistry. Useful for testing."""
    global _registry
    if _registry is not None:
        _registry.shutdown_all()
    _registry = None
