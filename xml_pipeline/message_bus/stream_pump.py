"""
pump_aiostream.py — Stream-Based Message Pump using aiostream

This implementation treats the entire message flow as composable streams.
Fan-out (multi-payload, broadcast) is handled naturally via flatmap.

Key insight: Each step is a stream transformer, not a 1:1 function.
The pipeline is just a composition of stream operators.

Dependencies:
    pip install aiostream

CPU-Bound Handlers:
    Handlers marked with `cpu_bound: true` are dispatched to a
    ProcessPoolExecutor instead of running in the main event loop.
    This prevents long-running handlers from blocking other messages.

    Requires shared backend (Redis or Manager) for cross-process data access.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterable, Callable, List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xml_pipeline.crypto.identity import Identity

import yaml
from lxml import etree
from aiostream import stream, pipe, operator

# Import existing step implementations (we'll wrap them)
from xml_pipeline.message_bus.steps.repair import repair_step
from xml_pipeline.message_bus.steps.c14n import c14n_step
from xml_pipeline.message_bus.steps.envelope_validation import envelope_validation_step
from xml_pipeline.message_bus.steps.payload_extraction import payload_extraction_step
from xml_pipeline.message_bus.steps.thread_assignment import thread_assignment_step
from xml_pipeline.message_bus.message_state import MessageState, HandlerMetadata, HandlerResponse, SystemError, ROUTING_ERROR
from xml_pipeline.message_bus.thread_registry import get_registry
from xml_pipeline.message_bus.todo_registry import get_todo_registry
from xml_pipeline.memory import get_context_buffer

pump_logger = logging.getLogger(__name__)


# ============================================================================
# Event Hooks
# ============================================================================

@dataclass
class PumpEvent:
    """Base class for pump events."""
    pass


@dataclass
class MessageReceivedEvent(PumpEvent):
    """Fired when a message is received by a handler."""
    thread_id: str
    from_id: str
    to_id: str
    payload_type: str
    payload: Any


@dataclass
class MessageSentEvent(PumpEvent):
    """Fired when a handler sends a response."""
    thread_id: str
    from_id: str
    to_id: str
    payload_type: str
    payload: Any


@dataclass
class AgentStateEvent(PumpEvent):
    """Fired when an agent's processing state changes."""
    agent_name: str
    state: str  # "idle", "processing", "waiting", "error"
    thread_id: Optional[str] = None


@dataclass
class ThreadEvent(PumpEvent):
    """Fired when a thread is created or completed."""
    thread_id: str
    status: str  # "created", "active", "completed", "error", "killed"
    participants: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ReloadEvent(PumpEvent):
    """Fired when organism configuration is reloaded."""
    success: bool
    added_listeners: List[str] = field(default_factory=list)
    removed_listeners: List[str] = field(default_factory=list)
    updated_listeners: List[str] = field(default_factory=list)
    error: Optional[str] = None


EventCallback = Callable[[PumpEvent], None]


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
    prompt: str = ""  # System prompt for LLM agents (loaded into PromptRegistry)
    cpu_bound: bool = False  # Dispatch to ProcessPoolExecutor if True
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

    # LLM configuration (optional)
    llm_config: Dict[str, Any] = field(default_factory=dict)

    # Process pool configuration (for cpu_bound handlers)
    process_pool_workers: int = 4
    process_pool_max_tasks_per_child: int = 100
    process_pool_enabled: bool = False

    # Backend configuration (for shared state)
    backend_type: str = "memory"  # "memory", "manager", "redis"
    backend_redis_url: str = "redis://localhost:6379"
    backend_redis_prefix: str = "xp:"


