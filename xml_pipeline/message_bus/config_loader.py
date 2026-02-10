"""
config_loader.py — YAML configuration loader for organism configs.

Loads organism.yaml, parses listener definitions, and resolves
Python import paths to actual classes and functions.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import List, Dict, Any

import yaml

from xml_pipeline.message_bus.pump_config import ListenerConfig, OrganismConfig


class ConfigLoader:
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

        # Parse OOB config
        oob = raw.get("oob", {})
        oob_enabled = oob.get("enabled", True) if oob else True
        oob_bind = oob.get("bind", "127.0.0.1") if oob else "127.0.0.1"
        oob_port = oob.get("port", 8766) if oob else 8766

        # Parse auth config
        auth = raw.get("auth", {})
        auth_totp_secret_env = auth.get("totp_secret_env", "") if auth else ""
        auth_totp_required = auth.get("totp_required", False) if auth else False

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
                network_ports.append({
                    "port": int(port_raw["port"]),
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
        )

    @classmethod
    def _resolve_imports(cls, lc: ListenerConfig) -> None:
        mod, cls_name = lc.payload_class_path.rsplit(".", 1)
        lc.payload_class = getattr(importlib.import_module(mod), cls_name)

        mod, fn_name = lc.handler_path.rsplit(".", 1)
        lc.handler = getattr(importlib.import_module(mod), fn_name)
