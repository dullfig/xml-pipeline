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

__all__ = [
    "Boot",
    "handle_boot",
    "TodoUntil",
    "TodoComplete",
    "TodoRegistered",
    "TodoClosed",
    "handle_todo_until",
    "handle_todo_complete",
    "TextInput",
    "TextOutput",
]
