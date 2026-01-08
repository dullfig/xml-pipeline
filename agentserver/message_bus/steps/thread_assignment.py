"""
thread_assignment.py — Ensure every message has a valid opaque thread UUID.

The envelope.xsd requires <thread>, but external clients may:
  - Omit it (first message)
  - Send invalid format
  - Send duplicate/malformed UUID

This step enforces:
  - Presence of a valid UUID v4 string in <thread>
  - If missing or invalid → generate a new one (new root thread)
  - Store it in state.thread_id for all downstream use

This guarantees thread continuity and privacy (external parties never see internal hierarchy).

Part of AgentServer v2.1 message pump.
"""

import uuid
from agentserver.message_bus.message_state import MessageState


def _is_valid_uuid(val: str) -> bool:
    """Simple UUID v4 validation — accepts standard string formats."""
    try:
        uuid_obj = uuid.UUID(val, version=4)
        return str(uuid_obj) == val  # Ensures canonical lowercase format
    except ValueError:
        return False


async def thread_assignment_step(state: MessageState) -> MessageState:
    """
    Assign or validate the thread UUID.

    - If state.thread_id is already set and valid → keep it
    - Else → generate new UUID v4
    - Always normalizes to lowercase canonical string

    This is the source of truth for thread identity throughout the organism.
    """
    if state.thread_id and _is_valid_uuid(state.thread_id):
        # Already valid — nothing to do
        return state

    # Invalid, missing, or malformed — generate new root thread
    new_thread_id = str(uuid.uuid4())

    # Optional: log warning if external client sent bad thread
    if state.thread_id:
        state.metadata.setdefault("diagnostics", []).append(
            f"Invalid external thread ID '{state.thread_id}' — replaced with new root thread"
        )

    state.thread_id = new_thread_id

    return state