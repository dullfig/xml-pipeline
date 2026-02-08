"""
wit_parser.py — Minimal WIT interface parser.

Extracts record definitions, interface blocks, and type mappings from .wit files.
Not full WIT spec compliant — just enough for our handler contract.

Each interface MUST have exactly one *-request record and one *-response record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


class WitValidationError(Exception):
    """Raised when a .wit file violates our expected contract."""


# ============================================================================
# Data structures
# ============================================================================

@dataclass
class WitField:
    name: str          # e.g. "expression" (snake_case, kebab converted)
    wit_type: str      # e.g. "string"
    python_type: type  # e.g. str


@dataclass
class WitRecord:
    name: str          # e.g. "calculate-request" (original kebab-case)
    fields: List[WitField] = field(default_factory=list)


@dataclass
class WitInterface:
    name: str          # e.g. "calculate"
    request: WitRecord = field(default=None)  # type: ignore[assignment]
    response: WitRecord = field(default=None)  # type: ignore[assignment]


# ============================================================================
# Type mapping
# ============================================================================

WIT_TYPE_MAP: dict[str, type] = {
    "string": str,
    "bool": int,      # 0/1, xmlify-safe
    "u8": int,
    "u16": int,
    "u32": int,
    "u64": int,
    "s8": int,
    "s16": int,
    "s32": int,
    "s64": int,
    "f32": float,
    "f64": float,
}


def _resolve_type(wit_type: str) -> type:
    """Resolve a WIT type to a Python type."""
    stripped = wit_type.strip()

    # option<T> → str (empty string when None)
    if stripped.startswith("option<") and stripped.endswith(">"):
        return str

    # list<T> → str (JSON-encoded)
    if stripped.startswith("list<") and stripped.endswith(">"):
        return str

    result = WIT_TYPE_MAP.get(stripped)
    if result is None:
        raise WitValidationError(f"Unsupported WIT type: {wit_type!r}")
    return result


def _kebab_to_snake(name: str) -> str:
    """Convert kebab-case to snake_case."""
    return name.replace("-", "_")


# ============================================================================
# Parser
# ============================================================================

_RECORD_RE = re.compile(r"^\s*record\s+([\w-]+)\s*\{")
_FIELD_RE = re.compile(r"^\s*([\w-]+)\s*:\s*(.+?)\s*,?\s*$")
_INTERFACE_RE = re.compile(r"^\s*interface\s+([\w-]+)\s*\{")
_PACKAGE_RE = re.compile(r"^\s*package\s+(.+?)\s*;")


def parse_wit(source: str) -> List[WitInterface]:
    """
    Parse WIT source text and return extracted interfaces.

    Each interface must contain exactly one *-request and one *-response record.
    Records outside interfaces are collected and matched by naming convention.
    """
    lines = source.splitlines()

    # First pass: extract all records
    records: dict[str, WitRecord] = {}
    interfaces: dict[str, dict] = {}  # name -> {"request": str, "response": str}

    i = 0
    current_interface: str | None = None
    current_record: WitRecord | None = None
    brace_depth = 0
    interface_brace_depth = 0
    interface_records: dict[str, list[str]] = {}  # interface_name -> [record_names]

    while i < len(lines):
        line = lines[i].strip()

        # Skip comments and empty lines
        if not line or line.startswith("//"):
            i += 1
            continue

        # Package declaration (ignored, just parsed)
        if _PACKAGE_RE.match(line):
            i += 1
            continue

        # Interface start
        m = _INTERFACE_RE.match(line)
        if m and current_record is None:
            current_interface = m.group(1)
            interface_brace_depth = brace_depth + 1
            brace_depth += line.count("{") - line.count("}")
            interface_records[current_interface] = []
            i += 1
            continue

        # Record start
        m = _RECORD_RE.match(line)
        if m:
            record_name = m.group(1)
            current_record = WitRecord(name=record_name)
            if current_interface:
                interface_records.setdefault(current_interface, []).append(record_name)

            # Handle single-line records: record foo { a: u32, b: u32, }
            if "}" in line:
                # Extract fields between { and }
                inner = line.split("{", 1)[1].rsplit("}", 1)[0].strip()
                if inner:
                    for part in inner.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        fm = _FIELD_RE.match(part)
                        if fm:
                            field_name = _kebab_to_snake(fm.group(1))
                            wit_type = fm.group(2).rstrip(",").strip()
                            python_type = _resolve_type(wit_type)
                            current_record.fields.append(WitField(
                                name=field_name,
                                wit_type=wit_type,
                                python_type=python_type,
                            ))
                records[current_record.name] = current_record
                brace_depth += line.count("{") - line.count("}")
                current_record = None
            else:
                brace_depth += line.count("{") - line.count("}")
            i += 1
            continue

        # Inside a record: parse fields
        if current_record is not None:
            brace_depth += line.count("{") - line.count("}")

            if "}" in line:
                # Check if we also have a field on this line before the brace
                field_part = line.split("}")[0].strip()
                if field_part:
                    fm = _FIELD_RE.match(field_part)
                    if fm:
                        field_name = _kebab_to_snake(fm.group(1))
                        wit_type = fm.group(2).rstrip(",").strip()
                        python_type = _resolve_type(wit_type)
                        current_record.fields.append(WitField(
                            name=field_name,
                            wit_type=wit_type,
                            python_type=python_type,
                        ))

                records[current_record.name] = current_record
                current_record = None
            else:
                fm = _FIELD_RE.match(line)
                if fm:
                    field_name = _kebab_to_snake(fm.group(1))
                    wit_type = fm.group(2).rstrip(",").strip()
                    python_type = _resolve_type(wit_type)
                    current_record.fields.append(WitField(
                        name=field_name,
                        wit_type=wit_type,
                        python_type=python_type,
                    ))

            i += 1
            continue

        # Track brace depth for interfaces
        brace_depth += line.count("{") - line.count("}")
        if current_interface and brace_depth < interface_brace_depth:
            current_interface = None

        i += 1

    # Second pass: build interfaces from records
    result: List[WitInterface] = []

    # If we found explicit interface blocks, use those
    if interface_records:
        for iface_name, rec_names in interface_records.items():
            iface = WitInterface(name=iface_name)
            for rname in rec_names:
                rec = records.get(rname)
                if rec is None:
                    continue
                if rname.endswith("-request"):
                    iface.request = rec
                elif rname.endswith("-response"):
                    iface.response = rec

            if iface.request is None:
                raise WitValidationError(
                    f"Interface '{iface_name}' missing *-request record"
                )
            if iface.response is None:
                raise WitValidationError(
                    f"Interface '{iface_name}' missing *-response record"
                )
            result.append(iface)
    else:
        # Fallback: match records by naming convention
        # Group by prefix: "calculate-request" + "calculate-response" → "calculate"
        prefixes: dict[str, dict[str, WitRecord]] = {}
        for rname, rec in records.items():
            if rname.endswith("-request"):
                prefix = rname[: -len("-request")]
                prefixes.setdefault(prefix, {})["request"] = rec
            elif rname.endswith("-response"):
                prefix = rname[: -len("-response")]
                prefixes.setdefault(prefix, {})["response"] = rec

        for prefix, pair in prefixes.items():
            if "request" not in pair:
                raise WitValidationError(
                    f"Record '{prefix}-response' found but '{prefix}-request' missing"
                )
            if "response" not in pair:
                raise WitValidationError(
                    f"Record '{prefix}-request' found but '{prefix}-response' missing"
                )
            result.append(WitInterface(
                name=prefix,
                request=pair["request"],
                response=pair["response"],
            ))

    if not result:
        raise WitValidationError("No interfaces found in WIT source")

    return result


def parse_wit_file(path: str | Path) -> List[WitInterface]:
    """Parse a .wit file from disk."""
    return parse_wit(Path(path).read_text(encoding="utf-8"))
