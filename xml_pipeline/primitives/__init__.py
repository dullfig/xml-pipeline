"""
System primitives — Core message types handled by the organism itself.

These are not user-defined listeners but system-level messages that
establish context, handle errors, and manage the organism lifecycle.
"""

from xml_pipeline.primitives.boot import Boot, handle_boot
from xml_pipeline.primitives.todo import (
    TodoUntil,
    TodoComplete,
    TodoRegistered,
    TodoClosed,
    handle_todo_until,
    handle_todo_complete,
)
from xml_pipeline.primitives.text_input import TextInput, TextOutput
from xml_pipeline.primitives.sequence import (
    SequenceStart,
    SequenceComplete,
    SequenceError,
    handle_sequence_start,
)
from xml_pipeline.primitives.buffer import (
    BufferStart,
    BufferItem,
    BufferComplete,
    BufferDispatched,
    BufferError,
    handle_buffer_start,
)

__all__ = [
    # Boot
    "Boot",
    "handle_boot",
    # Todo
    "TodoUntil",
    "TodoComplete",
    "TodoRegistered",
    "TodoClosed",
    "handle_todo_until",
    "handle_todo_complete",
    # Text I/O
    "TextInput",
    "TextOutput",
    # Sequence orchestration
    "SequenceStart",
    "SequenceComplete",
    "SequenceError",
    "handle_sequence_start",
    # Buffer orchestration
    "BufferStart",
    "BufferItem",
    "BufferComplete",
    "BufferDispatched",
    "BufferError",
    "handle_buffer_start",
]