@dataclass
class Listener:
    name: str
    payload_class: type
    handler: Callable
    description: str
    is_agent: bool = False
    peers: List[str] = field(default_factory=list)
    broadcast: bool = False
    cpu_bound: bool = False  # Dispatch to ProcessPoolExecutor if True
    handler_path: str = ""  # Import path for worker process
    schema: etree.XMLSchema = field(default=None, repr=False)
    root_tag: str = ""
    usage_instructions: str = ""  # Generated at registration for LLM agents


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

    CPU-bound handlers can be dispatched to a ProcessPoolExecutor by
    marking them with `cpu_bound: true` in config. This requires a
    shared backend (Redis or Manager) for cross-process data access.
    """

    def __init__(self, config: OrganismConfig, config_path: str = ""):
        self.config = config
        self.config_path = config_path  # Store path for hot-reload

        # Message queue feeds the stream
        self.queue: asyncio.Queue[MessageState] = asyncio.Queue()

        # Routing table
        self.routing_table: Dict[str, List[Listener]] = {}
        self.listeners: Dict[str, Listener] = {}

        # Identity for envelope signing (optional)
        self.identity: Optional["Identity"] = None
        if config.identity_path:
            try:
                from xml_pipeline.crypto import Identity
                self.identity = Identity.load(config.identity_path)
                pump_logger.info(f"Identity loaded: {config.identity_path}")
            except Exception as e:
                pump_logger.warning(f"Failed to load identity: {e}")

        # Generic listeners (accept any payload type)
        # Used for ephemeral orchestration handlers (sequences, buffers)
        self._generic_listeners: Dict[str, Listener] = {}

        # Per-agent semaphores for rate limiting
        self.agent_semaphores: Dict[str, asyncio.Semaphore] = {}

        # Shutdown control
        self._running = False

        # Event hooks for external observers (ServerState, etc.)
        self._event_callbacks: List[EventCallback] = []

        # Process pool for cpu_bound handlers
        self._process_pool: Optional[ProcessPoolExecutor] = None
        if config.process_pool_enabled:
            self._process_pool = ProcessPoolExecutor(
                max_workers=config.process_pool_workers,
                max_tasks_per_child=config.process_pool_max_tasks_per_child,
            )
            pump_logger.info(
                f"ProcessPool initialized: {config.process_pool_workers} workers"
            )

        # Shared backend for cross-process state
        self._shared_backend = None
        if config.backend_type != "memory":
            from xml_pipeline.memory.shared_backend import BackendConfig, get_shared_backend
            backend_config = BackendConfig(
                backend_type=config.backend_type,
                redis_url=config.backend_redis_url,
                redis_prefix=config.backend_redis_prefix,
            )
            self._shared_backend = get_shared_backend(backend_config)
            pump_logger.info(f"Shared backend: {config.backend_type}")

    # ------------------------------------------------------------------
    # Event Hooks
    # ------------------------------------------------------------------

    def subscribe_events(self, callback: EventCallback) -> None:
        """Subscribe to pump events (message flow, agent state, thread lifecycle)."""
        self._event_callbacks.append(callback)

    def unsubscribe_events(self, callback: EventCallback) -> None:
        """Unsubscribe from pump events."""
        if callback in self._event_callbacks:
            self._event_callbacks.remove(callback)

    def _emit_event(self, event: PumpEvent) -> None:
        """Emit an event to all subscribers (non-blocking)."""
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                pump_logger.warning(f"Event callback error: {e}")

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
            cpu_bound=lc.cpu_bound,
            handler_path=lc.handler_path,  # For worker process import
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

    def register_generic_listener(
        self,
        name: str,
        handler: Callable,
        description: str = "",
    ) -> Listener:
        """
        Register a generic listener that accepts any payload type.

        Used for ephemeral orchestration handlers (sequences, buffers)
        that need to receive responses from various step types.

        Generic listeners:
        - Are NOT added to the routing table (no root_tag)
        - Are looked up by name (to_id) as a fallback in routing
        - Receive payload_tree directly (no XSD validation/deserialization)

        Args:
            name: Unique listener name (e.g., "sequence_abc123")
            handler: Async handler function (receives payload_tree, metadata)
            description: Human-readable description

        Returns:
            Listener object
        """
        listener = Listener(
            name=name,
            payload_class=object,  # Placeholder - not used
            handler=handler,
            description=description,
            is_agent=False,
            root_tag="*",  # Wildcard marker
        )

        self._generic_listeners[name.lower()] = listener
        self.listeners[name] = listener

        pump_logger.debug(f"Registered generic listener: {name}")
        return listener

    def unregister_listener(self, name: str) -> bool:
        """
        Remove a listener by name.

        Used to clean up ephemeral listeners after orchestration completes.

        Args:
            name: Listener name to remove

        Returns:
            True if found and removed, False if not found
        """
        name_lower = name.lower()
        removed = False

        # Remove from generic listeners
        if name_lower in self._generic_listeners:
            del self._generic_listeners[name_lower]
            removed = True
            pump_logger.debug(f"Unregistered generic listener: {name}")

        # Remove from main listeners dict
        if name in self.listeners:
            listener = self.listeners.pop(name)
            removed = True

            # Remove from routing table
            if listener.root_tag and listener.root_tag != "*":
                listeners_for_tag = self.routing_table.get(listener.root_tag, [])
                if listener in listeners_for_tag:
                    listeners_for_tag.remove(listener)
                    if not listeners_for_tag:
                        del self.routing_table[listener.root_tag]

        return removed

    def register_all(self) -> None:
        # First pass: register all listeners
        for lc in self.config.listeners:
            self.register_listener(lc)

        # Second pass: build usage instructions (needs all listeners registered)
        for listener in self.listeners.values():
            if listener.is_agent and listener.peers:
                listener.usage_instructions = self._build_usage_instructions(listener)

    def _build_usage_instructions(self, agent: Listener) -> str:
        """
        Build LLM system prompt instructions from peer schemas.

        Generates human-readable documentation of what messages
        this agent can send to its peers.
        """
        lines = [
            f"You are the {agent.name} agent.",
            f"Description: {agent.description}",
            "",
            "You can send messages to the following peers:",
        ]

        for peer_name in agent.peers:
            peer = self.listeners.get(peer_name)
            if not peer:
                lines.append(f"\n## {peer_name} (not registered)")
                continue

            lines.append(f"\n## {peer_name}")
            lines.append(f"Description: {peer.description}")

            # Get XSD schema as readable XML
            if hasattr(peer.payload_class, 'xsd'):
                xsd_tree = peer.payload_class.xsd()
                xsd_str = etree.tostring(xsd_tree, pretty_print=True, encoding='unicode')
                lines.append(f"Expected payload schema:\n```xml\n{xsd_str}```")

            # Also show a simple example structure
            if hasattr(peer.payload_class, '__dataclass_fields__'):
                fields = peer.payload_class.__dataclass_fields__
                example_lines = [f"<{peer.payload_class.__name__}>"]
                for fname, finfo in fields.items():
                    example_lines.append(f"  <{fname}>...</{fname}>")
                example_lines.append(f"</{peer.payload_class.__name__}>")
                lines.append(f"Example structure:\n```xml\n" + "\n".join(example_lines) + "\n```")

        lines.append("\n---")
        lines.append("## Important: Response Semantics")
        lines.append("")
        lines.append("When you RESPOND (return to your caller), your call chain is pruned.")
        lines.append("This means:")
        lines.append("- Any sub-agents you called are effectively terminated")
        lines.append("- Their state/context is lost (e.g., calculator memory, scratch space)")
        lines.append("- You cannot call them again in the same context after responding")
        lines.append("")
        lines.append("Therefore: Complete ALL sub-tasks before responding to your caller.")
        lines.append("If you need results from a peer, wait for their response before you respond.")

        return "\n".join(lines)

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

        Handlers can return:
        - None: no response needed
        - HandlerResponse(payload, to): clean dataclass + target (preferred)
        - bytes: raw envelope XML (legacy, for backwards compatibility)
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
                    # Emit agent state change event
                    self._emit_event(AgentStateEvent(
                        agent_name=listener.name,
                        state="processing",
                        thread_id=state.thread_id,
                    ))
                    # Ensure we have a valid thread chain
                    registry = get_registry()
                    todo_registry = get_todo_registry()
                    context_buffer = get_context_buffer()
                    current_thread = state.thread_id or ""

                    # Check if thread exists in registry; if not, register it
                    if current_thread and not registry.lookup(current_thread):
                        # New conversation - register existing UUID to chain
                        # The UUID was assigned by thread_assignment_step
                        from_id = state.from_id or "external"
                        registry.register_thread(current_thread, from_id, listener.name)

                    # Check for todo matches on this message
                    # This may raise eyebrows on watchers for this thread
                    if current_thread and state.payload:
                        payload_type = type(state.payload).__name__
                        todo_registry.check(
                            thread_id=current_thread,
                            payload_type=payload_type,
                            from_id=state.from_id or "",
                            payload=state.payload,
                        )

                    # Detect self-calls (agent sending to itself)
                    is_self_call = (state.from_id or "") == listener.name

                    # Get any raised eyebrows for this agent (for nagging)
                    todo_nudge = ""
                    if listener.is_agent and current_thread:
                        raised = todo_registry.get_raised_for(current_thread, listener.name)
                        todo_nudge = todo_registry.format_nudge(raised)

                    # === CONTEXT BUFFER: Record incoming message ===
                    # Append validated payload to thread's context buffer
                    # The returned BufferSlot becomes the single source of truth
                    slot = None
                    if current_thread and state.payload:
                        try:
                            slot = context_buffer.append(
                                thread_id=current_thread,
                                payload=state.payload,
                                from_id=state.from_id or "unknown",
                                to_id=listener.name,
                                own_name=listener.name if listener.is_agent else None,
                                is_self_call=is_self_call,
                                usage_instructions=listener.usage_instructions,
                                todo_nudge=todo_nudge,
                            )
                        except MemoryError:
                            # Thread exceeded max slots - log and continue
                            import logging
                            logging.getLogger(__name__).warning(
                                f"Thread {current_thread[:8]}... exceeded context buffer limit"
                            )

                    # Derive metadata from slot (single source of truth)
                    # Fall back to manual construction if no slot (e.g., buffer overflow)
                    if slot:
                        from xml_pipeline.memory import slot_to_handler_metadata
                        metadata = slot_to_handler_metadata(slot)
                        payload_ref = slot.payload  # Same reference as in buffer
                    else:
                        metadata = HandlerMetadata(
                            thread_id=current_thread,
                            from_id=state.from_id or "",
                            own_name=listener.name if listener.is_agent else None,
                            is_self_call=is_self_call,
                            usage_instructions=listener.usage_instructions,
                            todo_nudge=todo_nudge,
                        )
                        payload_ref = state.payload

                    # Emit message received event
                    self._emit_event(MessageReceivedEvent(
                        thread_id=current_thread,
                        from_id=state.from_id or "",
                        to_id=listener.name,
                        payload_type=type(payload_ref).__name__,
                        payload=payload_ref,
                    ))

                    # Dispatch to handler - either in-process or via ProcessPool
                    if listener.cpu_bound and self._process_pool and self._shared_backend:
                        response = await self._dispatch_to_process_pool(
                            listener=listener,
                            payload=payload_ref,
                            metadata=metadata,
                        )
                    else:
                        response = await listener.handler(payload_ref, metadata)

                    # None means "no response needed" - don't re-inject
                    if response is None:
                        # Emit idle state
                        self._emit_event(AgentStateEvent(
                            agent_name=listener.name,
                            state="idle",
                            thread_id=current_thread,
                        ))
                        continue

                    # Handle clean HandlerResponse (preferred)
                    if isinstance(response, HandlerResponse):
                        registry = get_registry()

                        if response.is_response:
                            # Response back to caller - prune chain
                            target, new_thread_id = registry.prune_for_response(current_thread)
                            if target is None:
                                # Chain exhausted - nowhere to respond to
                                continue
                            to_id = target
                            thread_id = new_thread_id
                        else:
                            # Forward to named target - validate against peers
                            requested_to = response.to

                            # Enforce peer constraints for agents
                            if listener.is_agent and listener.peers:
                                if requested_to not in listener.peers:
                                    # Agent trying to send to non-peer - send generic error back to agent
                                    # Log details internally but don't reveal to agent
                                    import logging
                                    logging.getLogger(__name__).warning(
                                        f"Peer violation: {listener.name} -> {requested_to} (allowed: {listener.peers})"
                                    )

                                    # Send SystemError back to the agent (keeps thread alive)
                                    error_bytes = self._wrap_in_envelope(
                                        payload=ROUTING_ERROR,
                                        from_id="system",
                                        to_id=listener.name,
                                        thread_id=current_thread,
                                    )
                                    yield MessageState(
                                        raw_bytes=error_bytes,
                                        thread_id=current_thread,
                                        from_id="system",
                                    )
                                    continue

                            to_id = requested_to
                            thread_id = registry.extend_chain(current_thread, to_id)

                        # === CONTEXT BUFFER: Record outgoing response ===
                        # Append handler's response to the target thread's buffer
                        # This happens BEFORE serialization - the buffer holds the clean payload
                        try:
                            context_buffer.append(
                                thread_id=thread_id,
                                payload=response.payload,
                                from_id=listener.name,
                                to_id=to_id,
                            )
                        except MemoryError:
                            import logging
                            logging.getLogger(__name__).warning(
                                f"Thread {thread_id[:8]}... exceeded context buffer limit"
                            )

                        response_bytes = self._wrap_in_envelope(
                            payload=response.payload,
                            from_id=listener.name,
                            to_id=to_id,
                            thread_id=thread_id,
                        )
                    # Legacy: raw bytes (backwards compatible)
                    elif isinstance(response, bytes):
                        response_bytes = response
                        thread_id = state.thread_id
                    else:
                        response_bytes = b"<huh>Handler returned invalid type</huh>"
                        thread_id = state.thread_id

                    # Emit message sent event
                    if isinstance(response, HandlerResponse):
                        self._emit_event(MessageSentEvent(
                            thread_id=thread_id,
                            from_id=listener.name,
                            to_id=to_id,
                            payload_type=type(response.payload).__name__,
                            payload=response.payload,
                        ))

                    # Emit agent state back to idle
                    self._emit_event(AgentStateEvent(
                        agent_name=listener.name,
                        state="idle",
                        thread_id=None,
                    ))

                    # Yield response — will be processed by next iteration
                    yield MessageState(
                        raw_bytes=response_bytes,
                        thread_id=thread_id,
                        from_id=listener.name,
                    )

                finally:
                    if semaphore:
                        semaphore.release()

            except Exception as exc:
                # Emit error state
                self._emit_event(AgentStateEvent(
                    agent_name=listener.name,
                    state="error",
                    thread_id=state.thread_id,
                ))
                yield MessageState(
                    raw_bytes=f"<huh>Handler {listener.name} crashed: {exc}</huh>".encode(),
                    thread_id=state.thread_id,
                    from_id=listener.name,
                    error=str(exc),
                )

    def _wrap_in_envelope(self, payload: Any, from_id: str, to_id: str, thread_id: str) -> bytes:
        """Wrap a dataclass payload in a message envelope, optionally signed."""
        # Serialize payload to XML
        if hasattr(payload, 'to_xml'):
            # SystemError and similar have manual to_xml()
            payload_str = payload.to_xml()
        elif hasattr(payload, 'xml_value'):
            # @xmlify dataclasses
            payload_class_name = type(payload).__name__
            payload_tree = payload.xml_value(payload_class_name)
            payload_str = etree.tostring(payload_tree, encoding='unicode')
        else:
            # Fallback for non-xmlify classes
            payload_class_name = type(payload).__name__
            payload_str = f"<{payload_class_name}>{payload}</{payload_class_name}>"

        # Add xmlns="" to keep payload out of envelope namespace
        if 'xmlns=' not in payload_str:
            idx = payload_str.index('>')
            payload_str = payload_str[:idx] + ' xmlns=""' + payload_str[idx:]

        envelope_str = f"""<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>{from_id}</from>
    <to>{to_id}</to>
    <thread>{thread_id}</thread>
  </meta>
  {payload_str}
