"""
File tools - sandboxed file system operations.

All paths are validated against configured allowed directories.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional, List

from .base import tool, ToolResult


# Security configuration
_allowed_paths: List[Path] = []
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_LISTING_ENTRIES = 1000


def configure_allowed_paths(paths: List[str | Path]) -> None:
    global _allowed_paths
    _allowed_paths = [Path(p).resolve() for p in paths]


def _validate_path(path: str) -> tuple[Optional[str], Optional[Path]]:
    if not _allowed_paths:
        try:
            return None, Path(path).resolve()
        except Exception as e:
            return f"Invalid path: {e}", None
    try:
        resolved = Path(path).resolve()
    except Exception as e:
        return f"Invalid path: {e}", None
    if ".." in str(path):
        return "Path traversal (..) not allowed", None
    for allowed in _allowed_paths:
        try:
            resolved.relative_to(allowed)
            return None, resolved
        except ValueError:
            continue
    return "Path not in allowed directories", None


@tool
async def read_file(
    path: str,
    encoding: str = "utf-8",
    binary: bool = False,
    offset: int = 0,
    limit: Optional[int] = None,
) -> ToolResult:
    error, resolved = _validate_path(path)
    if error:
        return ToolResult(success=False, error=error)
    if not resolved.exists():
        return ToolResult(success=False, error=f"File not found: {path}")
    if not resolved.is_file():
        return ToolResult(success=False, error=f"Not a file: {path}")
    try:
        file_size = resolved.stat().st_size
        read_size = min(limit or MAX_FILE_SIZE, MAX_FILE_SIZE)
        if binary:
            with open(resolved, "rb") as f:
                if offset:
                    f.seek(offset)
                content = f.read(read_size)
            return ToolResult(success=True, data={
                "content": base64.b64encode(content).decode("ascii"),
                "size": file_size,
                "encoding": "base64",
            })
        else:
            with open(resolved, "r", encoding=encoding) as f:
                if offset:
                    f.seek(offset)
                content = f.read(read_size)
            return ToolResult(success=True, data={
                "content": content,
                "size": file_size,
                "encoding": encoding,
            })
    except UnicodeDecodeError:
        return ToolResult(success=False, error=f"Cannot decode as {encoding}. Try binary=true.")
    except Exception as e:
        return ToolResult(success=False, error=f"Read error: {e}")


@tool
async def write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
    encoding: str = "utf-8",
    binary: bool = False,
    create_dirs: bool = False,
) -> ToolResult:
    error, resolved = _validate_path(path)
    if error:
        return ToolResult(success=False, error=error)
    if binary:
        try:
            data = base64.b64decode(content)
        except Exception as e:
            return ToolResult(success=False, error=f"Invalid base64: {e}")
    else:
        data = content.encode(encoding)
    if len(data) > MAX_FILE_SIZE:
        return ToolResult(success=False, error=f"Content too large: {len(data)} bytes")
    try:
        if create_dirs:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            write_mode = "ab" if mode == "append" else "wb"
            with open(resolved, write_mode) as f:
                f.write(data)
        else:
            if mode == "append":
                with open(resolved, "a", encoding=encoding) as f:
                    f.write(content)
            else:
                resolved.write_text(content, encoding=encoding)
        return ToolResult(success=True, data={"bytes_written": len(data), "path": str(resolved)})
    except Exception as e:
        return ToolResult(success=False, error=f"Write error: {e}")


@tool
async def list_dir(
    path: str,
    pattern: str = "*",
    recursive: bool = False,
    include_hidden: bool = False,
) -> ToolResult:
    error, resolved = _validate_path(path)
    if error:
        return ToolResult(success=False, error=error)
    if not resolved.exists():
        return ToolResult(success=False, error=f"Directory not found: {path}")
    if not resolved.is_dir():
        return ToolResult(success=False, error=f"Not a directory: {path}")
    try:
        entries = []
        glob_method = resolved.rglob if recursive else resolved.glob
        for entry in glob_method(pattern):
            if not include_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                entries.append({
                    "name": str(entry.relative_to(resolved)),
                    "type": "dir" if entry.is_dir() else "file",
                    "size": stat.st_size if entry.is_file() else None,
                    "modified": stat.st_mtime,
                })
            except (OSError, PermissionError):
                continue
            if len(entries) >= MAX_LISTING_ENTRIES:
                break
        entries.sort(key=lambda e: e["name"])
        return ToolResult(success=True, data={
            "entries": entries,
            "count": len(entries),
            "truncated": len(entries) >= MAX_LISTING_ENTRIES,
        })
    except Exception as e:
        return ToolResult(success=False, error=f"List error: {e}")


@tool
async def delete_file(path: str, recursive: bool = False) -> ToolResult:
    error, resolved = _validate_path(path)
    if error:
        return ToolResult(success=False, error=error)
    if not resolved.exists():
        return ToolResult(success=False, error=f"Path not found: {path}")
    try:
        if resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            if not recursive:
                return ToolResult(success=False, error="Cannot delete directory without recursive=true")
            import shutil
            shutil.rmtree(resolved)
        else:
            return ToolResult(success=False, error=f"Unknown file type: {path}")
        return ToolResult(success=True, data={"deleted": True, "path": str(resolved)})
    except Exception as e:
        return ToolResult(success=False, error=f"Delete error: {e}")
