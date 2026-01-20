"""
Split configuration loader.

Loads organism configuration from multiple files:
- organism.yaml: Core settings (name, port, llm backends)
- listeners/*.yaml: Per-listener configurations

This enables:
- Cleaner separation of concerns
- Per-listener LSP-assisted editing
- Modular configuration management
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import glob as glob_module

import yaml

from .listeners.store import ListenerConfigStore, LISTENERS_DIR


class SplitConfigError(Exception):
    """Configuration loading/validation error."""
    pass


@dataclass
class OrganismCoreConfig:
    """
    Core organism configuration (from organism.yaml).

    Does not include listener details - those come from split files.
    """

    name: str
    port: int = 8765
    version: str = "0.1.0"
    description: str = ""

    # Thread scheduling
    thread_scheduling: str = "breadth-first"

    # Concurrency limits
    max_concurrent_pipelines: int = 100
    max_concurrent_handlers: int = 50
    max_concurrent_per_agent: int = 5

    # Listeners directory configuration
    listeners_directory: Optional[str] = None
    listeners_include: list[str] = field(default_factory=lambda: ["*.yaml"])

    # LLM configuration (kept in organism.yaml)
    llm: dict[str, Any] = field(default_factory=dict)

    # Server configuration
    server: dict[str, Any] = field(default_factory=dict)

    # Auth configuration
    auth: dict[str, Any] = field(default_factory=dict)

    # Meta configuration
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SplitOrganismConfig:
    """
    Complete organism configuration assembled from split files.
    """

    # Core config from organism.yaml
    core: OrganismCoreConfig

    # Listener configs (from split files or inlined)
    listeners: list[dict[str, Any]] = field(default_factory=list)

    # Source paths for debugging
    organism_path: Optional[Path] = None
    listener_paths: list[Path] = field(default_factory=list)


def load_organism_yaml(path: Path) -> tuple[dict[str, Any], OrganismCoreConfig]:
    """
    Load organism.yaml and extract core config.

    Returns (raw_data, core_config) tuple.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise SplitConfigError(f"Config must be a YAML mapping, got {type(raw)}")

    # Extract organism section
    org_raw = raw.get("organism", {})
    if not org_raw.get("name"):
        raise SplitConfigError("organism.name is required")

    # Extract listeners config
    listeners_raw = raw.get("listeners", {})
    if isinstance(listeners_raw, dict):
        # New split format: listeners: { directory: ..., include: [...] }
        listeners_dir = listeners_raw.get("directory")
        listeners_include = listeners_raw.get("include", ["*.yaml"])
    else:
        # Legacy format: listeners is a list - no split loading
        listeners_dir = None
        listeners_include = ["*.yaml"]

    core = OrganismCoreConfig(
        name=org_raw["name"],
        port=org_raw.get("port", 8765),
        version=org_raw.get("version", "0.1.0"),
        description=org_raw.get("description", ""),
        thread_scheduling=org_raw.get("thread_scheduling", "breadth-first"),
        max_concurrent_pipelines=org_raw.get("max_concurrent_pipelines", 100),
        max_concurrent_handlers=org_raw.get("max_concurrent_handlers", 50),
        max_concurrent_per_agent=org_raw.get("max_concurrent_per_agent", 5),
        listeners_directory=listeners_dir,
        listeners_include=listeners_include,
        llm=raw.get("llm", {}),
        server=raw.get("server", {}),
        auth=raw.get("auth", {}),
        meta=raw.get("meta", {}),
    )

    return raw, core


def load_listener_files(
    directory: Path,
    patterns: list[str],
) -> list[tuple[Path, dict[str, Any]]]:
    """
    Load all listener YAML files matching patterns.

    Returns list of (path, data) tuples.
    """
    results = []

    for pattern in patterns:
        full_pattern = str(directory / pattern)
        for filepath in glob_module.glob(full_pattern):
            path = Path(filepath)
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    # Ensure name is set from filename if not in file
                    if "name" not in data:
                        data["name"] = path.stem
                    results.append((path, data))
            except Exception as e:
                raise SplitConfigError(
                    f"Failed to load listener file {path}: {e}"
                )

    return results


def resolve_listeners_directory(
    config_dir: Optional[str],
    organism_path: Optional[Path] = None,
) -> Path:
    """
    Resolve the listeners directory path.

    Handles:
    - None -> default ~/.xml-pipeline/listeners
    - Absolute path -> use as-is
    - Relative path -> relative to organism.yaml location
    - ~ expansion
    """
    if config_dir is None:
        return LISTENERS_DIR

    # Expand user home
    expanded = Path(config_dir).expanduser()

    if expanded.is_absolute():
        return expanded

    # Relative to organism.yaml location
    if organism_path is not None:
        return organism_path.parent / expanded

    return expanded


def load_split_config(organism_path: Path) -> SplitOrganismConfig:
    """
    Load complete organism configuration from split files.

    If organism.yaml has listeners as a dict with 'directory' key,
    loads listener configs from that directory.

    If listeners is a list (legacy format), uses those directly.
    """
    raw, core = load_organism_yaml(organism_path)

    listeners: list[dict[str, Any]] = []
    listener_paths: list[Path] = []

    listeners_raw = raw.get("listeners", {})

    if isinstance(listeners_raw, list):
        # Legacy format: inline listeners
        listeners = listeners_raw
    elif isinstance(listeners_raw, dict):
        # Split format: load from directory
        listeners_dir = resolve_listeners_directory(
            core.listeners_directory,
            organism_path,
        )

        if listeners_dir.exists():
            loaded = load_listener_files(listeners_dir, core.listeners_include)
            for path, data in loaded:
                listeners.append(data)
                listener_paths.append(path)
    else:
        raise SplitConfigError(
            f"listeners must be a list or dict, got {type(listeners_raw)}"
        )

    return SplitOrganismConfig(
        core=core,
        listeners=listeners,
        organism_path=organism_path,
        listener_paths=listener_paths,
    )


def get_organism_yaml_path() -> Optional[Path]:
    """
    Get the default organism.yaml path.

    Searches in order:
    1. ~/.xml-pipeline/organism.yaml
    2. ./organism.yaml
    3. ./config/organism.yaml
    """
    candidates = [
        Path.home() / ".xml-pipeline" / "organism.yaml",
        Path("organism.yaml"),
        Path("config/organism.yaml"),
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def save_organism_yaml(config: OrganismCoreConfig, path: Path) -> None:
    """
    Save organism core config to YAML file.

    Preserves the split-file structure if listeners_directory is set.
    """
    data: dict[str, Any] = {
        "organism": {
            "name": config.name,
            "port": config.port,
            "version": config.version,
        }
    }

    if config.description:
        data["organism"]["description"] = config.description

    if config.thread_scheduling != "breadth-first":
        data["organism"]["thread_scheduling"] = config.thread_scheduling

    # Add listeners directory reference
    if config.listeners_directory:
        data["listeners"] = {
            "directory": config.listeners_directory,
            "include": config.listeners_include,
        }

    # Add other sections if non-empty
    if config.llm:
        data["llm"] = config.llm

    if config.server:
        data["server"] = config.server

    if config.auth:
        data["auth"] = config.auth

    if config.meta:
        data["meta"] = config.meta

    with open(path, "w") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )


def load_organism_yaml_content(path: Path) -> str:
    """Load organism.yaml content as string for editing."""
    with open(path) as f:
        return f.read()


def save_organism_yaml_content(path: Path, content: str) -> None:
    """Save organism.yaml content from string."""
    # Validate YAML before saving
    yaml.safe_load(content)

    with open(path, "w") as f:
        f.write(content)
