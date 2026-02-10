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
import logging
import sys
from typing import AsyncIterable, Callable, List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xml_pipeline.crypto.identity import Identity

from lxml import etree
from aiostream import stream, pipe, operator

# Import existing step implementations (we'll wrap them)
from xml_pipeline.message_bus.steps.repair import repair_step
from xml_pipeline.message_bus.steps.c14n import c14n_step
from xml_pipeline.message_bus.steps.envelope_validation import envelope_validation_step
from xml_pipeline.message_bus.steps.payload_extraction import payload_extraction_step
from xml_pipeline.message_bus.steps.thread_assignment import thread_assignment_step
from xml_pipeline.message_bus.message_state import MessageState, HandlerMetadata, HandlerResponse, SystemError, ROUTING_ERROR, TIMEOUT_ERROR
from xml_pipeline.message_bus.thread_registry import get_registry
from xml_pipeline.message_bus.todo_registry import get_todo_registry
from xml_pipeline.message_bus.budget_registry import get_budget_registry
from xml_pipeline.memory import get_context_buffer

pump_logger = logging.getLogger(__name__)


from xml_pipeline.message_bus.events import (
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
    ReloadEvent,
    EventCallback,
)
from xml_pipeline.message_bus.pump_config import (
    ListenerConfig,
    OrganismConfig,
    Listener,
)
from xml_pipeline.message_bus.pipeline import (
    wrap_step,
    extract_payloads,
    make_xsd_validation,
    make_deserialization,
)
from xml_pipeline.message_bus.config_loader import ConfigLoader
from xml_pipeline.message_bus.singleton import set_stream_pump


# ============================================================================
# The Stream-Based Pump
# ============================================================================

