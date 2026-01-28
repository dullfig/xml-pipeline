"""
state.py — Server state manager for tracking organism runtime state.

Maintains real-time state for API queries:
- Agent states (idle, processing, waiting, error)
- Active threads and their participants
- Message counts and activity timestamps

This is the bridge between the StreamPump and the API.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from xml_pipeline.server.models import (
    AgentInfo,
    AgentState,
    CapabilityDetail,
    CapabilityInfo,
    MessageInfo,
    OrganismInfo,
    OrganismStatus,
    ThreadInfo,
    ThreadStatus,
)

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump
    from xml_pipeline.message_bus import (
        PumpEvent,
        MessageReceivedEvent,
        MessageSentEvent,
        AgentStateEvent,
        ThreadEvent,
    )


@dataclass
class AgentRuntimeState:
    """Runtime state for a single agent."""

    name: str
    description: str
    is_agent: bool
    peers: List[str]
    payload_class: str
    state: AgentState = AgentState.IDLE
    current_thread: Optional[str] = None
    last_activity: Optional[datetime] = None
    message_count: int = 0
    queue_depth: int = 0


@dataclass
class ThreadRuntimeState:
    """Runtime state for a single thread."""

    id: str
    status: ThreadStatus = ThreadStatus.ACTIVE
    participants: Set[str] = field(default_factory=set)
    message_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: Optional[datetime] = None
    error: Optional[str] = None


@dataclass
class MessageRecord:
    """Record of a message for history."""

    id: str
    thread_id: str
    from_id: str
    to_id: str
    payload_type: str
    payload: Dict[str, Any]
    timestamp: datetime
    slot_index: int


class ServerState:
    """
    Centralized state manager for the API server.

    Tracks runtime state of agents, threads, and messages.
    Provides methods for the API to query current state.
    """

    def __init__(self, pump: StreamPump) -> None:
        self.pump = pump
        self._start_time = time.time()
        self._status = OrganismStatus.STARTING

        # Runtime state
        self._agents: Dict[str, AgentRuntimeState] = {}
        self._threads: Dict[str, ThreadRuntimeState] = {}
        self._messages: List[MessageRecord] = []
        self._message_count = 0

        # Event subscribers (WebSocket connections)
        self._subscribers: Set[Callable] = set()

        # Lock for thread-safe updates
        self._lock = asyncio.Lock()

        # Initialize agent states from pump
        self._init_agents()

        # Subscribe to pump events
        self._subscribe_to_pump()

    def _init_agents(self) -> None:
        """Initialize agent states from pump listeners."""
        for name, listener in self.pump.listeners.items():
            self._agents[name] = AgentRuntimeState(
                name=name,
                description=listener.description,
                is_agent=listener.is_agent,
                peers=list(listener.peers),
                payload_class=f"{listener.payload_class.__module__}.{listener.payload_class.__name__}",
            )

    def _subscribe_to_pump(self) -> None:
        """Subscribe to pump events for real-time state updates."""
        from xml_pipeline.message_bus import (
            PumpEvent,
            MessageReceivedEvent,
            MessageSentEvent,
            AgentStateEvent as PumpAgentStateEvent,
            ThreadEvent,
        )

        def handle_pump_event(event: "PumpEvent") -> None:
            """Handle pump events synchronously (schedules async updates)."""
            if isinstance(event, MessageReceivedEvent):
                # Schedule async message recording
                asyncio.create_task(self._handle_message_received(event))
            elif isinstance(event, MessageSentEvent):
                # Schedule async message recording
                asyncio.create_task(self._handle_message_sent(event))
            elif isinstance(event, PumpAgentStateEvent):
                # Schedule async agent state update
                asyncio.create_task(self._handle_agent_state(event))
            elif isinstance(event, ThreadEvent):
                # Schedule async thread update
                asyncio.create_task(self._handle_thread_event(event))

        self.pump.subscribe_events(handle_pump_event)

    async def _handle_message_received(self, event: "MessageReceivedEvent") -> None:
        """Handle message received by handler."""
        # Convert payload to dict representation
        payload_dict = {}
        if hasattr(event.payload, '__dataclass_fields__'):
            import dataclasses
            payload_dict = dataclasses.asdict(event.payload)
        elif isinstance(event.payload, dict):
            payload_dict = event.payload

        await self.record_message(
            thread_id=event.thread_id,
            from_id=event.from_id,
            to_id=event.to_id,
            payload_type=event.payload_type,
            payload=payload_dict,
        )

    async def _handle_message_sent(self, event: "MessageSentEvent") -> None:
        """Handle message sent by handler."""
        payload_dict = {}
        if hasattr(event.payload, '__dataclass_fields__'):
            import dataclasses
            payload_dict = dataclasses.asdict(event.payload)
        elif isinstance(event.payload, dict):
            payload_dict = event.payload

        await self.record_message(
            thread_id=event.thread_id,
            from_id=event.from_id,
            to_id=event.to_id,
            payload_type=event.payload_type,
            payload=payload_dict,
        )

    async def _handle_agent_state(self, event: "AgentStateEvent") -> None:
        """Handle agent state change from pump."""
        # Map pump state strings to AgentState enum
        state_map = {
            "idle": AgentState.IDLE,
            "processing": AgentState.PROCESSING,
            "waiting": AgentState.WAITING,
            "error": AgentState.ERROR,
            "paused": AgentState.PAUSED,
        }
        state = state_map.get(event.state, AgentState.IDLE)
        await self.update_agent_state(event.agent_name, state, event.thread_id)

    async def _handle_thread_event(self, event: "ThreadEvent") -> None:
        """Handle thread lifecycle event from pump."""
        # Map status to ThreadStatus enum
        status_map = {
            "created": ThreadStatus.ACTIVE,
            "active": ThreadStatus.ACTIVE,
            "completed": ThreadStatus.COMPLETED,
            "error": ThreadStatus.ERROR,
            "killed": ThreadStatus.KILLED,
        }
        status = status_map.get(event.status, ThreadStatus.ACTIVE)

        if event.status in ("completed", "error", "killed"):
            await self.complete_thread(event.thread_id, status, event.error)

    def set_running(self) -> None:
        """Mark organism as running."""
        self._status = OrganismStatus.RUNNING

    def set_stopping(self) -> None:
        """Mark organism as stopping."""
        self._status = OrganismStatus.STOPPING

    async def reload_config(self, config_path: Optional[str] = None) -> dict:
        """
        Hot-reload organism configuration.

        Calls pump.reload_config() and updates local agent state.

        Args:
            config_path: Optional path to config file (uses stored path if not provided)

        Returns:
            Dict with reload results
        """
        from xml_pipeline.message_bus import ReloadEvent

        # Call pump's reload
        event = self.pump.reload_config(config_path)

        if event.success:
            # Refresh agent states from pump
            async with self._lock:
                # Remove agents that were removed from pump
                for name in event.removed_listeners:
                    if name in self._agents:
                        del self._agents[name]

                # Add/update agents
                for name in event.added_listeners + event.updated_listeners:
                    listener = self.pump.listeners.get(name)
                    if listener:
                        self._agents[name] = AgentRuntimeState(
                            name=name,
                            description=listener.description,
                            is_agent=listener.is_agent,
                            peers=list(listener.peers),
                            payload_class=f"{listener.payload_class.__module__}.{listener.payload_class.__name__}",
                        )

            # Notify subscribers of reload
            await self._broadcast({
                "event": "reload",
                "success": True,
                "added": event.added_listeners,
                "removed": event.removed_listeners,
                "updated": event.updated_listeners,
            })

        return {
            "success": event.success,
            "added": event.added_listeners,
            "removed": event.removed_listeners,
            "updated": event.updated_listeners,
            "error": event.error,
        }

    # =========================================================================
    # Event Recording (called by pump hooks)
    # =========================================================================

    async def record_message(
        self,
        thread_id: str,
        from_id: str,
        to_id: str,
        payload_type: str,
        payload: Dict[str, Any],
    ) -> str:
        """Record a message and update related state."""
        async with self._lock:
            msg_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            self._message_count += 1

            record = MessageRecord(
                id=msg_id,
                thread_id=thread_id,
                from_id=from_id,
                to_id=to_id,
                payload_type=payload_type,
                payload=payload,
                timestamp=now,
                slot_index=self._message_count,
            )
            self._messages.append(record)

            # Update thread state
            if thread_id not in self._threads:
                self._threads[thread_id] = ThreadRuntimeState(
                    id=thread_id,
                    created_at=now,
                )
            thread = self._threads[thread_id]
            thread.participants.add(from_id)
            thread.participants.add(to_id)
            thread.message_count += 1
            thread.last_activity = now

            # Update agent states
            if from_id in self._agents:
                self._agents[from_id].message_count += 1
                self._agents[from_id].last_activity = now

            # Notify subscribers
            await self._notify_message(record)

            return msg_id

    async def update_agent_state(
        self,
        agent_name: str,
        state: AgentState,
        current_thread: Optional[str] = None,
    ) -> None:
        """Update an agent's processing state."""
        async with self._lock:
            if agent_name not in self._agents:
                return

            agent = self._agents[agent_name]
            old_state = agent.state
            agent.state = state
            agent.current_thread = current_thread
            agent.last_activity = datetime.now(timezone.utc)

            if old_state != state:
                await self._notify_agent_state(agent_name, state, current_thread)

    async def complete_thread(
        self,
        thread_id: str,
        status: ThreadStatus = ThreadStatus.COMPLETED,
        error: Optional[str] = None,
    ) -> None:
        """Mark a thread as completed or errored."""
        async with self._lock:
            if thread_id not in self._threads:
                return

            thread = self._threads[thread_id]
            thread.status = status
            thread.error = error
            thread.last_activity = datetime.now(timezone.utc)

            await self._notify_thread_updated(thread)

    # =========================================================================
    # Query Methods (for API)
    # =========================================================================

    def get_organism_info(self) -> OrganismInfo:
        """Get organism overview."""
        return OrganismInfo(
            name=self.pump.config.name,
            status=self._status,
            uptime_seconds=time.time() - self._start_time,
            agent_count=len(self._agents),
            active_threads=sum(
                1 for t in self._threads.values() if t.status == ThreadStatus.ACTIVE
            ),
            total_messages=self._message_count,
            identity_configured=self.pump.identity is not None,
        )

    def get_organism_config(self) -> Dict[str, Any]:
        """Get sanitized organism config (no secrets)."""
        return {
            "name": self.pump.config.name,
            "port": self.pump.config.port,
            "thread_scheduling": self.pump.config.thread_scheduling,
            "max_concurrent_pipelines": self.pump.config.max_concurrent_pipelines,
            "max_concurrent_handlers": self.pump.config.max_concurrent_handlers,
            "listeners": list(self.pump.listeners.keys()),
        }

    def get_agents(self) -> List[AgentInfo]:
        """Get all agents."""
        return [self._agent_to_info(a) for a in self._agents.values()]

    def get_agent(self, name: str) -> Optional[AgentInfo]:
        """Get a single agent by name."""
        agent = self._agents.get(name)
        if agent:
            return self._agent_to_info(agent)
        return None

    def get_agent_schema(self, name: str) -> Optional[str]:
        """Get agent's XSD schema as string."""
        listener = self.pump.listeners.get(name)
        if listener and listener.schema is not None:
            from lxml import etree

            return etree.tostring(listener.schema, encoding="unicode", pretty_print=True)
        return None

    # =========================================================================
    # Capability Introspection (for operators, not agents)
    # =========================================================================

    def get_capabilities(self) -> List[CapabilityInfo]:
        """
        List all registered capabilities.

        This is for operator introspection via REST API.
        Agents cannot access this - they only know their peers.
        """
        capabilities = []
        for name, listener in self.pump.listeners.items():
            capabilities.append(
                CapabilityInfo(
                    name=name,
                    description=listener.description,
                    is_agent=listener.is_agent,
                    peers=list(listener.peers),
                    root_tag=listener.root_tag,
                )
            )
        # Sort by name for consistent ordering
        capabilities.sort(key=lambda c: c.name)
        return capabilities

    def get_capability(self, name: str) -> Optional[CapabilityDetail]:
        """
        Get detailed capability info including schema.

        This is for operator introspection via REST API.
        """
        listener = self.pump.listeners.get(name)
        if listener is None:
            return None

        # Get XSD schema
        schema_xsd = None
        if listener.schema is not None:
            from lxml import etree
            try:
                # Get the schema document from the XMLSchema object
                # XMLSchema wraps an Element, we need to serialize it
                schema_xsd = etree.tostring(
                    listener.schema, encoding="unicode", pretty_print=True
                )
            except Exception:
                pass

        # Generate example XML
        example_xml = None
        if hasattr(listener.payload_class, '__dataclass_fields__'):
            fields = listener.payload_class.__dataclass_fields__
            class_name = listener.payload_class.__name__
            lines = [f"<{class_name}>"]
            for fname in fields:
                lines.append(f"  <{fname}>...</{fname}>")
            lines.append(f"</{class_name}>")
            example_xml = "\n".join(lines)

        return CapabilityDetail(
            name=name,
            description=listener.description,
            is_agent=listener.is_agent,
            peers=list(listener.peers),
            root_tag=listener.root_tag,
            payload_class=f"{listener.payload_class.__module__}.{listener.payload_class.__name__}",
            schema_xsd=schema_xsd,
            example_xml=example_xml,
        )

    def get_threads(
        self,
        status: Optional[ThreadStatus] = None,
        agent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[ThreadInfo], int]:
        """Get threads with optional filtering."""
        threads = list(self._threads.values())

        # Filter
        if status:
            threads = [t for t in threads if t.status == status]
        if agent:
            threads = [t for t in threads if agent in t.participants]

        # Sort by last activity (most recent first)
        threads.sort(key=lambda t: t.last_activity or t.created_at, reverse=True)

        total = len(threads)
        threads = threads[offset : offset + limit]

        return [self._thread_to_info(t) for t in threads], total

    def get_thread(self, thread_id: str) -> Optional[ThreadInfo]:
        """Get a single thread by ID."""
        thread = self._threads.get(thread_id)
        if thread:
            return self._thread_to_info(thread)
        return None

    def get_messages(
        self,
        thread_id: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[MessageInfo], int]:
        """Get messages with optional filtering."""
        messages = self._messages.copy()

        # Filter
        if thread_id:
            messages = [m for m in messages if m.thread_id == thread_id]
        if agent:
            messages = [m for m in messages if m.from_id == agent or m.to_id == agent]

        # Sort by timestamp (most recent first)
        messages.sort(key=lambda m: m.timestamp, reverse=True)

        total = len(messages)
        messages = messages[offset : offset + limit]

        return [self._message_to_info(m) for m in messages], total

    # =========================================================================
    # Subscription Management
    # =========================================================================

    def subscribe(self, callback: Callable) -> None:
        """Subscribe to state events."""
        self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """Unsubscribe from state events."""
        self._subscribers.discard(callback)

    async def _notify_message(self, record: MessageRecord) -> None:
        """Notify subscribers of new message."""
        event = {
            "event": "message",
            "message": self._message_to_info(record).model_dump(by_alias=True),
        }
        await self._broadcast(event)

    async def _notify_agent_state(
        self,
        agent_name: str,
        state: AgentState,
        current_thread: Optional[str],
    ) -> None:
        """Notify subscribers of agent state change."""
        event = {
            "event": "agent_state",
            "agent": agent_name,
            "state": state.value,
            "currentThread": current_thread,
        }
        await self._broadcast(event)

    async def _notify_thread_updated(self, thread: ThreadRuntimeState) -> None:
        """Notify subscribers of thread update."""
        event = {
            "event": "thread_updated",
            "threadId": thread.id,
            "status": thread.status.value,
            "messageCount": thread.message_count,
        }
        await self._broadcast(event)

    async def _broadcast(self, event: Dict[str, Any]) -> None:
        """Broadcast event to all subscribers."""
        for callback in list(self._subscribers):
            try:
                await callback(event)
            except Exception:
                # Remove failed subscribers
                self._subscribers.discard(callback)

    # =========================================================================
    # Conversion Helpers
    # =========================================================================

    def _agent_to_info(self, agent: AgentRuntimeState) -> AgentInfo:
        """Convert runtime state to API model."""
        return AgentInfo(
            name=agent.name,
            description=agent.description,
            is_agent=agent.is_agent,
            peers=agent.peers,
            payload_class=agent.payload_class,
            state=agent.state,
            current_thread=agent.current_thread,
            queue_depth=agent.queue_depth,
            last_activity=agent.last_activity,
            message_count=agent.message_count,
        )

    def _thread_to_info(self, thread: ThreadRuntimeState) -> ThreadInfo:
        """Convert runtime state to API model."""
        return ThreadInfo(
            id=thread.id,
            status=thread.status,
            participants=list(thread.participants),
            message_count=thread.message_count,
            created_at=thread.created_at,
            last_activity=thread.last_activity,
            error=thread.error,
        )

    def _message_to_info(self, record: MessageRecord) -> MessageInfo:
        """Convert record to API model."""
        return MessageInfo(
            id=record.id,
            thread_id=record.thread_id,
            from_id=record.from_id,
            to_id=record.to_id,
            payload_type=record.payload_type,
            payload=record.payload,
            timestamp=record.timestamp,
            slot_index=record.slot_index,
        )
