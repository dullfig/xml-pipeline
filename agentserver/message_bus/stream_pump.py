"""
pump_aiostream.py — Stream-Based Message Pump using aiostream

This implementation treats the entire message flow as composable streams.
Fan-out (multi-payload, broadcast) is handled naturally via flatmap.

Key insight: Each step is a stream transformer, not a 1:1 function.
The pipeline is just a composition of stream operators.

Dependencies:
    pip install aiostream
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterable, Callable, List, Dict, Any, Optional

import yaml
from lxml import etree
from aiostream import stream, pipe, operator

# Import existing step implementations (we'll wrap them)
from agentserver.message_bus.steps.repair import repair_step
from agentserver.message_bus.steps.c14n import c14n_step
from agentserver.message_bus.steps.envelope_validation import envelope_validation_step
from agentserver.message_bus.steps.payload_extraction import payload_extraction_step
from agentserver.message_bus.steps.thread_assignment import thread_assignment_step
from agentserver.message_bus.message_state import MessageState, HandlerMetadata


# ============================================================================
# Configuration (same as before)
# ============================================================================

@dataclass
class ListenerConfig:
    name: str
    payload_class_path: str
    handler_path: str
    description: str
    is_agent: bool = False
    peers: List[str] = field(default_factory=list)
    broadcast: bool = False
    payload_class: type = field(default=None, repr=False)
    handler: Callable = field(default=None, repr=False)


@dataclass
class OrganismConfig:
    name: str
    identity_path: str = ""
    port: int = 8765
    thread_scheduling: str = "breadth-first"
    listeners: List[ListenerConfig] = field(default_factory=list)

    # Concurrency tuning
    max_concurrent_pipelines: int = 50    # Total concurrent messages in pipeline
    max_concurrent_handlers: int = 20     # Concurrent handler invocations
    max_concurrent_per_agent: int = 5     # Per-agent rate limit


@dataclass
class Listener:
    name: str
    payload_class: type
    handler: Callable
    description: str
    is_agent: bool = False
    peers: List[str] = field(default_factory=list)
    broadcast: bool = False
    schema: etree.XMLSchema = field(default=None, repr=False)
    root_tag: str = ""


# ============================================================================
# Stream-Based Pipeline Steps
# ============================================================================

def wrap_step(step_fn: Callable) -> Callable:
    """
    Wrap an existing async step function for use with pipe.map.

    Existing steps: async def step(state) -> state
    We keep them as-is since pipe.map handles the iteration.
    """
    return step_fn


async def extract_payloads(state: MessageState) -> AsyncIterable[MessageState]:
    """
    Fan-out step: Extract 1..N payloads from handler response.

    This is used with pipe.flatmap — yields multiple states for each input.
    """
    if state.raw_bytes is None:
        yield state
        return

    try:
        # Wrap in dummy to handle multiple roots
        wrapped = b"<dummy>" + state.raw_bytes + b"</dummy>"
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))

        children = list(tree)
        if not children:
            yield state
            return

        for child in children:
            payload_bytes = etree.tostring(child)
            yield MessageState(
                raw_bytes=payload_bytes,
                thread_id=state.thread_id,
                from_id=state.from_id,
                metadata=state.metadata.copy(),
            )

    except Exception:
        # On parse failure, pass through as-is
        yield state


def make_xsd_validation(schema: etree.XMLSchema) -> Callable:
    """Factory for XSD validation step with schema baked in."""
    async def validate(state: MessageState) -> MessageState:
        if state.payload_tree is None or state.error:
            return state
        try:
            schema.assertValid(state.payload_tree)
        except etree.DocumentInvalid as e:
            state.error = f"XSD validation failed: {e}"
        return state
    return validate


def make_deserialization(payload_class: type) -> Callable:
    """Factory for deserialization step with class baked in."""
    from third_party.xmlable import parse_element

    async def deserialize(state: MessageState) -> MessageState:
        if state.payload_tree is None or state.error:
            return state
        try:
            state.payload = parse_element(payload_class, state.payload_tree)
        except Exception as e:
            state.error = f"Deserialization failed: {e}"
        return state
    return deserialize


# ============================================================================
# The Stream-Based Pump
# ============================================================================

class StreamPump:
    """
    Message pump built on aiostream.

    The entire flow is a single composable stream pipeline.
    Fan-out is natural via flatmap. Concurrency is controlled via task_limit.
    """

    def __init__(self, config: OrganismConfig):
        self.config = config

        # Message queue feeds the stream
        self.queue: asyncio.Queue[MessageState] = asyncio.Queue()

        # Routing table
        self.routing_table: Dict[str, List[Listener]] = {}
        self.listeners: Dict[str, Listener] = {}

        # Per-agent semaphores for rate limiting
        self.agent_semaphores: Dict[str, asyncio.Semaphore] = {}

        # Shutdown control
        self._running = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_listener(self, lc: ListenerConfig) -> Listener:
        root_tag = f"{lc.name.lower()}.{lc.payload_class.__name__.lower()}"

        listener = Listener(
            name=lc.name,
            payload_class=lc.payload_class,
            handler=lc.handler,
            description=lc.description,
            is_agent=lc.is_agent,
            peers=lc.peers,
            broadcast=lc.broadcast,
            schema=self._generate_schema(lc.payload_class),
            root_tag=root_tag,
        )

        if lc.is_agent:
            self.agent_semaphores[lc.name] = asyncio.Semaphore(
                self.config.max_concurrent_per_agent
            )

        self.routing_table.setdefault(root_tag, []).append(listener)
        self.listeners[lc.name] = listener
        return listener

    def register_all(self) -> None:
        for lc in self.config.listeners:
            self.register_listener(lc)

    def _generate_schema(self, payload_class: type) -> etree.XMLSchema:
        """Generate XSD schema from xmlified payload class."""
        if hasattr(payload_class, 'xsd'):
            xsd_tree = payload_class.xsd()
            return etree.XMLSchema(xsd_tree)
        # Fallback for non-xmlified classes (e.g., in tests)
        permissive = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:any processContents="lax"/></xs:schema>'
        return etree.XMLSchema(etree.fromstring(permissive.encode()))

    # ------------------------------------------------------------------
    # Stream Source
    # ------------------------------------------------------------------

    async def _queue_source(self) -> AsyncIterable[MessageState]:
        """Async generator that yields messages from the queue."""
        while self._running:
            try:
                state = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                yield state
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Pipeline Steps (as stream operators)
    # ------------------------------------------------------------------

    async def _route_step(self, state: MessageState) -> MessageState:
        """Determine target listeners based on to_id.class format."""
        if state.error or state.payload is None:
            return state

        payload_class_name = type(state.payload).__name__.lower()
        to_id = (state.to_id or "").lower()
        root_tag = f"{to_id}.{payload_class_name}" if to_id else payload_class_name

        targets = self.routing_table.get(root_tag)
        if targets:
            state.target_listeners = targets
        else:
            state.error = f"No listener for: {root_tag}"

        return state

    async def _dispatch_to_handlers(self, state: MessageState) -> AsyncIterable[MessageState]:
        """
        Fan-out step: Dispatch to handler(s) and yield response states.

        For broadcast, yields one response per listener.
        Each response becomes a new message in the stream.
        """
        if state.error or not state.target_listeners:
            # Pass through errors/unroutable for downstream handling
            yield state
            return

        for listener in state.target_listeners:
            try:
                # Rate limiting for agents
                semaphore = self.agent_semaphores.get(listener.name)
                if semaphore:
                    await semaphore.acquire()

                try:
                    metadata = HandlerMetadata(
                        thread_id=state.thread_id or "",
                        from_id=state.from_id or "",
                        own_name=listener.name if listener.is_agent else None,
                    )

                    response_bytes = await listener.handler(state.payload, metadata)

                    if not isinstance(response_bytes, bytes):
                        response_bytes = b"<huh>Handler returned invalid type</huh>"

                    # Yield response — will be processed by next iteration
                    yield MessageState(
                        raw_bytes=response_bytes,
                        thread_id=state.thread_id,
                        from_id=listener.name,
                    )

                finally:
                    if semaphore:
                        semaphore.release()

            except Exception as exc:
                yield MessageState(
                    raw_bytes=f"<huh>Handler {listener.name} crashed: {exc}</huh>".encode(),
                    thread_id=state.thread_id,
                    from_id=listener.name,
                    error=str(exc),
                )

    async def _reinject_responses(self, state: MessageState) -> None:
        """Push handler responses back into the queue for next iteration."""
        await self.queue.put(state)

    # ------------------------------------------------------------------
    # Build the Pipeline
    # ------------------------------------------------------------------

    def build_pipeline(self, source: AsyncIterable[MessageState]):
        """
        Construct the full processing pipeline.

        This is where you configure the flow. Modify this method to:
        - Add/remove steps
        - Change concurrency limits
        - Insert logging/metrics
        - Add filtering
        """

        # The pipeline is a composition of stream operators
        pipeline = (
            stream.iterate(source)

            # ============================================================
            # STAGE 1: Envelope Processing (1:1 transforms)
            # ============================================================
            | pipe.map(repair_step)
            | pipe.map(c14n_step)
            | pipe.map(envelope_validation_step)
            | pipe.map(payload_extraction_step)
            | pipe.map(thread_assignment_step)

            # ============================================================
            # STAGE 2: Fan-out — Extract Multiple Payloads (1:N)
            # ============================================================
            # Handler responses may contain multiple payloads.
            # Each becomes a separate message in the stream.
            | pipe.flatmap(extract_payloads)

            # ============================================================
            # STAGE 3: Per-Payload Validation (1:1 transforms)
            # ============================================================
            # Note: In a real implementation, you'd route to listener-specific
            # validation here. For now, we use a simplified approach.
            | pipe.map(self._validate_and_deserialize)

            # ============================================================
            # STAGE 4: Routing (1:1)
            # ============================================================
            | pipe.map(self._route_step)

            # ============================================================
            # STAGE 5: Filter Errors
            # ============================================================
            # Errors go to a separate handler (could also be a branch)
            | pipe.map(self._handle_errors)
            | pipe.filter(lambda s: s.error is None and s.target_listeners)

            # ============================================================
            # STAGE 6: Fan-out — Dispatch to Handlers (1:N for broadcast)
            # ============================================================
            # This is where handlers are invoked. Broadcast = multiple yields.
            # task_limit controls concurrent handler invocations.
            | pipe.flatmap(
                self._dispatch_to_handlers,
                task_limit=self.config.max_concurrent_handlers
            )

            # ============================================================
            # STAGE 7: Re-inject Responses
            # ============================================================
            # Handler responses go back into the queue for next iteration.
            # The cycle continues until no more messages.
            | pipe.action(self._reinject_responses)
        )

        return pipeline

    async def _validate_and_deserialize(self, state: MessageState) -> MessageState:
        """
        Combined validation + deserialization.

        Uses to_id + payload tag to find the right listener and schema.
        """
        if state.error or state.payload_tree is None:
            return state

        # Build lookup key: to_id.payload_tag (matching routing table format)
        payload_tag = state.payload_tree.tag
        if payload_tag.startswith("{"):
            payload_tag = payload_tag.split("}", 1)[1]

        to_id = (state.to_id or "").lower()
        lookup_key = f"{to_id}.{payload_tag.lower()}" if to_id else payload_tag.lower()

        listeners = self.routing_table.get(lookup_key, [])
        if not listeners:
            state.error = f"No listener for: {lookup_key}"
            return state

        listener = listeners[0]

        # Validate against listener's schema
        try:
            listener.schema.assertValid(state.payload_tree)
        except etree.DocumentInvalid as e:
            state.error = f"XSD validation failed: {e}"
            return state

        # Deserialize
        try:
            from third_party.xmlable import parse_element
            state.payload = parse_element(listener.payload_class, state.payload_tree)
        except Exception as e:
            state.error = f"Deserialization failed: {e}"

        return state

    async def _handle_errors(self, state: MessageState) -> MessageState:
        """Log errors (could also emit <huh> messages)."""
        if state.error:
            print(f"[ERROR] {state.thread_id}: {state.error}")
            # Could emit <huh> to a specific listener here
        return state

    # ------------------------------------------------------------------
    # Run the Pump
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main entry point — run the stream pipeline.

        The pipeline pulls from the queue, processes messages,
        and re-injects handler responses. Continues until shutdown.
        """
        self._running = True

        pipeline = self.build_pipeline(self._queue_source())

        try:
            async with pipeline.stream() as streamer:
                async for _ in streamer:
                    # The pipeline drives itself via re-injection.
                    # We just need to consume the stream.
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------

    async def inject(self, raw_bytes: bytes, thread_id: str, from_id: str) -> None:
        """Inject a message to start processing."""
        state = MessageState(
            raw_bytes=raw_bytes,
            thread_id=thread_id,
            from_id=from_id,
        )
        await self.queue.put(state)

    async def shutdown(self) -> None:
        """Graceful shutdown — wait for queue to drain."""
        self._running = False
        await self.queue.join()


