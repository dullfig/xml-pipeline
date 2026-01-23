"""
FlowRunner — Execution bridge between BloxServer flows and xml-pipeline.

The FlowRunner:
1. Takes a Flow domain object
2. Converts it to xml-pipeline configuration
3. Creates and manages a StreamPump instance
4. Handles triggers (webhook, schedule, manual)
5. Tracks execution state and events

Usage:
    flow = Flow.from_canvas_json(canvas_data)
    runner = FlowRunner(flow)
    await runner.start()

    # Trigger execution
    await runner.trigger("my_webhook", payload={"query": "hello"})

    # Or inject directly to a node
    await runner.inject("researcher", {"query": "hello"})

    await runner.stop()
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from bloxserver.domain import Flow, AgentNode, ToolNode, GatewayNode
from bloxserver.domain.triggers import Trigger, TriggerType

logger = logging.getLogger(__name__)


# =============================================================================
# State & Events
# =============================================================================


class FlowRunnerState(str, Enum):
    """Lifecycle states for a FlowRunner."""

    CREATED = "created"  # Initial state
    STARTING = "starting"  # Registering listeners, preparing pump
    RUNNING = "running"  # Pump is active, accepting messages
    STOPPING = "stopping"  # Graceful shutdown in progress
    STOPPED = "stopped"  # Fully stopped
    ERROR = "error"  # Failed to start or crashed


@dataclass
class ExecutionEvent:
    """An event during flow execution (for logging/monitoring)."""

    timestamp: datetime
    event_type: str  # started, message_received, handler_called, error, stopped
    node_name: str | None = None
    thread_id: str | None = None
    payload_type: str | None = None
    message: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "eventType": self.event_type,
            "nodeName": self.node_name,
            "threadId": self.thread_id,
            "payloadType": self.payload_type,
            "message": self.message,
            "error": self.error,
        }


# =============================================================================
# FlowRunner
# =============================================================================


class FlowRunner:
    """
    Runs a BloxServer Flow using xml-pipeline's StreamPump.

    This is the bridge between the Flow domain model and the actual
    message pump execution. It handles:

    - Converting Flow nodes to xml-pipeline ListenerConfigs
    - Creating and managing the StreamPump lifecycle
    - Handling triggers (webhook, schedule, manual)
    - Tracking execution events for monitoring

    Note: For production use, each Flow runs in its own FlowRunner instance.
    Multiple FlowRunners can run concurrently (one per active flow).
    """

    def __init__(
        self,
        flow: Flow,
        port: int = 0,
        event_callback: Callable[[ExecutionEvent], None] | None = None,
    ):
        """
        Initialize a FlowRunner.

        Args:
            flow: The Flow domain object to run.
            port: Port for the organism (0 = auto-assign).
            event_callback: Optional callback for execution events.
        """
        self.flow = flow
        self.port = port
        self.event_callback = event_callback

        # State
        self.state = FlowRunnerState.CREATED
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.error_message: str | None = None

        # StreamPump instance (created on start)
        self._pump = None
        self._pump_task: asyncio.Task | None = None

        # Execution tracking
        self.execution_id = uuid4()
        self.events: list[ExecutionEvent] = []
        self.message_count = 0

        # Thread tracking (root thread per execution)
        self._root_thread_id: str | None = None

        # Trigger handlers (for webhook callbacks)
        self._trigger_handlers: dict[UUID, Callable] = {}

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """
        Start the flow runner.

        This creates the StreamPump, registers all listeners, and starts
        the message processing loop.
        """
        if self.state not in (FlowRunnerState.CREATED, FlowRunnerState.STOPPED):
            raise RuntimeError(f"Cannot start FlowRunner in state: {self.state}")

        self.state = FlowRunnerState.STARTING
        self.started_at = datetime.now(timezone.utc)
        self._emit_event("starting", message=f"Starting flow: {self.flow.name}")

        try:
            # Validate flow before starting
            errors = self.flow.validate()
            blocking_errors = [e for e in errors if e.severity == "error"]
            if blocking_errors:
                error_msg = "; ".join(e.message for e in blocking_errors)
                raise ValueError(f"Flow validation failed: {error_msg}")

            # Create the pump
            self._pump = await self._create_pump()

            # Start the pump in a background task
            self._pump_task = asyncio.create_task(self._run_pump())

            self.state = FlowRunnerState.RUNNING
            self._emit_event("started", message=f"Flow running: {len(self.flow.nodes)} nodes")

        except Exception as e:
            self.state = FlowRunnerState.ERROR
            self.error_message = str(e)
            self._emit_event("error", error=str(e))
            logger.exception(f"Failed to start flow {self.flow.id}: {e}")
            raise

    async def stop(self, timeout: float = 30.0) -> None:
        """
        Stop the flow runner gracefully.

        Args:
            timeout: Maximum time to wait for graceful shutdown.
        """
        if self.state not in (FlowRunnerState.RUNNING, FlowRunnerState.ERROR):
            return

        self.state = FlowRunnerState.STOPPING
        self._emit_event("stopping", message="Initiating graceful shutdown")

        try:
            if self._pump:
                await self._pump.shutdown()

            if self._pump_task:
                self._pump_task.cancel()
                try:
                    await asyncio.wait_for(self._pump_task, timeout=timeout)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            self.state = FlowRunnerState.STOPPED
            self.stopped_at = datetime.now(timezone.utc)
            self._emit_event("stopped", message=f"Flow stopped after {self.message_count} messages")

        except Exception as e:
            self.state = FlowRunnerState.ERROR
            self.error_message = str(e)
            self._emit_event("error", error=str(e))
            raise

    async def _run_pump(self) -> None:
        """Run the pump's main loop."""
        try:
            await self._pump.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.state = FlowRunnerState.ERROR
            self.error_message = str(e)
            self._emit_event("error", error=f"Pump crashed: {e}")
            logger.exception(f"Pump crashed for flow {self.flow.id}: {e}")

    # =========================================================================
    # Pump Creation
    # =========================================================================

    async def _create_pump(self):
        """Create and configure the StreamPump from the Flow."""
        from xml_pipeline.message_bus.stream_pump import (
            StreamPump,
            OrganismConfig,
            ListenerConfig,
        )
        from xml_pipeline.message_bus.thread_registry import get_registry

        # Build OrganismConfig
        org_config = self._build_organism_config()

        # Create pump
        pump = StreamPump(org_config)

        # Register system listeners (boot, todo)
        self._register_system_listeners(pump)

        # Register flow listeners
        for node in self.flow.nodes:
            listener_config = self._node_to_listener_config(node)
            if listener_config:
                pump.register_listener(listener_config)

        # Build usage instructions for agents
        pump.register_all()

        # Initialize root thread
        registry = get_registry()
        self._root_thread_id = registry.initialize_root(f"flow-{self.flow.id}")

        # Inject boot message
        await self._inject_boot_message(pump)

        return pump

    def _build_organism_config(self):
        """Build OrganismConfig from Flow settings."""
        from xml_pipeline.message_bus.stream_pump import OrganismConfig

        return OrganismConfig(
            name=f"flow-{self.flow.id}",
            port=self.port,
            max_concurrent_pipelines=50,
            max_concurrent_handlers=20,
            max_concurrent_per_agent=5,
            llm_config={
                "strategy": self.flow.settings.llm.strategy,
                "retries": self.flow.settings.llm.retries,
                "retry_base_delay": self.flow.settings.llm.retry_base_delay,
            },
        )

    def _register_system_listeners(self, pump) -> None:
        """Register system listeners (boot, todo) on the pump."""
        from xml_pipeline.message_bus.stream_pump import ListenerConfig
        from xml_pipeline.primitives import (
            Boot, handle_boot,
            TodoUntil, TodoComplete,
            handle_todo_until, handle_todo_complete,
        )

        # Boot handler
        pump.register_listener(ListenerConfig(
            name="system.boot",
            payload_class_path="xml_pipeline.primitives.Boot",
            handler_path="xml_pipeline.primitives.handle_boot",
            description="System boot handler",
            payload_class=Boot,
            handler=handle_boot,
        ))

        # TodoUntil handler
        pump.register_listener(ListenerConfig(
            name="system.todo",
            payload_class_path="xml_pipeline.primitives.TodoUntil",
            handler_path="xml_pipeline.primitives.handle_todo_until",
            description="System todo handler - registers watchers",
            payload_class=TodoUntil,
            handler=handle_todo_until,
        ))

        # TodoComplete handler
        pump.register_listener(ListenerConfig(
            name="system.todo-complete",
            payload_class_path="xml_pipeline.primitives.TodoComplete",
            handler_path="xml_pipeline.primitives.handle_todo_complete",
            description="System todo handler - closes watchers",
            payload_class=TodoComplete,
            handler=handle_todo_complete,
        ))

    def _node_to_listener_config(self, node):
        """Convert a domain Node to a ListenerConfig."""
        from xml_pipeline.message_bus.stream_pump import ListenerConfig
        from bloxserver.domain.edges import compute_peers

        node_map = {n.id: n.name for n in self.flow.nodes}

        if isinstance(node, AgentNode):
            # Agent node - needs dynamic handler
            peers = compute_peers(node.id, self.flow.edges, node_map)

            # For agents, we use a generic LLM handler
            # The prompt is passed via config
            handler, payload_class = self._get_agent_handler(node)

            return ListenerConfig(
                name=node.name,
                payload_class_path=f"bloxserver.runtime.handlers.{node.name}",
                handler_path=f"bloxserver.runtime.handlers.{node.name}",
                description=node.description,
                is_agent=True,
                peers=peers,
                prompt=node.prompt,
                payload_class=payload_class,
                handler=handler,
            )

        elif isinstance(node, ToolNode):
            # Tool node - use built-in or custom handler
            handler_path, payload_class_path = self._get_tool_paths(node)

            # Import the actual classes
            handler, payload_class = self._import_handler(handler_path, payload_class_path)

            return ListenerConfig(
                name=node.name,
                payload_class_path=payload_class_path,
                handler_path=handler_path,
                description=node.description,
                payload_class=payload_class,
                handler=handler,
            )

        elif isinstance(node, GatewayNode):
            # Gateway node - federation or REST
            # TODO: Implement gateway handlers
            logger.warning(f"Gateway nodes not yet implemented: {node.name}")
            return None

        return None

    def _get_agent_handler(self, node: AgentNode):
        """
        Get or create a handler for an agent node.

        Agents use a generic LLM completion handler that:
        1. Gets the prompt from PromptRegistry
        2. Builds conversation from ContextBuffer
        3. Calls LLM router
        4. Parses response and routes to peers
        """
        from dataclasses import dataclass
        from third_party.xmlable import xmlify

        # Create a dynamic payload class for this agent
        @xmlify
        @dataclass
        class AgentInput:
            """Input message for agent."""
            content: str

        # Create handler that uses the agent's prompt
        async def agent_handler(payload, metadata):
            from xml_pipeline.message_bus.message_state import HandlerResponse
            from xml_pipeline.platform import get_prompt_registry
            from xml_pipeline.llm import complete

            # Get the registered prompt
            prompt_registry = get_prompt_registry()
            prompt_data = prompt_registry.get(metadata.own_name)

            system_prompt = ""
            if prompt_data:
                system_prompt = prompt_data.full_prompt

            # Build messages for LLM
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload.content},
            ]

            # Call LLM
            try:
                response = await complete(
                    model=node.model or "grok-4.1",
                    messages=messages,
                    agent_id=metadata.own_name,
                    temperature=node.temperature,
                    max_tokens=node.max_tokens,
                )

                # For now, just echo response
                # TODO: Parse response for tool calls, routing
                @xmlify
                @dataclass
                class AgentOutput:
                    content: str

                return HandlerResponse(
                    payload=AgentOutput(content=response.content),
                    to=metadata.from_id,  # Reply to caller
                )

            except Exception as e:
                logger.error(f"Agent {metadata.own_name} LLM error: {e}")
                return None

        return agent_handler, AgentInput

    def _get_tool_paths(self, node: ToolNode) -> tuple[str, str]:
        """Get handler and payload class paths for a tool node."""
        from bloxserver.domain.nodes import BUILTIN_TOOLS

        if node.tool_type in BUILTIN_TOOLS:
            info = BUILTIN_TOOLS[node.tool_type]
            return info["handler"], info["payload_class"]

        # Custom tool
        if node.handler_path and node.payload_class_path:
            return node.handler_path, node.payload_class_path

        raise ValueError(f"Tool {node.name} has no handler configured")

    def _import_handler(self, handler_path: str, payload_class_path: str):
        """Import handler function and payload class."""
        # Import payload class
        mod_path, class_name = payload_class_path.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        payload_class = getattr(mod, class_name)

        # Import handler
        mod_path, fn_name = handler_path.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        handler = getattr(mod, fn_name)

        return handler, payload_class

    async def _inject_boot_message(self, pump) -> None:
        """Inject the boot message to start the organism."""
        from xml_pipeline.primitives import Boot

        boot_payload = Boot(
            organism_name=f"flow-{self.flow.id}",
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            listener_count=len(pump.listeners),
        )

        boot_envelope = pump._wrap_in_envelope(
            payload=boot_payload,
            from_id="system",
            to_id="system.boot",
            thread_id=self._root_thread_id,
        )

        await pump.inject(boot_envelope, thread_id=self._root_thread_id, from_id="system")

    # =========================================================================
    # Message Injection
    # =========================================================================

    async def inject(
        self,
        target_node: str,
        payload_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> str:
        """
        Inject a message to a specific node.

        Args:
            target_node: Name of the target node.
            payload_data: Payload data (will be wrapped in node's payload class).
            thread_id: Optional thread ID (creates new thread if not provided).

        Returns:
            The thread ID used for this message.
        """
        if self.state != FlowRunnerState.RUNNING:
            raise RuntimeError(f"Cannot inject: FlowRunner state is {self.state}")

        if not self._pump:
            raise RuntimeError("Pump not initialized")

        # Get the listener
        listener = self._pump.listeners.get(target_node)
        if not listener:
            raise ValueError(f"Unknown node: {target_node}")

        # Create payload instance
        payload = listener.payload_class(**payload_data)

        # Get or create thread
        from xml_pipeline.message_bus.thread_registry import get_registry
        registry = get_registry()

        if thread_id is None:
            thread_id = registry.extend_chain(self._root_thread_id, target_node)

        # Wrap in envelope
        envelope = self._pump._wrap_in_envelope(
            payload=payload,
            from_id="external",
            to_id=target_node,
            thread_id=thread_id,
        )

        # Inject
        await self._pump.inject(envelope, thread_id=thread_id, from_id="external")

        self.message_count += 1
        self._emit_event(
            "message_injected",
            node_name=target_node,
            thread_id=thread_id,
            payload_type=type(payload).__name__,
        )

        return thread_id

    # =========================================================================
    # Trigger Handling
    # =========================================================================

    async def trigger(
        self,
        trigger_id: UUID | str,
        payload_data: dict[str, Any] | None = None,
    ) -> str:
        """
        Fire a trigger to start flow execution.

        Args:
            trigger_id: The trigger ID (UUID or string).
            payload_data: Optional payload data for the trigger.

        Returns:
            The thread ID for this execution.
        """
        if isinstance(trigger_id, str):
            trigger_id = UUID(trigger_id)

        # Find the trigger
        trigger = next((t for t in self.flow.triggers if t.id == trigger_id), None)
        if not trigger:
            raise ValueError(f"Unknown trigger: {trigger_id}")

        if not trigger.enabled:
            raise ValueError(f"Trigger {trigger.name} is disabled")

        # Get target nodes
        if not trigger.target_node_ids:
            raise ValueError(f"Trigger {trigger.name} has no target nodes")

        # Find target node names
        node_map = {n.id: n.name for n in self.flow.nodes}
        target_names = [node_map.get(nid) for nid in trigger.target_node_ids]
        target_names = [n for n in target_names if n]

        if not target_names:
            raise ValueError(f"Trigger {trigger.name} targets unknown nodes")

        # Create thread for this execution
        from xml_pipeline.message_bus.thread_registry import get_registry
        registry = get_registry()
        thread_id = registry.extend_chain(self._root_thread_id, target_names[0])

        # Inject to each target
        for target_name in target_names:
            await self.inject(target_name, payload_data or {}, thread_id=thread_id)

        self._emit_event(
            "trigger_fired",
            message=f"Trigger {trigger.name} fired to {target_names}",
            thread_id=thread_id,
        )

        return thread_id

    def get_webhook_handler(self, trigger_id: UUID) -> Callable | None:
        """
        Get a webhook handler for a trigger.

        Used by the API layer to create webhook endpoints.
        """
        trigger = next((t for t in self.flow.triggers if t.id == trigger_id), None)
        if not trigger or trigger.trigger_type != TriggerType.WEBHOOK:
            return None

        async def webhook_handler(payload: dict[str, Any]) -> str:
            return await self.trigger(trigger_id, payload)

        return webhook_handler

    # =========================================================================
    # Status & Events
    # =========================================================================

    def get_status(self) -> dict[str, Any]:
        """Get current runner status."""
        return {
            "executionId": str(self.execution_id),
            "flowId": str(self.flow.id),
            "flowName": self.flow.name,
            "state": self.state.value,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "stoppedAt": self.stopped_at.isoformat() if self.stopped_at else None,
            "messageCount": self.message_count,
            "errorMessage": self.error_message,
            "nodeCount": len(self.flow.nodes),
            "triggerCount": len(self.flow.triggers),
        }

    def get_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent execution events."""
        return [e.to_dict() for e in self.events[-limit:]]

    def _emit_event(
        self,
        event_type: str,
        node_name: str | None = None,
        thread_id: str | None = None,
        payload_type: str | None = None,
        message: str = "",
        error: str | None = None,
    ) -> None:
        """Emit an execution event."""
        event = ExecutionEvent(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            node_name=node_name,
            thread_id=thread_id,
            payload_type=payload_type,
            message=message,
            error=error,
        )
        self.events.append(event)

        # Call external callback if provided
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception as e:
                logger.warning(f"Event callback error: {e}")

        # Log
        if error:
            logger.error(f"[{self.flow.name}] {event_type}: {error}")
        else:
            logger.info(f"[{self.flow.name}] {event_type}: {message or node_name or ''}")
