"""
singleton.py — Global StreamPump singleton.

Provides get/set/reset for the global pump instance.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump


_pump: Optional["StreamPump"] = None


def get_stream_pump() -> "StreamPump":
    """
    Get the global StreamPump instance.

    The pump is set during StreamPump.start().
    Raises RuntimeError if called before start().
    """
    global _pump
    if _pump is None:
        raise RuntimeError(
            "StreamPump not initialized. Call pump.start() first."
        )
    return _pump


def set_stream_pump(pump: "StreamPump") -> None:
    """
    Set the global StreamPump instance.

    Called by StreamPump.start() after initialization.
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