# ============================================================================
# Config Loader (same as before)
# ============================================================================

class ConfigLoader:
    @classmethod
    def load(cls, path: str | Path) -> OrganismConfig:
        with open(Path(path)) as f:
            raw = yaml.safe_load(f)
        return cls._parse(raw)

    @classmethod
    def _parse(cls, raw: dict) -> OrganismConfig:
        org = raw.get("organism", {})
        config = OrganismConfig(
            name=org.get("name", "unnamed"),
            identity_path=org.get("identity", ""),
            port=org.get("port", 8765),
            thread_scheduling=raw.get("thread_scheduling", "breadth-first"),
            max_concurrent_pipelines=raw.get("max_concurrent_pipelines", 50),
            max_concurrent_handlers=raw.get("max_concurrent_handlers", 20),
            max_concurrent_per_agent=raw.get("max_concurrent_per_agent", 5),
        )

        for entry in raw.get("listeners", []):
            lc = cls._parse_listener(entry)
            cls._resolve_imports(lc)
            config.listeners.append(lc)

        return config

    @classmethod
    def _parse_listener(cls, raw: dict) -> ListenerConfig:
        return ListenerConfig(
            name=raw["name"],
            payload_class_path=raw["payload_class"],
            handler_path=raw["handler"],
            description=raw["description"],
            is_agent=raw.get("agent", False),
            peers=raw.get("peers", []),
            broadcast=raw.get("broadcast", False),
        )

    @classmethod
    def _resolve_imports(cls, lc: ListenerConfig) -> None:
        mod, cls_name = lc.payload_class_path.rsplit(".", 1)
        lc.payload_class = getattr(importlib.import_module(mod), cls_name)

        mod, fn_name = lc.handler_path.rsplit(".", 1)
        lc.handler = getattr(importlib.import_module(mod), fn_name)


