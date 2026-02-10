"""
handler_sandbox.py — Subprocess isolation for untrusted handlers.

Untrusted handlers run in a persistent subprocess where the pump singleton
is never set. The subprocess has no access to pump internals, so it cannot
forge messages or bypass routing.

Wire protocol uses multiprocessing Queues with XML-bytes serialization
(same format that flows through the pipeline).

Architecture:
    SandboxWorker  — wraps one Process for a single listener
    SandboxRegistry — manages all sandbox workers
    _sandbox_worker_loop — subprocess entry point (target for Process)
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import logging
import multiprocessing
import threading
import time
import uuid
from queue import Empty
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from xml_pipeline.message_bus.pump_config import Listener

logger = logging.getLogger(__name__)

_RESPOND_MARKER = "__RESPOND__"


# ============================================================================
# Helper: import from dotted path
# ============================================================================

def _import_from_path(dotted_path: str) -> Any:
    """Import an object from a dotted path like 'xml_pipeline.tools.calculate.Calculate'."""
    module_path, attr_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


# ============================================================================
# Subprocess worker loop (runs in child process)
# ============================================================================

def _sandbox_worker_loop(
    inbox: multiprocessing.Queue,
    outbox: multiprocessing.Queue,
    handler_path: str,
    payload_class_path: str,
) -> None:
    """
    Worker main loop — target for multiprocessing.Process.

    Imports handler and payload class once at startup, then loops on inbox.
    The pump singleton is never set in this process.
    """
    from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

    # Import handler and payload class ONCE
    handler = _import_from_path(handler_path)
    payload_class = _import_from_path(payload_class_path)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            msg = inbox.get(timeout=1.0)
        except Empty:
            continue

        if msg is None:
            break  # Sentinel → graceful stop

        request_id = msg["request_id"]
        try:
            # Deserialize payload from XML bytes
            from third_party.xmlable import parse_bytes
            payload = parse_bytes(payload_class, msg["payload_xml"])

            # Reconstruct metadata
            metadata = HandlerMetadata(**msg["metadata"])

            # Call handler
            response = loop.run_until_complete(handler(payload, metadata))

            # Serialize response
            if response is None:
                outbox.put({"request_id": request_id, "response_xml": None})
            elif isinstance(response, HandlerResponse):
                payload_obj = response.payload
                tag = type(payload_obj).__name__
                xml_bytes = etree.tostring(
                    payload_obj.xml_value(tag), encoding="utf-8"
                )
                to_str = _RESPOND_MARKER if response.is_response else response.to
                class_path = (
                    f"{type(payload_obj).__module__}"
                    f".{type(payload_obj).__qualname__}"
                )
                outbox.put({
                    "request_id": request_id,
                    "response_xml": xml_bytes,
                    "to": to_str,
                    "response_class_path": class_path,
                })
            else:
                outbox.put({
                    "request_id": request_id,
                    "error": f"Handler returned invalid type: {type(response).__name__}",
                })
        except Exception as exc:
            outbox.put({
                "request_id": request_id,
                "error": str(exc),
            })

    loop.close()


# ============================================================================
# SandboxWorker — wraps one subprocess for a single listener
# ============================================================================

class SandboxWorker:
    """
    Wraps a persistent subprocess for a single untrusted listener.

    The process is spawned once at start() and loops on a Queue.
    dispatch() serializes payloads to XML bytes, sends to the worker,
    and waits for the correlated response.
    """

    def __init__(
        self,
        listener_name: str,
        handler_path: str,
        payload_class_path: str,
    ) -> None:
        self.listener_name = listener_name
        self.handler_path = handler_path
        self.payload_class_path = payload_class_path
        self._process: Optional[multiprocessing.Process] = None
        self._inbox: Optional[multiprocessing.Queue] = None
        self._outbox: Optional[multiprocessing.Queue] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Spawn the worker subprocess."""
        self._inbox = multiprocessing.Queue()
        self._outbox = multiprocessing.Queue()
        self._process = multiprocessing.Process(
            target=_sandbox_worker_loop,
            args=(self._inbox, self._outbox, self.handler_path, self.payload_class_path),
            daemon=True,
            name=f"sandbox-{self.listener_name}",
        )
        self._process.start()
        logger.info(
            f"Sandbox worker started for '{self.listener_name}' "
            f"(pid={self._process.pid})"
        )

    async def dispatch(
        self, payload: Any, metadata: Any
    ) -> Any:
        """
        Dispatch a handler call to the subprocess.

        Serializes payload to XML bytes, sends to worker inbox,
        waits for correlated response, and reconstructs HandlerResponse.

        Returns HandlerResponse or None.
        Raises RuntimeError on handler error.
        """
        from xml_pipeline.message_bus.message_state import HandlerResponse

        request_id = str(uuid.uuid4())

        # Serialize payload to XML bytes
        payload_class_name = type(payload).__name__
        payload_xml = etree.tostring(
            payload.xml_value(payload_class_name), encoding="utf-8"
        )

        # Serialize metadata to dict
        metadata_dict = dataclasses.asdict(metadata)

        msg = {
            "request_id": request_id,
            "payload_xml": payload_xml,
            "metadata": metadata_dict,
        }

        self._inbox.put(msg)

        # Poll outbox for correlated response
        response = await self._wait_for_response(request_id)

        # Handle error
        if "error" in response:
            raise RuntimeError(
                f"Sandboxed handler '{self.listener_name}' error: {response['error']}"
            )

        # None response (terminate chain)
        if response.get("response_xml") is None:
            return None

        # Deserialize response payload
        from third_party.xmlable import parse_bytes
        response_class = _import_from_path(response["response_class_path"])
        response_payload = parse_bytes(response_class, response["response_xml"])

        # Reconstruct HandlerResponse
        if response["to"] == _RESPOND_MARKER:
            return HandlerResponse.respond(response_payload)
        else:
            return HandlerResponse(payload=response_payload, to=response["to"])

    async def _wait_for_response(
        self, request_id: str, poll_interval: float = 0.01
    ) -> dict:
        """Poll the outbox queue until we get the response for request_id."""
        # Buffer for responses belonging to other requests
        buffered: List[dict] = []

        while True:
            try:
                msg = self._outbox.get_nowait()
                if msg.get("request_id") == request_id:
                    # Re-queue buffered messages
                    for b in buffered:
                        self._outbox.put(b)
                    return msg
                else:
                    buffered.append(msg)
            except Empty:
                await asyncio.sleep(poll_interval)

    def stop(self, join_timeout: float = 5.0) -> None:
        """Graceful shutdown: sentinel → join → terminate."""
        if self._process is None or not self._process.is_alive():
            return

        try:
            self._inbox.put_nowait(None)  # Sentinel
        except Exception:
            pass

        self._process.join(timeout=join_timeout)

        if self._process.is_alive():
            logger.warning(
                f"Sandbox worker '{self.listener_name}' did not stop gracefully, terminating"
            )
            self._process.terminate()
            self._process.join(timeout=2.0)

        logger.debug(f"Sandbox worker '{self.listener_name}' stopped")

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None


