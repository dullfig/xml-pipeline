"""
worker.py — Worker process entry point for CPU-bound handler dispatch.

This module provides the entry point for handler execution in worker processes
when using ProcessPoolExecutor. Workers communicate with the main process via
the shared backend (Redis or Manager).

Architecture:
    Main Process (StreamPump)
         │
         │  Submits WorkerTask to ProcessPoolExecutor
         │
         ▼
    Worker Process (this module)
         │
         ├── Imports handler module
         ├── Fetches payload from shared backend
         ├── Executes handler
         ├── Stores response in shared backend
         └── Returns WorkerResult

Key design decisions:
- Minimal IPC payload: Only UUIDs and module paths cross process boundary
- Handler state via shared backend: Workers fetch/store data in Redis
- Process reuse: Workers are reused across tasks (max_tasks_per_child for restart)
- Error isolation: Handler exceptions don't crash the worker pool

Usage:
    # Main process submits task
    from concurrent.futures import ProcessPoolExecutor
    from xml_pipeline.message_bus.worker import execute_handler, WorkerTask

    pool = ProcessPoolExecutor(max_workers=4)
    task = WorkerTask(
        thread_uuid="550e8400-...",
        payload_uuid="6ba7b810-...",
        handler_path="handlers.librarian.handle_query",
        metadata_uuid="7c9e6679-...",
    )
    future = pool.submit(execute_handler, task)
    result = future.result()  # WorkerResult
"""

from __future__ import annotations

import importlib
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xml_pipeline.memory.shared_backend import SharedBackend

logger = logging.getLogger(__name__)


@dataclass
class WorkerTask:
    """
    Task submitted to worker process.

    Contains only UUIDs and paths — actual data lives in shared backend.
    This minimizes IPC overhead and allows large payloads.
    """

    thread_uuid: str
    payload_uuid: str  # UUID for looking up serialized payload
    handler_path: str  # e.g., "handlers.librarian.handle_query"
    metadata_uuid: str  # UUID for looking up serialized HandlerMetadata
    listener_name: str  # Name of the listener (for logging/response injection)
    is_agent: bool = False  # Whether this is an agent handler
    peers: list[str] = field(default_factory=list)  # Allowed peers (for agents)


@dataclass
class WorkerResult:
    """
    Result from worker process.

    Contains UUID for response stored in backend, or error information.
    """

    success: bool
    response_uuid: Optional[str] = None  # UUID for serialized HandlerResponse
    error: Optional[str] = None  # Error message if failed
    error_traceback: Optional[str] = None  # Full traceback for debugging
    elapsed_ms: float = 0.0  # Execution time


# Keys for storing task data in shared backend
def _payload_key(uuid: str) -> str:
    return f"worker:payload:{uuid}"


def _metadata_key(uuid: str) -> str:
    return f"worker:metadata:{uuid}"


def _response_key(uuid: str) -> str:
    return f"worker:response:{uuid}"


def _import_handler(handler_path: str) -> Callable:
    """
    Import and return handler function from path.

    Args:
        handler_path: Dotted path like "handlers.librarian.handle_query"

    Returns:
        The handler function

    Raises:
        ImportError: If module not found
        AttributeError: If function not found in module
    """
    module_path, func_name = handler_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def store_task_data(
    backend: SharedBackend,
    payload: Any,
    metadata: Any,
    ttl: int = 3600,
) -> tuple[str, str]:
    """
    Store payload and metadata in shared backend for worker access.

    Args:
        backend: Shared backend instance
        payload: The @xmlify dataclass payload
        metadata: HandlerMetadata instance
        ttl: Time-to-live in seconds (default 1 hour)

    Returns:
        Tuple of (payload_uuid, metadata_uuid)
    """
    import pickle

    payload_uuid = str(uuid.uuid4())
    metadata_uuid = str(uuid.uuid4())

    # Serialize and store
    payload_bytes = pickle.dumps(payload)
    metadata_bytes = pickle.dumps(metadata)

    # Store as buffer slots (reusing the backend interface)
    # We use a special "worker" prefix to distinguish from regular slots
    backend.buffer_append(f"worker:payload:{payload_uuid}", payload_bytes)
    backend.buffer_append(f"worker:metadata:{metadata_uuid}", metadata_bytes)

    return payload_uuid, metadata_uuid