# ============================================================================
# Bootstrap
# ============================================================================

async def bootstrap(config_path: str = "config/organism.yaml") -> StreamPump:
    """Load config and create pump."""
    config = ConfigLoader.load(config_path)
    print(f"Organism: {config.name}")
    print(f"Listeners: {len(config.listeners)}")

    pump = StreamPump(config)
    pump.register_all()

    print(f"Routing: {list(pump.routing_table.keys())}")
    return pump


# ============================================================================
# Example: Customizing the Pipeline
# ============================================================================

"""
The beauty of aiostream: the pipeline is just a composition.
You can easily insert, remove, or reorder stages.

# Add logging between stages:
| pipe.action(lambda s: print(f"After repair: {s.thread_id}"))

# Add throttling:
| pipe.map(some_step, task_limit=5)

# Branch errors to a separate stream:
errors, valid = stream.partition(source, lambda s: s.error is not None)

# Merge multiple sources:
combined = stream.merge(queue_source, oob_source, external_api_source)

# Add timeout per message:
| pipe.timeout(30.0)  # 30 second timeout per item

# Rate limit the whole pipeline:
| pipe.spaceout(0.1)  # 100ms between items
"""


# ============================================================================
# Comparison: Old vs New
# ============================================================================

"""
OLD (bus.py):
    for payload in payloads:
        await pipeline.process(payload)  # Sequential, recursive

NEW (aiostream):
    | pipe.flatmap(extract_payloads)     # Fan-out, parallel
    | pipe.flatmap(dispatch, task_limit=20)  # Concurrent handlers

The key difference:
- Old: 3 tool calls = 3 sequential awaits, each blocking until complete
- New: 3 tool calls = 3 items in stream, processed concurrently up to task_limit
"""