</message>"""

        # Sign if identity is configured
        if self.identity is not None:
            try:
                from xml_pipeline.crypto.signing import sign_envelope
                envelope_tree = etree.fromstring(envelope_str.encode('utf-8'))
                signed_tree = sign_envelope(envelope_tree, self.identity, in_place=True)
                return etree.tostring(signed_tree, encoding='utf-8', xml_declaration=True)
            except Exception as e:
                pump_logger.warning(f"Failed to sign envelope: {e}")
                # Fall through to unsigned

        return envelope_str.encode('utf-8')

    async def _dispatch_to_process_pool(
        self,
        listener: Listener,
        payload: Any,
        metadata: HandlerMetadata,
    ) -> Optional[HandlerResponse]:
        """
        Dispatch handler to ProcessPoolExecutor for CPU-bound execution.

        This offloads work to a separate process to avoid blocking
        the main event loop.

        Args:
            listener: The target listener
            payload: The @xmlify dataclass payload
            metadata: Handler metadata

        Returns:
            HandlerResponse or None (same as direct handler call)
        """
        from xml_pipeline.message_bus.worker import (
            WorkerTask,
            store_task_data,
            fetch_response,
            cleanup_task_data,
            execute_handler,
        )

        assert self._process_pool is not None
        assert self._shared_backend is not None

        # Store payload and metadata in shared backend
        payload_uuid, metadata_uuid = store_task_data(
            self._shared_backend, payload, metadata
        )

        # Create worker task
        task = WorkerTask(
            thread_uuid=metadata.thread_id,
            payload_uuid=payload_uuid,
            handler_path=listener.handler_path,
            metadata_uuid=metadata_uuid,
            listener_name=listener.name,
            is_agent=listener.is_agent,
            peers=list(listener.peers),
        )

        # Backend config for worker process
        backend_config = {
            "backend_type": self.config.backend_type,
            "redis_url": self.config.backend_redis_url,
            "redis_prefix": self.config.backend_redis_prefix,
        }

        try:
            # Submit to process pool and await result
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._process_pool,
                execute_handler,
                task,
                backend_config,
            )

            if not result.success:
                pump_logger.error(
                    f"Worker error for {listener.name}: {result.error}"
                )
                if result.error_traceback:
                    pump_logger.debug(f"Traceback: {result.error_traceback}")
                return None

            # Fetch response from shared backend
            if result.response_uuid:
                response = fetch_response(self._shared_backend, result.response_uuid)
                return response

            return None

        finally:
            # Clean up task data from backend
            cleanup_task_data(
                self._shared_backend,
                payload_uuid,
                metadata_uuid,
                result.response_uuid if 'result' in dir() and result.success else None,
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
        Falls back to generic listeners (ephemeral orchestration handlers)
        when no regular listener matches.
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

        # Fallback: check for generic listener by to_id
        # Generic listeners accept any payload type (for orchestration)
        if not listeners and to_id:
            generic_listener = self._generic_listeners.get(to_id)
            if generic_listener:
                # Generic listener: skip XSD validation and deserialization
                # Pass the raw payload_tree to the handler
                state.payload = state.payload_tree  # Handler receives Element
                state.target_listeners = [generic_listener]
                state.metadata["generic_handler"] = True
                return state

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
        """Graceful shutdown — wait for queue to drain and close resources."""
        self._running = False
        await self.queue.join()

        # Shutdown process pool if active
        if self._process_pool:
            self._process_pool.shutdown(wait=True)
            pump_logger.info("ProcessPool shutdown complete")
            self._process_pool = None

    def reload_config(self, config_path: Optional[str] = None) -> ReloadEvent:
        """
        Hot-reload organism configuration.

        Re-reads the config file and updates listeners:
        - New listeners are registered
        - Removed listeners are unregistered
        - Changed listeners are updated (handler, peers, description)

        Args:
            config_path: Path to config file. Uses stored path if not provided.

        Returns:
            ReloadEvent with details of what changed
        """
        path = config_path or self.config_path
        if not path:
            return ReloadEvent(
                success=False,
                error="No config path available for reload",
            )

        try:
            # Re-read config
            new_config = ConfigLoader.load(path)

            # Track changes
            added: List[str] = []
            removed: List[str] = []
            updated: List[str] = []

            # Get current listener names (excluding system listeners)
            current_names = {
                name for name in self.listeners.keys()
                if not name.startswith("system.")
            }

            # Get new listener names
            new_names = {lc.name for lc in new_config.listeners}

            # Find removed listeners
            for name in current_names - new_names:
                if self.unregister_listener(name):
                    removed.append(name)
                    pump_logger.info(f"Hot-reload: removed listener '{name}'")

            # Find new and updated listeners
            for lc in new_config.listeners:
                # Resolve imports for the listener config
                ConfigLoader._resolve_imports(lc)

                if lc.name in current_names:
                    # Check if changed
                    existing = self.listeners.get(lc.name)
                    if existing and self._listener_changed(existing, lc):
                        # Remove old and re-register
                        self.unregister_listener(lc.name)
                        self.register_listener(lc)
                        updated.append(lc.name)
                        pump_logger.info(f"Hot-reload: updated listener '{lc.name}'")
                else:
                    # New listener
                    self.register_listener(lc)
                    added.append(lc.name)
                    pump_logger.info(f"Hot-reload: added listener '{lc.name}'")

            # Rebuild usage instructions for all agents (peers may have changed)
            for listener in self.listeners.values():
                if listener.is_agent and listener.peers:
                    listener.usage_instructions = self._build_usage_instructions(listener)

            # Update stored config
            self.config = new_config

            # Emit reload event
            event = ReloadEvent(
                success=True,
                added_listeners=added,
                removed_listeners=removed,
                updated_listeners=updated,
            )
            self._emit_event(event)

            pump_logger.info(
                f"Hot-reload complete: +{len(added)} -{len(removed)} ~{len(updated)}"
            )
            return event

        except Exception as e:
            pump_logger.error(f"Hot-reload failed: {e}")
            event = ReloadEvent(success=False, error=str(e))
            self._emit_event(event)
            return event

    def _listener_changed(self, existing: Listener, new_config: ListenerConfig) -> bool:
        """Check if listener config has changed."""
        # Compare key fields
        if existing.handler != new_config.handler:
            return True
        if existing.payload_class != new_config.payload_class:
            return True
        if existing.description != new_config.description:
            return True
        if existing.is_agent != new_config.is_agent:
            return True
        if set(existing.peers) != set(new_config.peers):
            return True
        if existing.broadcast != new_config.broadcast:
            return True
        if existing.cpu_bound != new_config.cpu_bound:
            return True
        return False


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

        # Parse process pool config
        pool = raw.get("process_pool", {})
        process_pool_enabled = pool.get("enabled", False) if pool else False
        process_pool_workers = pool.get("workers", 4) if pool else 4
        process_pool_max_tasks = pool.get("max_tasks_per_child", 100) if pool else 100

        # Parse backend config
        backend = raw.get("backend", {})
        backend_type = backend.get("type", "memory") if backend else "memory"
        backend_redis_url = backend.get("redis_url", "redis://localhost:6379") if backend else "redis://localhost:6379"
        backend_redis_prefix = backend.get("redis_prefix", "xp:") if backend else "xp:"

        config = OrganismConfig(
            name=org.get("name", "unnamed"),
            identity_path=org.get("identity", ""),
            port=org.get("port", 8765),
            thread_scheduling=raw.get("thread_scheduling", "breadth-first"),
            max_concurrent_pipelines=raw.get("max_concurrent_pipelines", 50),
            max_concurrent_handlers=raw.get("max_concurrent_handlers", 20),
            max_concurrent_per_agent=raw.get("max_concurrent_per_agent", 5),
            llm_config=raw.get("llm", {}),
            process_pool_enabled=process_pool_enabled,
            process_pool_workers=process_pool_workers,
            process_pool_max_tasks_per_child=process_pool_max_tasks,
            backend_type=backend_type,
            backend_redis_url=backend_redis_url,
            backend_redis_prefix=backend_redis_prefix,
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
            prompt=raw.get("prompt", ""),
            cpu_bound=raw.get("cpu_bound", False),
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
    """Load config, create pump, initialize root thread, and inject boot message."""
    from datetime import datetime, timezone
    from dotenv import load_dotenv
    from xml_pipeline.primitives import Boot, handle_boot
    from xml_pipeline.primitives import (
        TodoUntil, TodoComplete,
        handle_todo_until, handle_todo_complete,
    )
    from xml_pipeline.platform import get_prompt_registry

    # Load .env file if present
    load_dotenv()

    config = ConfigLoader.load(config_path)
    print(f"Organism: {config.name}")
    print(f"Listeners: {len(config.listeners)}")

    pump = StreamPump(config, config_path=config_path)

    # Register system listeners first
    boot_listener_config = ListenerConfig(
        name="system.boot",
        payload_class_path="xml_pipeline.primitives.Boot",
        handler_path="xml_pipeline.primitives.handle_boot",
        description="System boot handler - initializes organism",
        is_agent=False,
        payload_class=Boot,
        handler=handle_boot,
    )
    pump.register_listener(boot_listener_config)

    # Register TodoUntil handler (agents register watchers)
    todo_until_config = ListenerConfig(
        name="system.todo",
        payload_class_path="xml_pipeline.primitives.TodoUntil",
        handler_path="xml_pipeline.primitives.handle_todo_until",
        description="System todo handler - registers watchers",
        is_agent=False,
        payload_class=TodoUntil,
        handler=handle_todo_until,
    )
    pump.register_listener(todo_until_config)

    # Register TodoComplete handler (agents close watchers)
    todo_complete_config = ListenerConfig(
        name="system.todo-complete",
        payload_class_path="xml_pipeline.primitives.TodoComplete",
        handler_path="xml_pipeline.primitives.handle_todo_complete",
        description="System todo handler - closes watchers",
        is_agent=False,
        payload_class=TodoComplete,
        handler=handle_todo_complete,
    )
    pump.register_listener(todo_complete_config)

    # Register Sequence primitives (orchestration)
    from xml_pipeline.primitives.sequence import (
        SequenceStart, handle_sequence_start,
    )
    sequence_config = ListenerConfig(
        name="system.sequence",
        payload_class_path="xml_pipeline.primitives.sequence.SequenceStart",
        handler_path="xml_pipeline.primitives.sequence.handle_sequence_start",
        description="System sequence handler - chains listeners in order",
        is_agent=False,
        payload_class=SequenceStart,
        handler=handle_sequence_start,
    )
    pump.register_listener(sequence_config)

    # Register Buffer primitives (fan-out orchestration)
    from xml_pipeline.primitives.buffer import (
        BufferStart, handle_buffer_start,
    )
    buffer_config = ListenerConfig(
        name="system.buffer",
        payload_class_path="xml_pipeline.primitives.buffer.BufferStart",
        handler_path="xml_pipeline.primitives.buffer.handle_buffer_start",
        description="System buffer handler - fan-out to parallel workers",
        is_agent=False,
        payload_class=BufferStart,
        handler=handle_buffer_start,
    )
    pump.register_listener(buffer_config)

    # Register all user-defined listeners
    pump.register_all()

    # Load prompts into PromptRegistry (platform-managed, immutable)
    prompt_registry = get_prompt_registry()
    prompt_count = 0
    for listener in pump.listeners.values():
        if listener.is_agent:
            # Get prompt from config (may be empty)
            lc = next((l for l in config.listeners if l.name == listener.name), None)
            system_prompt = lc.prompt if lc else ""

            # Register prompt with peer schemas (usage_instructions)
            prompt_registry.register(
                agent_name=listener.name,
                system_prompt=system_prompt,
                peer_schemas=listener.usage_instructions,
            )
            prompt_count += 1

    # Freeze registry - no more registrations allowed
    prompt_registry.freeze()
    print(f"Prompts: {prompt_count} agents registered, registry frozen")

    # Configure LLM router if llm section present
    if config.llm_config:
        from xml_pipeline.llm import configure_router
        configure_router(config.llm_config)
        print(f"LLM backends: {len(config.llm_config.get('backends', []))}")

    # Initialize root thread in registry
    registry = get_registry()
    root_uuid = registry.initialize_root(config.name)
    print(f"Root thread: {root_uuid} ({registry.root_chain})")

    # Create and inject the boot message
    boot_payload = Boot(
        organism_name=config.name,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        listener_count=len(pump.listeners),
    )

    # Wrap boot payload in envelope
    boot_envelope = pump._wrap_in_envelope(
        payload=boot_payload,
        from_id="system",
        to_id="system.boot",
        thread_id=root_uuid,
    )

    # Inject boot message (will be processed when pump.run() is called)
    await pump.inject(boot_envelope, thread_id=root_uuid, from_id="system")

    # Set global pump instance for get_stream_pump()
    set_stream_pump(pump)

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


# ============================================================================
# Global Singleton
# ============================================================================

_pump: Optional[StreamPump] = None


def get_stream_pump() -> StreamPump:
    """
    Get the global StreamPump instance.

    The pump is initialized via bootstrap() and set here.
    Raises RuntimeError if called before bootstrap.
    """
    global _pump
    if _pump is None:
        raise RuntimeError(
            "StreamPump not initialized. Call bootstrap() first."
        )
    return _pump


def set_stream_pump(pump: StreamPump) -> None:
    """
    Set the global StreamPump instance.

    Called by bootstrap() after creating the pump.
    """
    global _pump
    _pump = pump


def reset_stream_pump() -> None:
    """
    Reset the global StreamPump instance.

    Useful for testing.
    """
    global _pump
    _pump = None
