"""
pump_config.py — Configuration and runtime dataclasses for the message pump.

Pure data classes with no dependencies on StreamPump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any

from lxml import etree


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class ListenerConfig:
    name: str
    payload_class_path: str
    handler_path: str
    description: str
    is_agent: bool = False
    peers: List[str] = field(default_factory=list)
    broadcast: bool = False
    prompt: str = ""  # System prompt for LLM agents (loaded into PromptRegistry)
    cpu_bound: bool = False  # Dispatch to ProcessPoolExecutor if True
    timeout: float = 30.0  # Handler execution timeout in seconds
    payload_class: type = field(default=None, repr=False)
    handler: Callable = field(default=None, repr=False)


@dataclass
class OrganismConfig:
    name: str
    identity_path: str = ""
    port: int = 8765
    thread_scheduling: str = "breadth-first"
    listeners: List[ListenerConfig] = field(default_factory=list)

    # Concurrency tuning
    max_concurrent_pipelines: int = 50    # Total concurrent messages in pipeline
    max_concurrent_handlers: int = 20     # Concurrent handler invocations
    max_concurrent_per_agent: int = 5     # Per-agent rate limit

    # Token budget enforcement
    max_tokens_per_thread: int = 100_000  # Max tokens per conversation thread

    # LLM configuration (optional)
    llm_config: Dict[str, Any] = field(default_factory=dict)

    # Process pool configuration (for cpu_bound handlers)
    process_pool_workers: int = 4
    process_pool_max_tasks_per_child: int = 100
    process_pool_enabled: bool = False

    # Backend configuration (for shared state)
    backend_type: str = "memory"  # "memory", "manager", "redis"
    backend_redis_url: str = "redis://localhost:6379"
    backend_redis_prefix: str = "xp:"

    # OOB privileged channel
    oob_enabled: bool = True
    oob_bind: str = "127.0.0.1"
    oob_port: int = 8766

    # Auth (TOTP for OOB channel)
    auth_totp_secret_env: str = ""
    auth_totp_required: bool = False

    # Peer tables from YAML config
    # Each: {"name": str, "parent": str|None, "peers": {listener: [peers]}}
    peer_table_configs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Listener:
    name: str
    payload_class: type
    handler: Callable
    description: str
    is_agent: bool = False
    peers: List[str] = field(default_factory=list)
    broadcast: bool = False
    cpu_bound: bool = False  # Dispatch to ProcessPoolExecutor if True
    handler_path: str = ""  # Import path for worker process
    timeout: float = 30.0  # Handler execution timeout in seconds
    schema: etree.XMLSchema = field(default=None, repr=False)
    root_tag: str = ""
    usage_instructions: str = ""  # Generated at registration for LLM agents