class StreamPump:
    """
    Message pump built on aiostream.

    The entire flow is a single composable stream pipeline.
    Fan-out is natural via flatmap. Concurrency is controlled via task_limit.
    """

    def __init__(
        self,
        config: OrganismConfig | None = None,
        config_path: str = "",
        *,
        name: str = "organism",
        port: int = 8765,
        max_tokens_per_thread: int = 100_000,
        max_concurrent_handlers: int = 20,
        max_concurrent_per_agent: int = 5,
        llm_config: Dict[str, Any] | None = None,
        identity_path: str = "",
        oob_enabled: bool = False,
        oob_bind: str = "127.0.0.1",
        oob_port: int = 8766,
    ):
        # Build config from kwargs if not provided directly
        if config is None:
            config = OrganismConfig(
                name=name,
                identity_path=identity_path,
                port=port,
                max_concurrent_handlers=max_concurrent_handlers,
                max_concurrent_per_agent=max_concurrent_per_agent,
                max_tokens_per_thread=max_tokens_per_thread,
                llm_config=llm_config or {},
                oob_enabled=oob_enabled,
                oob_bind=oob_bind,
                oob_port=oob_port,
            )

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

        # OOB server (started in start(), stopped in shutdown())
        self._oob_server: Any = None
        self._start_time: float = 0.0

        # Peer tables: named routing tables for thread-scoped privilege enforcement
        # { table_name: { listener_name: [allowed_peers] } }
        self._peer_tables: Dict[str, Dict[str, List[str]]] = {}
        # { table_name: { listener_name: usage_instructions_str } }
        self._table_usage_instructions: Dict[str, Dict[str, str]] = {}
        # { table_name: parent_name | None }
        self._peer_table_parents: Dict[str, Optional[str]] = {}

        # Worker registry for background processes
        from xml_pipeline.workers import WorkerRegistry
        self._worker_registry = WorkerRegistry()

        # Port gate (initialized in start())
        self._port_gate: Any = None

        # WASM registry (initialized in start() if tools declared)
        self._wasm_registry: Any = None

        # Shell worker (OS-isolated execution via xp-exec)
        self._shell_worker_id: Optional[str] = None
        self._shell_config: Dict[str, Any] = {}

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
    # Thread Lifecycle
    # ------------------------------------------------------------------

    def _cleanup_thread(self, thread_id: str) -> None:
        """
        Consolidate all cleanup when a thread terminates.

        Called when:
        - Handler returns None (chain terminates)
        - Chain exhausted after .respond() (nowhere to return)
        """
        # Budget cleanup
        budget_registry = get_budget_registry()
        final_budget = budget_registry.cleanup_thread(thread_id)
        if final_budget:
            pump_logger.debug(
                f"Thread {thread_id[:8]}... completed: "
                f"{final_budget.total_tokens} tokens used"
            )

        # Todo watcher cleanup
        todo_registry = get_todo_registry()
        closed = todo_registry.close_all_for_thread(thread_id)
        if closed:
            pump_logger.debug(
                f"Thread {thread_id[:8]}... closed {closed} todo watcher(s)"
            )

        # Worker cleanup
        if self._worker_registry:
            stopped = self._worker_registry.cleanup_for_thread(thread_id)
            if stopped:
                pump_logger.debug(
                    f"Thread {thread_id[:8]}... stopped {stopped} worker(s)"
                )

        # WASM instance cleanup
        if self._wasm_registry:
            wasm_cleaned = self._wasm_registry.cleanup_for_thread(thread_id)
            if wasm_cleaned:
                pump_logger.debug(
                    f"Thread {thread_id[:8]}... released {wasm_cleaned} WASM instance(s)"
                )

        # Emit thread completion event
        self._emit_event(ThreadEvent(
            thread_id=thread_id,
            status="completed",
        ))

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
            timeout=lc.timeout,
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

    def register(
        self,
        name: str,
        handler: Callable,
        payload_class: type,
        *,
        description: str = "",
        agent: bool = False,
        peers: List[str] | None = None,
        broadcast: bool = False,
        prompt: str = "",
        timeout: float = 30.0,
    ) -> Listener:
        """
        Register a listener using plain arguments (public API).

        This is the preferred way to register listeners programmatically.
        Internally constructs a ListenerConfig and delegates to
        register_listener().

        Args:
            name: Unique listener name (e.g., "greeter", "calculator.add")
            handler: Async handler function
            payload_class: @xmlify dataclass defining the message contract
            description: Human-readable description for tool prompts
            agent: Whether this is an LLM agent (gets own_name, unique root tag)
            peers: Allowed call targets for agents
            broadcast: Allow multiple listeners to share root tag
            prompt: System prompt for LLM agents
            timeout: Handler execution timeout in seconds (default 30)

        Returns:
            The registered Listener object
        """
        # Derive import paths from the live objects
        payload_class_path = f"{payload_class.__module__}.{payload_class.__qualname__}"
        handler_path = f"{handler.__module__}.{handler.__qualname__}"

        lc = ListenerConfig(
            name=name,
            payload_class_path=payload_class_path,
            handler_path=handler_path,
            description=description,
            is_agent=agent,
            peers=peers or [],
            broadcast=broadcast,
            prompt=prompt,
            timeout=timeout,
            payload_class=payload_class,
            handler=handler,
        )
        return self.register_listener(lc)

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

    def _build_usage_instructions(
        self, agent: Listener, peers_override: List[str] | None = None
    ) -> str:
        """
        Build LLM system prompt instructions from peer schemas.

        Generates human-readable documentation of what messages
        this agent can send to its peers.

        Args:
            agent: The agent listener
            peers_override: If provided, use this peers list instead of agent.peers
        """
        peers = peers_override if peers_override is not None else agent.peers
        lines = [
            f"You are the {agent.name} agent.",
            f"Description: {agent.description}",
            "",
            "You can send messages to the following peers:",
        ]

        for peer_name in peers:
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

    # ------------------------------------------------------------------
    # Peer Tables
    # ------------------------------------------------------------------

    def register_peer_table(
        self,
        name: str,
        peers: Dict[str, List[str]],
        parent: Optional[str] = None,
    ) -> None:
        """
        Register a named peer table (privilege tier).

        Peer tables override ``Listener.peers`` for threads that use this table.
        All dispatch enforcement for those threads uses the table's peer
        definitions instead of the listener's static peers list.

        **Ceiling enforcement:** Every peer in the table must exist in the
        parent's peer list (or in the listener's static ``peers`` from YAML
        if no parent). Tables can only subtract from the ceiling, never expand.

        Args:
            name: Table name (e.g., "premium", "basic")
            peers: Mapping of listener_name -> [allowed_peers]
            parent: Parent table name (None = inherit ceiling from YAML listener.peers)

        Raises:
            KeyError: If table name already exists
            ValueError: If any peer exceeds the ceiling
        """
        if name in self._peer_tables:
            raise KeyError(f"Peer table '{name}' already registered")

        if parent is not None and parent not in self._peer_tables:
            raise KeyError(f"Parent table '{parent}' not registered")

        # Validate ceiling: every peer must exist in parent's ceiling
        for listener_name, peer_list in peers.items():
            ceiling = self._get_peer_ceiling(listener_name, parent)
            for p in peer_list:
                if p not in ceiling:
                    source = f"table '{parent}'" if parent else "YAML listener.peers"
                    raise ValueError(
                        f"Peer '{p}' for '{listener_name}' in table '{name}' "
                        f"exceeds ceiling from {source}. "
                        f"Ceiling: {ceiling}"
                    )

        self._peer_tables[name] = dict(peers)
        self._peer_table_parents[name] = parent

        # Initialize table root in thread registry
        registry = get_registry()
        registry.initialize_table_root(name, self.config.name)

        # Pre-build usage_instructions for agents in this table
        table_instructions: Dict[str, str] = {}
        for listener_name, peer_list in peers.items():
            listener = self.listeners.get(listener_name)
            if listener and listener.is_agent:
                table_instructions[listener_name] = self._build_usage_instructions(
                    listener, peers_override=peer_list
                )
        self._table_usage_instructions[name] = table_instructions

        pump_logger.info(f"Peer table '{name}' registered: {list(peers.keys())}")

    def _get_peer_ceiling(
        self, listener_name: str, parent_table: Optional[str]
    ) -> List[str]:
        """
        Resolve the peer ceiling for a listener.

        If parent_table is set and the parent has an entry for this listener,
        use the parent's peer list. Otherwise fall back to the listener's
        static peers from YAML registration.
        """
        if parent_table and parent_table in self._peer_tables:
            parent_peers = self._peer_tables[parent_table].get(listener_name)
            if parent_peers is not None:
                return parent_peers
        listener = self.listeners.get(listener_name)
        return list(listener.peers) if listener else []

    def modify_peer_table(
        self,
        name: str,
        listener_name: str,
        *,
        grant: List[str] | None = None,
        revoke: List[str] | None = None,
    ) -> List[str]:
        """
        Modify a single listener's peers within a table.

        **Ceiling enforcement on grants:** Granted peers must exist in the
        parent table's ceiling (or YAML listener.peers). You can restore
        revoked peers up to the ceiling, but never exceed it. Revocations
        always succeed (subtraction is always valid).

        Args:
            name: Table name
            listener_name: Listener to modify
            grant: Peer names to add
            revoke: Peer names to remove

        Returns:
            The updated peers list for this listener in this table.

        Raises:
            KeyError: If table does not exist.
            ValueError: If any granted peer exceeds the ceiling.
        """
        if name not in self._peer_tables:
            raise KeyError(f"Peer table '{name}' not registered")

        current = list(self._peer_tables[name].get(listener_name, []))

        # Validate grants against ceiling
        if grant:
            parent = self._peer_table_parents.get(name)
            ceiling = self._get_peer_ceiling(listener_name, parent)
            for p in grant:
                if p not in ceiling:
                    source = f"table '{parent}'" if parent else "YAML listener.peers"
                    raise ValueError(
                        f"Cannot grant '{p}' to '{listener_name}' in table '{name}': "
                        f"exceeds ceiling from {source}. Ceiling: {ceiling}"
                    )

            for peer in grant:
                if peer not in current:
                    current.append(peer)

        if revoke:
            current = [p for p in current if p not in revoke]

        self._peer_tables[name][listener_name] = current

        # Rebuild usage_instructions for this listener in this table
        listener = self.listeners.get(listener_name)
        if listener and listener.is_agent:
            self._table_usage_instructions.setdefault(name, {})[listener_name] = (
                self._build_usage_instructions(listener, peers_override=current)
            )

        # Emit reload event
        self._emit_event(ReloadEvent(
            success=True,
            updated_listeners=[f"{name}:{listener_name}"],
        ))

        pump_logger.info(
            f"Peer table '{name}' modified: {listener_name} -> {current}"
        )
        return current

    def _register_config_peer_tables(self) -> None:
        """
        Register peer tables from YAML config in topological order.

        Parents are registered before children. Circular parent chains
        are detected and raise ValueError.
        """
        configs = {pt["name"]: pt for pt in self.config.peer_table_configs}
        registered: set[str] = set()
        in_progress: set[str] = set()  # For cycle detection

        def _register(name: str) -> None:
            if name in registered:
                return
            if name in in_progress:
                raise ValueError(
                    f"Circular peer table parent chain detected involving '{name}'"
                )
            if name not in configs:
                raise KeyError(f"Peer table '{name}' referenced as parent but not defined")

            in_progress.add(name)
            pt = configs[name]
            parent = pt.get("parent")
            if parent:
                _register(parent)
            in_progress.discard(name)

            self.register_peer_table(name, pt["peers"], parent=parent)
            registered.add(name)

        for pt in self.config.peer_table_configs:
            _register(pt["name"])

        if registered:
            pump_logger.info(
                f"Registered {len(registered)} peer table(s) from config: "
                f"{sorted(registered)}"
            )

    def _generate_schema(self, payload_class: type) -> etree.XMLSchema:
        """Generate XSD schema from xmlified payload class."""
        if hasattr(payload_class, 'xsd'):
            xsd_tree = payload_class.xsd()
            return etree.XMLSchema(xsd_tree)
        # Fallback for non-xmlified classes (e.g., in tests)
        permissive = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:any processContents="lax"/></xs:schema>'
        return etree.XMLSchema(etree.fromstring(permissive.encode()))

    # ------------------------------------------------------------------
    # Public API: start() — full bootstrap ceremony
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Run the full bootstrap ceremony (public API).

        This performs the full initialization sequence after pump
        creation:
        1. Register system listeners (Boot, Todo, Sequence, Buffer)
        2. Build usage_instructions for all agents (second pass)
        3. Load prompts into PromptRegistry and freeze it
        4. Configure LLM router (if llm_config provided)
        5. Configure ThreadBudgetRegistry
        6. Initialize root thread
        7. Inject boot message

        After this, call ``await pump.run()`` to start processing.
        """
        from datetime import datetime, timezone
        from xml_pipeline.primitives import (
            Boot, handle_boot,
            TodoUntil, TodoComplete,
            handle_todo_until, handle_todo_complete,
        )
        from xml_pipeline.primitives.sequence import (
            SequenceStart, handle_sequence_start,
        )
        from xml_pipeline.primitives.buffer import (
            BufferStart, handle_buffer_start,
        )
        from xml_pipeline.platform import get_prompt_registry
        from xml_pipeline.message_bus.budget_registry import configure_budget_registry

        # --- 1. System listeners ---
        system_listeners = [
            ("system.boot", Boot, handle_boot,
             "System boot handler - initializes organism"),
            ("system.todo", TodoUntil, handle_todo_until,
             "System todo handler - registers watchers"),
            ("system.todo-complete", TodoComplete, handle_todo_complete,
             "System todo handler - closes watchers"),
            ("system.sequence", SequenceStart, handle_sequence_start,
             "System sequence handler - chains listeners in order"),
            ("system.buffer", BufferStart, handle_buffer_start,
             "System buffer handler - fan-out to parallel workers"),
        ]
        for sys_name, cls, handler, desc in system_listeners:
            if sys_name not in self.listeners:
                self.register(sys_name, handler, cls, description=desc)

        # --- 1b. WASM tools (before usage_instructions so they appear in peer schemas) ---
        if self.config.wasm_tool_configs:
            from xml_pipeline.wasm import register_wasm_tool, WasmRegistry, set_wasm_registry
            self._wasm_registry = WasmRegistry()
            set_wasm_registry(self._wasm_registry)
            for tool_cfg in self.config.wasm_tool_configs:
                register_wasm_tool(self, tool_cfg)

        # --- 2. Build usage_instructions (needs all listeners registered) ---
        for listener in self.listeners.values():
            if listener.is_agent and listener.peers:
                listener.usage_instructions = self._build_usage_instructions(listener)

        # --- 3. Prompts ---
        prompt_registry = get_prompt_registry()
        for listener in self.listeners.values():
            if listener.is_agent:
                # Find prompt from the config's listener list (if available)
                lc = next(
                    (l for l in self.config.listeners if l.name == listener.name),
                    None,
                )
                system_prompt = lc.prompt if lc else ""
                prompt_registry.register(
                    agent_name=listener.name,
                    system_prompt=system_prompt,
                    peer_schemas=listener.usage_instructions,
                )
        prompt_registry.freeze()

        # --- 4. LLM router ---
        if self.config.llm_config:
            from xml_pipeline.llm import configure_router
            configure_router(self.config.llm_config)

        # --- 5. Budget ---
        configure_budget_registry(self.config.max_tokens_per_thread)

        # --- 6. Root thread ---
        registry = get_registry()
        root_uuid = registry.initialize_root(self.config.name)

        # --- 7. Boot message ---
        boot_payload = Boot(
            organism_name=self.config.name,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            listener_count=len(self.listeners),
        )
        boot_envelope = self._wrap_in_envelope(
            payload=boot_payload,
            from_id="system",
            to_id="system.boot",
            thread_id=root_uuid,
        )
        await self._inject_raw(boot_envelope, thread_id=root_uuid, from_id="system")

        # Set global singleton
        set_stream_pump(self)

        # --- 8. Network port gate ---
        from xml_pipeline.network.port_gate import PortAllocation, PortGate, set_port_gate
        allocations = [
            PortAllocation(
                port=p["port"],
                bind=p.get("bind", "127.0.0.1"),
                listener=p.get("listener", ""),
                protocol=p.get("protocol", "tcp"),
            )
            for p in self.config.network_ports
        ]
        self._port_gate = PortGate(allocations)
        set_port_gate(self._port_gate)

        # --- 9. OOB privileged channel ---
        import time as _time
        self._start_time = _time.time()
        if self.config.oob_enabled:
            try:
                # Resolve TOTP secret from environment variable
                totp_secret: Optional[str] = None
                if self.config.auth_totp_secret_env:
                    import os
                    totp_secret = os.environ.get(self.config.auth_totp_secret_env)
                    if self.config.auth_totp_required and not totp_secret:
                        pump_logger.warning(
                            f"TOTP required but {self.config.auth_totp_secret_env} "
                            f"not set — OOB connections will be rejected"
                        )
                    elif totp_secret:
                        pump_logger.info("OOB TOTP authentication enabled")

                from xml_pipeline.oob import OOBServer
                self._oob_server = OOBServer(
                    pump=self,
                    bind=self.config.oob_bind,
                    port=self.config.oob_port,
                    identity=self.identity,
                    totp_secret=totp_secret,
                )
                await self._oob_server.start()
            except Exception as e:
                pump_logger.warning(f"OOB server failed to start: {e}")
                self._oob_server = None

        # --- 10. Peer tables from config ---
        if self.config.peer_table_configs:
            self._register_config_peer_tables()

        # --- 11. Shell worker (OS-isolated execution) ---
        if self.config.shell_config.get("enabled") and sys.platform == "linux":
            self._start_shell_worker()

        pump_logger.info(
            f"Organism '{self.config.name}' started: "
            f"{len(self.listeners)} listeners, root={root_uuid}"
        )

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

                    # === PEER TABLE: Resolve effective usage_instructions ===
                    # If the thread belongs to a peer table, use table-specific
                    # instructions instead of the listener's static ones.
                    table_name = registry.get_table_for_thread(current_thread) if current_thread else None
                    if (table_name and table_name in self._table_usage_instructions
                            and listener.name in self._table_usage_instructions[table_name]):
                        effective_usage_instructions = self._table_usage_instructions[table_name][listener.name]
                    else:
                        effective_usage_instructions = listener.usage_instructions

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
                                usage_instructions=effective_usage_instructions,
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
                            usage_instructions=effective_usage_instructions,
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

                    # Dispatch to handler with per-listener timeout
                    try:
                        response = await asyncio.wait_for(
                            listener.handler(payload_ref, metadata),
                            timeout=listener.timeout,
                        )
                    except asyncio.TimeoutError:
                        pump_logger.error(
                            f"Handler {listener.name} timed out after {listener.timeout}s"
                        )
                        self._emit_event(AgentStateEvent(
                            agent_name=listener.name,
                            state="error",
                            thread_id=current_thread,
                        ))
                        # Send SystemError back to caller (keeps thread alive)
                        error_bytes = self._wrap_in_envelope(
                            payload=TIMEOUT_ERROR,
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

                    # None means "no response needed" - don't re-inject
                    if response is None:
                        # Thread terminates here - consolidated cleanup
                        self._cleanup_thread(current_thread)

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
                                # Chain exhausted - consolidated cleanup
                                self._cleanup_thread(current_thread)
                                continue
                            to_id = target
                            thread_id = new_thread_id
                        else:
                            # Forward to named target - validate against peers
                            requested_to = response.to

                            # Resolve effective peers: table overrides listener.peers
                            if (table_name and table_name in self._peer_tables
                                    and listener.name in self._peer_tables[table_name]):
                                effective_peers = self._peer_tables[table_name][listener.name]
                            else:
                                effective_peers = listener.peers

                            # Enforce peer constraints for agents
                            if listener.is_agent and effective_peers:
                                if requested_to not in effective_peers:
                                    # Agent trying to send to non-peer - send generic error back to agent
                                    # Log details internally but don't reveal to agent
                                    import logging
                                    logging.getLogger(__name__).warning(
                                        f"Peer violation: {listener.name} -> {requested_to} (allowed: {effective_peers})"
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

    async def inject(
        self,
        target: str,
        payload: Any,
        *,
        from_id: str = "external",
        thread_id: str | None = None,
        routing_table: str | None = None,
    ) -> str:
        """
        Inject a message into the pump (public API).

        Args:
            target: Target listener name.
            payload: @xmlify dataclass instance.
            from_id: Sender identity (default "external").
            thread_id: Thread UUID. Created automatically if not provided.
            routing_table: Named peer table for thread-scoped privilege
                enforcement. When specified, the thread chain starts with
                the table name prefix instead of ``system``, and all
                dispatch enforcement for this thread (and sub-threads) uses
                the table's peer definitions.

        Returns:
            The thread_id used for the message.

        Examples:
            await pump.inject("greeter", Greeting(name="Alice"))

            await pump.inject("greeter", Greeting(name="Alice"),
                              thread_id=my_thread)

            await pump.inject("concierge", Greeting(name="Alice"),
                              routing_table="premium")
        """

        if thread_id is None:
            registry = get_registry()
            if routing_table and routing_table in self._peer_tables:
                # Use table root instead of system root
                table_root = registry.get_table_root(routing_table)
                if table_root is None:
                    # Auto-initialize table root if not yet done
                    table_root = registry.initialize_table_root(
                        routing_table, self.config.name
                    )
                thread_id = registry.extend_chain(table_root, from_id)
                thread_id = registry.extend_chain(thread_id, target)
            else:
                thread_id = registry.extend_chain(
                    registry.root_uuid or "",
                    target,
                )

        envelope_bytes = self._wrap_in_envelope(
            payload=payload,
            from_id=from_id,
            to_id=target,
            thread_id=thread_id,
        )
        await self._inject_raw(envelope_bytes, thread_id=thread_id, from_id=from_id)
        return thread_id

    async def _inject_raw(self, raw_bytes: bytes, thread_id: str, from_id: str) -> None:
        """Inject raw envelope bytes into the pump (internal API)."""
        state = MessageState(
            raw_bytes=raw_bytes,
            thread_id=thread_id,
            from_id=from_id,
        )
        await self.queue.put(state)

    # ------------------------------------------------------------------
    # Shell Worker
    # ------------------------------------------------------------------

    def _start_shell_worker(self) -> None:
        """Start the shell worker process for OS-isolated execution."""
        import os
        from xml_pipeline.tools.shell_worker import shell_worker_main

        shell_cfg = self.config.shell_config

        # Build table_os_users mapping from peer table configs
        table_os_users: Dict[str, str] = {}
        for pt in self.config.peer_table_configs:
            os_user = pt.get("os_user")
            if os_user:
                table_os_users[pt["name"]] = os_user

        # Resolve TOTP secret
        totp_secret = ""
        totp_env = shell_cfg.get("totp_secret_env", "")
        if totp_env:
            totp_secret = os.environ.get(totp_env, "")
        if not totp_secret:
            # Auto-generate a TOTP secret for this session
            from xml_pipeline.crypto.totp import generate_secret
            totp_secret = generate_secret()

        xp_exec_path = shell_cfg.get("xp_exec_path", "/usr/local/bin/xp-exec")

        # Use a synthetic thread_id for the shell worker (system-level)
        self._shell_worker_id = self._worker_registry.spawn(
            thread_id="system.shell",
            listener_name="system.shell",
            target=shell_worker_main,
            kwargs={
                "totp_secret": totp_secret,
                "xp_exec_path": xp_exec_path,
            },
        )

        self._shell_config = {
            "enabled": True,
            "default_os_user": shell_cfg.get("default_os_user", ""),
            "xp_exec_path": xp_exec_path,
            "table_os_users": table_os_users,
        }

        pump_logger.info(
            f"Shell worker started (worker_id={self._shell_worker_id[:8]}...)"
        )

    async def _shell_execute(
        self,
        command: str,
        os_user: str,
        timeout: int = 30,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a command via the shell worker process.

        Sends a request to the worker's inbox and polls the outbox for
        the matching response.

        Args:
            command: Command to execute
            os_user: Target OS user for privilege isolation
            timeout: Command timeout in seconds
            cwd: Working directory (optional)
            env: Environment variables (optional)

        Returns:
            Response dict with exit_code, stdout, stderr, timed_out, error
        """
        import uuid as _uuid

        if not self._shell_worker_id:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "error": "Shell worker not running",
            }

        request_id = str(_uuid.uuid4())
        request = {
            "request_id": request_id,
            "command": command,
            "os_user": os_user,
            "timeout": timeout,
        }
        if cwd:
            request["cwd"] = cwd
        if env:
            request["env"] = env

        # Send to worker
        self._worker_registry.send(self._shell_worker_id, request)

        # Poll for matching response
        deadline = asyncio.get_event_loop().time() + timeout + 10
        while asyncio.get_event_loop().time() < deadline:
            messages = self._worker_registry.receive(self._shell_worker_id)
            for msg in messages:
                if isinstance(msg, dict) and msg.get("request_id") == request_id:
                    return msg
            await asyncio.sleep(0.1)

        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "error": "Shell worker response timeout",
        }

    async def shutdown(self) -> None:
        """Graceful shutdown — wait for queue to drain and close resources."""
        # Stop OOB server first
        if self._oob_server:
            await self._oob_server.stop()
            self._oob_server = None

        self._running = False
        await self.queue.join()

        # Release port gate
        if self._port_gate:
            self._port_gate.shutdown()
            from xml_pipeline.network.port_gate import reset_port_gate
            reset_port_gate()
            self._port_gate = None

        # Shutdown WASM registry
        if self._wasm_registry:
            self._wasm_registry.shutdown_all()
            from xml_pipeline.wasm import reset_wasm_registry
            reset_wasm_registry()
            self._wasm_registry = None

        # Clear shell worker reference (process stopped by shutdown_all)
        self._shell_worker_id = None
        self._shell_config = {}

        # Stop all background workers
        if self._worker_registry:
            self._worker_registry.shutdown_all()

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
        return False

    # ------------------------------------------------------------------
    # Public API: from_yaml() — construct pump from config file
    # ------------------------------------------------------------------

    @classmethod
    async def from_yaml(cls, config_path: str = "config/organism.yaml") -> "StreamPump":
        """
        Create a fully initialized pump from a YAML config file.

        This is the highest-level entry point. It:
        1. Loads and parses the config file
        2. Creates the StreamPump
        3. Registers all user-defined listeners from config
        4. Calls start() (system listeners, prompts, boot, etc.)

        After this, call ``await pump.run()`` to start processing.

        Args:
            config_path: Path to organism.yaml

        Returns:
            A fully bootstrapped StreamPump, ready for run().
        """
        from dotenv import load_dotenv

        load_dotenv()

        config = ConfigLoader.load(config_path)
        pump = cls(config=config, config_path=config_path)
        pump.register_all()
        await pump.start()
        return pump


