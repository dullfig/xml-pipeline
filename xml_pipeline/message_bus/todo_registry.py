"""
todo_registry.py — Registry for TodoUntil watchers.

Tracks pending "todos" that agents have issued. When a matching message
appears on the thread, the watcher's eyebrow is raised. Subsequent messages
to the issuing agent include a nudge until the agent explicitly closes
the todo with <TodoComplete/>.

Design:
- Observer pattern, not interceptor — messages flow normally
- Thread-scoped — watchers only see messages on their thread
- Persistent nudge — keeps nagging until explicit close
- Cheap matching — payload type + optional source filter

Usage:
    registry = get_todo_registry()

    # Agent issues TodoUntil
    watcher_id = registry.register(
        thread_id="...",
        issuer="greeter",
        wait_for="ShoutedResponse",
        from_listener="shouter",  # optional
    )

    # On each message, check for matches
    registry.check(message_state)

    # When dispatching to an agent, get raised eyebrows
    raised = registry.get_raised_for(thread_id, agent_name)

    # Agent closes todo
    registry.close(watcher_id)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import uuid
import threading


@dataclass
class TodoWatcher:
    """A pending todo that watches for a condition on a thread."""

    id: str                         # Unique ID for explicit close
    thread_id: str                  # Thread being watched
    issuer: str                     # Agent that issued the todo (who to nag)
    wait_for: str                   # Payload type to match (e.g., "ShoutedResponse")
    from_listener: Optional[str] = None  # Optional: must be from specific source
    description: str = ""           # Human-readable description of what we're waiting for

    eyebrow_raised: bool = False    # True when condition appears satisfied
    triggered_by: Any = None        # The payload that raised the eyebrow
    triggered_from: str = ""        # Who sent the triggering message


class TodoRegistry:
    """
    Registry for TodoUntil watchers.

    Thread-safe. Singleton pattern via get_todo_registry().
    """

    def __init__(self):
        self._lock = threading.Lock()
        # thread_id -> list of watchers
        self._watchers: Dict[str, List[TodoWatcher]] = {}
        # watcher_id -> watcher (for fast lookup on close)
        self._by_id: Dict[str, TodoWatcher] = {}

    def register(
        self,
        thread_id: str,
        issuer: str,
        wait_for: str,
        from_listener: Optional[str] = None,
        description: str = "",
    ) -> str:
        """
        Register a new todo watcher.

        Returns the watcher ID for explicit close.
        """
        watcher_id = str(uuid.uuid4())
        watcher = TodoWatcher(
            id=watcher_id,
            thread_id=thread_id,
            issuer=issuer,
            wait_for=wait_for.lower(),  # Normalize for matching
            from_listener=from_listener.lower() if from_listener else None,
            description=description,
        )

        with self._lock:
            if thread_id not in self._watchers:
                self._watchers[thread_id] = []
            self._watchers[thread_id].append(watcher)
            self._by_id[watcher_id] = watcher

        return watcher_id

    def check(self, thread_id: str, payload_type: str, from_id: str, payload: Any = None) -> List[TodoWatcher]:
        """
        Check if any watchers on this thread match the incoming message.

        Raises eyebrows on matching watchers. Returns list of newly raised.
        """
        newly_raised = []
        payload_type_lower = payload_type.lower()
        from_id_lower = from_id.lower() if from_id else ""

        with self._lock:
            watchers = self._watchers.get(thread_id, [])
            for watcher in watchers:
                if watcher.eyebrow_raised:
                    continue  # Already raised

                # Check payload type match
                if watcher.wait_for not in payload_type_lower:
                    continue

                # Check optional from_listener filter
                if watcher.from_listener and watcher.from_listener != from_id_lower:
                    continue

                # Match! Raise the eyebrow
                watcher.eyebrow_raised = True
                watcher.triggered_by = payload
                watcher.triggered_from = from_id
                newly_raised.append(watcher)

        return newly_raised

    def get_raised_for(self, thread_id: str, agent: str) -> List[TodoWatcher]:
        """
        Get all raised eyebrows for this agent on this thread.

        These are the todos that appear satisfied and should be nagged about.
        """
        agent_lower = agent.lower()
        with self._lock:
            watchers = self._watchers.get(thread_id, [])
            return [w for w in watchers if w.issuer.lower() == agent_lower and w.eyebrow_raised]

    def get_pending_for(self, thread_id: str, agent: str) -> List[TodoWatcher]:
        """
        Get all pending (not yet raised) todos for this agent on this thread.

        Useful for showing the agent what it's still waiting for.
        """
        agent_lower = agent.lower()
        with self._lock:
            watchers = self._watchers.get(thread_id, [])
            return [w for w in watchers if w.issuer.lower() == agent_lower and not w.eyebrow_raised]

    def close(self, watcher_id: str) -> bool:
        """
        Close a todo by ID.

        Returns True if found and removed, False if not found.
        """
        with self._lock:
            watcher = self._by_id.pop(watcher_id, None)
            if watcher is None:
                return False

            thread_watchers = self._watchers.get(watcher.thread_id, [])
            try:
                thread_watchers.remove(watcher)
            except ValueError:
                pass

            # Clean up empty thread entries
            if not thread_watchers:
                self._watchers.pop(watcher.thread_id, None)

            return True

    def close_all_for_thread(self, thread_id: str) -> int:
        """
        Close all watchers for a thread (e.g., when thread ends).

        Returns count of watchers removed.
        """
        with self._lock:
            watchers = self._watchers.pop(thread_id, [])
            for w in watchers:
                self._by_id.pop(w.id, None)
            return len(watchers)

    def format_nudge(self, watchers: List[TodoWatcher]) -> str:
        """
        Format raised eyebrows as a nudge string for the LLM.

        Returns empty string if no raised eyebrows.
        """
        if not watchers:
            return ""

        lines = ["[SYSTEM NOTE: The following todos appear complete:]"]
        for w in watchers:
            desc = f" ({w.description})" if w.description else ""
            lines.append(f"  - Waiting for {w.wait_for}{desc}: received from {w.triggered_from}")
            lines.append(f"    Close with: <TodoComplete><Id>{w.id}</Id></TodoComplete>")

        return "\n".join(lines)

    def clear(self):
        """Clear all watchers. Useful for testing."""
        with self._lock:
            self._watchers.clear()
            self._by_id.clear()


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[TodoRegistry] = None
_registry_lock = threading.Lock()


def get_todo_registry() -> TodoRegistry:
    """Get the global TodoRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = TodoRegistry()
    return _registry
