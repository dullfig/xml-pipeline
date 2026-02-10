"""
coding_swarm — Multi-agent organism for collaborative WASM tool development.

Five LLM-powered agents + four scoped tool listeners, wired through
xml-pipeline's message bus.
"""

from handlers.coding_swarm.payloads import SwarmMessage
from handlers.coding_swarm.coordinator import handle_coordinator
from handlers.coding_swarm.architect import handle_architect
from handlers.coding_swarm.coder import handle_coder
from handlers.coding_swarm.tester import handle_tester
from handlers.coding_swarm.reviewer import handle_reviewer
from handlers.coding_swarm.test_runner import handle_test_run
from handlers.coding_swarm.tools import (
    handle_workspace_read,
    handle_workspace_write,
    handle_compile,
    set_swarm_backend,
    get_swarm_backend,
)

__all__ = [
    "SwarmMessage",
    "handle_coordinator",
    "handle_architect",
    "handle_coder",
    "handle_tester",
    "handle_reviewer",
    "handle_test_run",
    "handle_workspace_read",
    "handle_workspace_write",
    "handle_compile",
    "set_swarm_backend",
    "get_swarm_backend",
]
