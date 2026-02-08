"""
workers — Background worker processes for long-running tasks.

Provides WorkerRegistry for spawning, managing, and cleaning up
background multiprocessing.Process workers that handlers can interact
with via non-blocking message queues.
"""

from xml_pipeline.workers.registry import (
    WorkerHandle,
    WorkerStatus,
    WorkerRegistry,
    get_worker_registry,
)

__all__ = [
    "WorkerHandle",
    "WorkerStatus",
    "WorkerRegistry",
    "get_worker_registry",
]
