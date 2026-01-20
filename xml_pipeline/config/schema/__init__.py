"""
JSON Schema management for YAML Language Server.

Provides JSON schemas for organism.yaml and listener.yaml files,
enabling LSP-powered autocompletion and validation in the editor.

Schemas are written to ~/.xml-pipeline/schemas/ for yaml-language-server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .organism import ORGANISM_SCHEMA
from .listener import LISTENER_SCHEMA


SCHEMA_DIR = Path.home() / ".xml-pipeline" / "schemas"

SCHEMA_FILES = {
    "organism.schema.json": ORGANISM_SCHEMA,
    "listener.schema.json": LISTENER_SCHEMA,
}


def ensure_schema_dir() -> Path:
    """Create schema directory if needed."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    return SCHEMA_DIR


def write_schemas() -> dict[str, Path]:
    """
    Write all schemas to the schema directory.

    Returns dict of schema_name -> path.
    """
    ensure_schema_dir()
    paths = {}

    for name, schema in SCHEMA_FILES.items():
        path = SCHEMA_DIR / name
        with open(path, "w") as f:
            json.dump(schema, f, indent=2)
        paths[name] = path

    return paths


def get_schema_path(schema_type: str) -> Optional[Path]:
    """
    Get path to a schema file.

    Args:
        schema_type: "organism" or "listener"

    Returns path if exists, None otherwise.
    """
    filename = f"{schema_type}.schema.json"
    path = SCHEMA_DIR / filename

    if not path.exists():
        # Write schemas if not present
        write_schemas()

    return path if path.exists() else None


def ensure_schemas() -> dict[str, Path]:
    """
    Ensure all schemas are written and up to date.

    Call this at startup to make sure schemas are available.
    Returns dict of schema_name -> path.
    """
    return write_schemas()


def get_schema_modeline(schema_type: str) -> str:
    """
    Get the YAML modeline for a schema type.

    Args:
        schema_type: "organism" or "listener"

    Returns modeline string like:
        # yaml-language-server: $schema=~/.xml-pipeline/schemas/listener.schema.json
    """
    return f"# yaml-language-server: $schema=~/.xml-pipeline/schemas/{schema_type}.schema.json"


__all__ = [
    "ORGANISM_SCHEMA",
    "LISTENER_SCHEMA",
    "SCHEMA_DIR",
    "ensure_schemas",
    "get_schema_path",
    "get_schema_modeline",
    "write_schemas",
]