# ============================================================================
# SandboxRegistry — manages all sandbox workers
# ============================================================================

class SandboxRegistry:
    """
    Manages sandbox workers for all untrusted listeners.

    Created by StreamPump at start(), used for dispatch during message
    processing, and shutdown on pump.shutdown().
    """

    def __init__(self) -> None:
        self._workers: Dict[str, SandboxWorker] = {}

    def register(self, listener: "Listener") -> None:
        """
        Create a SandboxWorker for an untrusted listener.

        Uses the listener's handler and payload_class import paths.
        """
        handler_path = (
            f"{listener.handler.__module__}.{listener.handler.__qualname__}"
        )
        payload_class_path = (
            f"{listener.payload_class.__module__}"
            f".{listener.payload_class.__qualname__}"
        )

        worker = SandboxWorker(
            listener_name=listener.name,
            handler_path=handler_path,
            payload_class_path=payload_class_path,
        )
        self._workers[listener.name] = worker

    def start_all(self) -> None:
        """Spawn all registered workers."""
        for worker in self._workers.values():
            worker.start()
        if self._workers:
            logger.info(
                f"SandboxRegistry: started {len(self._workers)} worker(s): "
                f"{list(self._workers.keys())}"
            )

    async def dispatch(
        self, listener: "Listener", payload: Any, metadata: Any
    ) -> Any:
        """
        Route a handler call to the correct sandbox worker.

        Raises KeyError if listener has no registered worker.
        """
        worker = self._workers.get(listener.name)
        if worker is None:
            raise KeyError(
                f"No sandbox worker for listener '{listener.name}'"
            )
        return await worker.dispatch(payload, metadata)

    def shutdown_all(self, join_timeout: float = 5.0) -> int:
        """Stop all workers. Returns count of workers stopped."""
        count = len(self._workers)
        for worker in self._workers.values():
            worker.stop(join_timeout=join_timeout)
        self._workers.clear()
        if count:
            logger.info(f"SandboxRegistry: stopped {count} worker(s)")
        return count