def fetch_task_data(
    backend: SharedBackend,
    payload_uuid: str,
    metadata_uuid: str,
) -> tuple[Any, Any]:
    """
    Fetch payload and metadata from shared backend.

    Args:
        backend: Shared backend instance
        payload_uuid: UUID of stored payload
        metadata_uuid: UUID of stored metadata

    Returns:
        Tuple of (payload, metadata)
    """
    import pickle

    payload_data = backend.buffer_get_slot(f"worker:payload:{payload_uuid}", 0)
    metadata_data = backend.buffer_get_slot(f"worker:metadata:{metadata_uuid}", 0)

    if payload_data is None:
        raise ValueError(f"Payload not found: {payload_uuid}")
    if metadata_data is None:
        raise ValueError(f"Metadata not found: {metadata_uuid}")

    payload = pickle.loads(payload_data)
    metadata = pickle.loads(metadata_data)

    return payload, metadata


def store_response(
    backend: SharedBackend,
    response: Any,
) -> str:
    """
    Store handler response in shared backend for main process retrieval.

    Args:
        backend: Shared backend instance
        response: HandlerResponse or None

    Returns:
        Response UUID
    """
    import pickle

    response_uuid = str(uuid.uuid4())
    response_bytes = pickle.dumps(response)
    backend.buffer_append(f"worker:response:{response_uuid}", response_bytes)

    return response_uuid


def fetch_response(
    backend: SharedBackend,
    response_uuid: str,
) -> Any:
    """
    Fetch handler response from shared backend.

    Args:
        backend: Shared backend instance
        response_uuid: UUID of stored response

    Returns:
        HandlerResponse or None
    """
    import pickle

    response_data = backend.buffer_get_slot(f"worker:response:{response_uuid}", 0)
    if response_data is None:
        raise ValueError(f"Response not found: {response_uuid}")

    return pickle.loads(response_data)


def cleanup_task_data(
    backend: SharedBackend,
    payload_uuid: str,
    metadata_uuid: str,
    response_uuid: Optional[str] = None,
) -> None:
    """
    Clean up temporary task data from shared backend.

    Call after response has been processed by main process.
    """
    backend.buffer_delete_thread(f"worker:payload:{payload_uuid}")
    backend.buffer_delete_thread(f"worker:metadata:{metadata_uuid}")
    if response_uuid:
        backend.buffer_delete_thread(f"worker:response:{response_uuid}")


def execute_handler(
    task: WorkerTask,
    backend_config: Optional[dict] = None,
) -> WorkerResult:
    """
    Execute a handler in the worker process.

    This is the entry point called by ProcessPoolExecutor.

    Args:
        task: WorkerTask containing UUIDs and handler path
        backend_config: Optional backend configuration dict
                       (if None, uses default shared backend)

    Returns:
        WorkerResult with response UUID or error
    """
    import asyncio
    import time

    start_time = time.monotonic()

    try:
        # Get shared backend
        from xml_pipeline.memory.shared_backend import (
            BackendConfig,
            get_shared_backend,
        )

        if backend_config:
            config = BackendConfig(**backend_config)
            backend = get_shared_backend(config)
        else:
            backend = get_shared_backend()

        # Fetch payload and metadata
        payload, metadata = fetch_task_data(
            backend, task.payload_uuid, task.metadata_uuid
        )

        # Import handler
        handler = _import_handler(task.handler_path)

        # Execute handler (async)
        # Workers run their own event loop for async handlers
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(handler(payload, metadata))
        finally:
            loop.close()

        # Store response
        response_uuid = store_response(backend, response)

        elapsed = (time.monotonic() - start_time) * 1000

        return WorkerResult(
            success=True,
            response_uuid=response_uuid,
            elapsed_ms=elapsed,
        )

    except Exception as e:
        elapsed = (time.monotonic() - start_time) * 1000
        logger.exception(f"Handler execution failed: {task.handler_path}")

        return WorkerResult(
            success=False,
            error=str(e),
            error_traceback=traceback.format_exc(),
            elapsed_ms=elapsed,
        )


def execute_handler_sync(
    task: WorkerTask,
    backend_config: Optional[dict] = None,
) -> WorkerResult:
    """
    Synchronous wrapper for execute_handler.

    Use this when the handler doesn't need async features
    (pure computation, no I/O).
    """
    return execute_handler(task, backend_config)
