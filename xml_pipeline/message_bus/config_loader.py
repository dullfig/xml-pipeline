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

        # Parse process pool config
        pool = raw.get("process_pool", {})
        process_pool_enabled = pool.get("enabled", False) if pool else False
        process_pool_workers = pool.get("workers", 4) if pool else 4
        process_pool_max_tasks = pool.get("max_tasks_per_child", 100) if pool else 100

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
            peers_dict: Dict[str, List[str]] = {}
            for entry in pt_raw.get("entries", []):
                listener_name = entry.get("listener", "")
                peer_list = entry.get("peers", [])
                if listener_name:
                    peers_dict[listener_name] = peer_list
            peer_table_configs.append({
                "name": name,
                "parent": parent,
                "peers": peers_dict,
            })

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
            process_pool_enabled=process_pool_enabled,
            process_pool_workers=process_pool_workers,
            process_pool_max_tasks_per_child=process_pool_max_tasks,
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
        )

        for entry in raw.get("listeners", []):
            lc = cls._parse_listener(entry)
            cls._resolve_imports(lc)
            config.listeners.append(lc)

        return config

    @classmethod
    def _parse_listener(cls, raw: dict) -> ListenerConfig:
        return ListenerConfig(
            name=raw["name"],
            payload_class_path=raw["payload_class"],
            handler_path=raw["handler"],
            description=raw["description"],
            is_agent=raw.get("agent", False),
            peers=raw.get("peers", []),
            broadcast=raw.get("broadcast", False),
            prompt=raw.get("prompt", ""),
            cpu_bound=raw.get("cpu_bound", False),
            timeout=float(raw.get("timeout_seconds", 30.0)),
        )

    @classmethod
    def _resolve_imports(cls, lc: ListenerConfig) -> None:
        mod, cls_name = lc.payload_class_path.rsplit(".", 1)
        lc.payload_class = getattr(importlib.import_module(mod), cls_name)

        mod, fn_name = lc.handler_path.rsplit(".", 1)
        lc.handler = getattr(importlib.import_module(mod), fn_name)
