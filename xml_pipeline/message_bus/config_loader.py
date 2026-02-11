"""
config_loader.py — YAML configuration loader for organism configs.

Loads organism.yaml, parses listener definitions, and resolves
Python import paths to actual classes and functions.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
from pathlib import Path
from typing import List, Dict, Any

import yaml

from xml_pipeline.message_bus.pump_config import ListenerConfig, OrganismConfig


class ConfigLoader:
    @staticmethod
    def _validate_port(port: int, context: str) -> None:
        """Validate that a port number is in the valid range."""
        if not (1 <= port <= 65535):
            raise ValueError(
                f"Invalid port {port} in {context}: must be between 1 and 65535"
            )

    @classmethod
    def load(cls, path: str | Path) -> OrganismConfig:
        with open(Path(path)) as f:
            raw = yaml.safe_load(f)
        return cls._parse(raw)

    @classmethod
    def _parse(cls, raw: dict) -> OrganismConfig:
        org = raw.get("organism", {})

        # Parse backend config
        backend = raw.get("backend", {})
        backend_type = backend.get("type", "memory") if backend else "memory"
        backend_redis_url = backend.get("redis_url", "redis://localhost:6379") if backend else "redis://localhost:6379"
        backend_redis_prefix = backend.get("redis_prefix", "xp:") if backend else "xp:"

        # Validate organism port
        org_port = org.get("port", 8765)
        cls._validate_port(org_port, "organism.port")

        # Parse OOB config
        oob = raw.get("oob", {})
        oob_enabled = oob.get("enabled", True) if oob else True
        oob_bind = oob.get("bind", "127.0.0.1") if oob else "127.0.0.1"
        oob_port = oob.get("port", 8766) if oob else 8766
        if oob:
            cls._validate_port(oob_port, "oob.port")

        # Parse auth config
        auth = raw.get("auth", {})
        auth_totp_secret_env = auth.get("totp_secret_env", "") if auth else ""
        auth_totp_required = auth.get("totp_required", False) if auth else False
        auth_api_key_env = auth.get("api_key_env", "") if auth else ""

        # Parse peer tables
        peer_table_configs: List[Dict[str, Any]] = []
        for pt_raw in raw.get("peer_tables", []):
            name = pt_raw.get("name", "")
            parent = pt_raw.get("parent")
            os_user = pt_raw.get("os_user")
            peers_dict: Dict[str, List[str]] = {}
            for entry in pt_raw.get("entries", []):
                listener_name = entry.get("listener", "")
                peer_list = entry.get("peers", [])
                if listener_name:
                    peers_dict[listener_name] = peer_list
            pt_config: Dict[str, Any] = {
                "name": name,
                "parent": parent,
                "peers": peers_dict,
            }
            if os_user is not None:
                pt_config["os_user"] = os_user
            peer_table_configs.append(pt_config)

        # Parse network port allocations
        network_ports: List[Dict[str, Any]] = []
        network = raw.get("network", {})
        if network:
            for port_raw in network.get("ports", []):
                net_port = int(port_raw["port"])
                cls._validate_port(net_port, f"network.ports[{net_port}]")
                network_ports.append({
                    "port": net_port,
                    "bind": port_raw.get("bind", "127.0.0.1"),
                    "listener": port_raw.get("listener", ""),
                    "protocol": port_raw.get("protocol", "tcp"),
                })

        # Parse WASM tool configs
        wasm_tool_configs: List[Dict[str, Any]] = []
        for tool_raw in raw.get("tools", []):
            wasm_tool_configs.append({
                "name": tool_raw["name"],
                "wasm_path": tool_raw["wasm_path"],
                "wit_path": tool_raw["wit_path"],
                "description": tool_raw.get("description", ""),
                "capabilities": tool_raw.get("capabilities", []),
                "memory_limit_mb": tool_raw.get("memory_limit_mb", 64),
                "timeout_seconds": float(tool_raw.get("timeout_seconds", 5.0)),
            })

        # Parse shell config
        shell_config: Dict[str, Any] = {}
        shell_raw = raw.get("shell", {})
        if shell_raw:
            shell_config = {
                "enabled": shell_raw.get("enabled", False),
                "default_os_user": shell_raw.get("default_os_user", ""),
                "xp_exec_path": shell_raw.get("xp_exec_path", "/usr/local/bin/xp-exec"),
                "totp_secret_env": shell_raw.get("totp_secret_env", ""),
            }

        # Parse compiler config
        compiler_config: Dict[str, Any] = {}
        compiler_raw = raw.get("compiler", {})
        if compiler_raw:
            compiler_config = {
                "asc_wasm_path": compiler_raw.get("asc_wasm_path", ""),
                "stdlib_path": compiler_raw.get("stdlib_path", ""),
                "timeout_seconds": float(compiler_raw.get("timeout_seconds", 60.0)),
                "memory_limit_mb": int(compiler_raw.get("memory_limit_mb", 256)),
            }

        config = OrganismConfig(
            name=org.get("name", "unnamed"),
            identity_path=org.get("identity", ""),
            port=org.get("port", 8765),
            thread_scheduling=raw.get("thread_scheduling", "breadth-first"),
            max_concurrent_pipelines=raw.get("max_concurrent_pipelines", 50),
            max_concurrent_handlers=raw.get("max_concurrent_handlers", 20),
            max_concurrent_per_agent=raw.get("max_concurrent_per_agent", 5),
            max_tokens_per_thread=raw.get("max_tokens_per_thread", 100_000),
            llm_config=raw.get("llm", {}),
            backend_type=backend_type,
            backend_redis_url=backend_redis_url,
            backend_redis_prefix=backend_redis_prefix,
            oob_enabled=oob_enabled,
            oob_bind=oob_bind,
            oob_port=oob_port,
            auth_totp_secret_env=auth_totp_secret_env,
            auth_totp_required=auth_totp_required,
            auth_api_key_env=auth_api_key_env,
            peer_table_configs=peer_table_configs,
            network_ports=network_ports,
            wasm_tool_configs=wasm_tool_configs,
            shell_config=shell_config,
            compiler_config=compiler_config,
        )

        for entry in raw.get("listeners", []):
            lc = cls._parse_listener(entry)
            cls._resolve_imports(lc)
            config.listeners.append(lc)

        return config

    @classmethod
    def _parse_listener(cls, raw: dict) -> ListenerConfig:
        # trusted: None = auto-detect, True/False = explicit
        trusted_raw = raw.get("trusted")
        trusted = None if trusted_raw is None else bool(trusted_raw)

        return ListenerConfig(
            name=raw["name"],
            payload_class_path=raw["payload_class"],
            handler_path=raw["handler"],
            description=raw["description"],
            is_agent=raw.get("agent", False),
            peers=raw.get("peers", []),
            broadcast=raw.get("broadcast", False),
            prompt=raw.get("prompt", ""),
            timeout=float(raw.get("timeout_seconds", 30.0)),
            trusted=trusted,
            handler_sha256=raw.get("handler_sha256", ""),
        )

    # Pattern for individual path segments: valid Python identifiers
    _SAFE_SEGMENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

    @classmethod
    def _validate_import_path(cls, path: str, context: str) -> None:
        """Validate that an import path contains only safe characters."""
        # Reject consecutive dots (e.g., "foo..bar", "a...evil.module")
        if ".." in path:
            raise ValueError(
                f"Unsafe import path in {context}: {path!r} "
                f"(consecutive dots not allowed)"
            )
        # Validate each segment individually
        segments = path.split(".")
        for segment in segments:
            if not cls._SAFE_SEGMENT_RE.match(segment):
                raise ValueError(
                    f"Unsafe import path in {context}: {path!r} "
                    f"(must be dotted Python identifiers)"
                )
            # Reject dunder segments (e.g., __builtins__, __import__)
            if segment.startswith("__") and segment.endswith("__"):
                raise ValueError(
                    f"Dunder module segment not allowed in {context}: {segment!r}"
                )

    @classmethod
    def _resolve_imports(cls, lc: ListenerConfig) -> None:
        cls._validate_import_path(lc.payload_class_path, f"listener '{lc.name}' payload_class")
        cls._validate_import_path(lc.handler_path, f"listener '{lc.name}' handler")

        mod, cls_name = lc.payload_class_path.rsplit(".", 1)
        lc.payload_class = getattr(importlib.import_module(mod), cls_name)

        handler_mod_path, fn_name = lc.handler_path.rsplit(".", 1)
        handler_module = importlib.import_module(handler_mod_path)
        lc.handler = getattr(handler_module, fn_name)

        if lc.handler_sha256:
            module_file = getattr(handler_module, "__file__", None)
            if module_file is None:
                raise ValueError(
                    f"handler_sha256 set for '{lc.name}' but handler module "
                    f"'{handler_mod_path}' has no __file__ (built-in or frozen module)"
                )
            resolved = Path(module_file).resolve()
            # If Python loaded a .pyc, find the corresponding .py source
            hash_target = resolved
            if resolved.suffix == ".pyc":
                try:
                    source_path = importlib.util.source_from_cache(str(resolved))
                except (ValueError, NotImplementedError):
                    raise ValueError(
                        f"Handler integrity check failed for '{lc.name}': "
                        f".pyc loaded but cannot determine source path"
                    )
                source = Path(source_path)
                if not source.exists():
                    raise ValueError(
                        f"Handler integrity check failed for '{lc.name}': "
                        f".pyc loaded but source .py not found at {source_path}"
                    )
                hash_target = source
            actual = hashlib.sha256(hash_target.read_bytes()).hexdigest()
            if actual != lc.handler_sha256:
                raise ValueError(
                    f"Handler integrity check failed for '{lc.name}': "
                    f"expected sha256={lc.handler_sha256}, "
                    f"got sha256={actual}"
                )
