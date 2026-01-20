# message_bus/thread.py
from __future__ import annotations
from typing import Optional
from lxml import etree
import uuid


class Thread:
    """
    Internal thread context used by MessageBus.

    Not part of the public API — do not import or instantiate directly.
    Exists in its own module only to keep MessageBus readable.
    """

    def __init__(
            self,
            parent: Optional['Thread'] = None,
            thread_id: Optional[str] = None,
            metadata: Optional[dict] = None,
    ):
        self.id = thread_id or str(uuid.uuid4())
        self.parent = parent
        self.depth = parent.depth + 1 if parent else 0
        self.buffer = bytearray()

        # noinspection PyTypeChecker
        self.parser = etree.XMLPullParser(events=("end",))

        self.active = True
        self.metadata = metadata or (parent.metadata.copy() if parent else {})

    # Optional: add __repr__ for debugging
    def __repr__(self) -> str:
        return f"<Thread {self.id[:8]} depth={self.depth} buf={len(self.buffer)}>"