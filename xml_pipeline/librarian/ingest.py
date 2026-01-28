"""
ingest.py — Codebase ingestion for Premium Librarian.

Clones git repositories, walks files, chunks them, and stores in eXist-db.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional
from xml.sax.saxutils import escape as xml_escape

from xml_pipeline.librarian.chunker import Chunk, chunk_file, detect_language

logger = logging.getLogger(__name__)


# File patterns to skip during ingestion
SKIP_PATTERNS = {
    # Version control
    ".git",
    ".svn",
    ".hg",
    # Dependencies
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    # Build artifacts
    "dist",
    "build",
    "target",
    "out",
    ".next",
    # IDE
    ".idea",
    ".vscode",
    # OS
    ".DS_Store",
    "Thumbs.db",
}

# File extensions to process
CODE_EXTENSIONS = {
    ".py", ".pyi",  # Python
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",  # JavaScript/TypeScript
    ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx",  # C/C++
    ".rs",  # Rust
    ".go",  # Go
    ".java",  # Java
    ".kt", ".kts",  # Kotlin
    ".rb",  # Ruby
    ".php",  # PHP
    ".cs",  # C#
    ".swift",  # Swift
    ".scala",  # Scala
    ".md", ".rst", ".txt",  # Documentation
    ".yaml", ".yml", ".toml", ".json",  # Config
    ".xml", ".xsd",  # XML
    ".sql",  # SQL
    ".sh", ".bash", ".zsh",  # Shell
    ".dockerfile", ".containerfile",  # Docker
}

# Max file size to process (1MB)
MAX_FILE_SIZE = 1024 * 1024


@dataclass
class IngestResult:
    """Result of a codebase ingestion."""

    library_id: str
    library_name: str
    files_processed: int
    chunks_created: int
    index_built: bool
    errors: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class IngestConfig:
    """Configuration for ingestion."""

    branch: str = "main"
    max_file_size: int = MAX_FILE_SIZE
    skip_patterns: set[str] = field(default_factory=lambda: SKIP_PATTERNS.copy())
    extensions: set[str] = field(default_factory=lambda: CODE_EXTENSIONS.copy())


def _should_skip_path(path: Path, config: IngestConfig) -> bool:
    """Check if a path should be skipped."""
    for part in path.parts:
        if part in config.skip_patterns:
            return True
        if part.startswith(".") and part not in {".github", ".gitlab"}:
            return True
    return False


def _should_process_file(path: Path, config: IngestConfig) -> bool:
    """Check if a file should be processed."""
    # Check extension
    suffix = path.suffix.lower()
    if suffix not in config.extensions:
        # Also check for files without extension (Dockerfile, Makefile, etc.)
        name_lower = path.name.lower()
        if name_lower not in {"dockerfile", "makefile", "rakefile", "gemfile"}:
            return False

    # Check size
    try:
        if path.stat().st_size > config.max_file_size:
            return False
    except OSError:
        return False

    return True


async def _clone_repo(url: str, branch: str, target_dir: Path) -> None:
    """Clone a git repository."""
    try:
        # Try using GitPython
        from git import Repo
        logger.info(f"Cloning {url} (branch: {branch}) to {target_dir}")
        Repo.clone_from(url, target_dir, branch=branch, depth=1)
    except ImportError:
        # Fall back to git CLI
        logger.info(f"GitPython not available, using git CLI")
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--branch", branch, url, str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode()}")


async def _walk_files(root: Path, config: IngestConfig) -> AsyncIterator[Path]:
    """Walk directory tree, yielding files to process."""
    for path in root.rglob("*"):
        if path.is_file():
            rel_path = path.relative_to(root)
            if not _should_skip_path(rel_path, config):
                if _should_process_file(path, config):
                    yield path


def _chunk_to_xml(chunk: Chunk, library_id: str) -> str:
    """Convert a chunk to XML document for storage."""
    # Escape content for XML
    content_escaped = xml_escape(chunk.content)
    docstring_escaped = xml_escape(chunk.docstring) if chunk.docstring else ""
    signature_escaped = xml_escape(chunk.signature) if chunk.signature else ""

    imports_xml = "\n".join(f"    <import>{xml_escape(imp)}</import>" for imp in chunk.imports)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<chunk xmlns="https://xml-pipeline.org/ns/librarian/v1">
  <id>{xml_escape(chunk.chunk_id)}</id>
  <library-id>{xml_escape(library_id)}</library-id>
  <file-path>{xml_escape(chunk.file_path)}</file-path>
  <start-line>{chunk.start_line}</start-line>
  <end-line>{chunk.end_line}</end-line>
  <chunk-type>{xml_escape(chunk.chunk_type)}</chunk-type>
  <name>{xml_escape(chunk.name)}</name>
  <language>{xml_escape(chunk.language)}</language>
  <parent-class>{xml_escape(chunk.parent_class)}</parent-class>
  <signature>{signature_escaped}</signature>
  <docstring>{docstring_escaped}</docstring>
  <imports>
{imports_xml}
  </imports>
  <content><![CDATA[{chunk.content}]]></content>
</chunk>"""


async def _store_chunk(
    chunk: Chunk,
    library_id: str,
    collection: str,
) -> bool:
    """Store a chunk in eXist-db."""
    from xml_pipeline.tools.librarian import librarian_store

    xml_content = _chunk_to_xml(chunk, library_id)

    # Generate document name from chunk ID
    doc_name = f"{chunk.chunk_id.replace(':', '_').replace('/', '_')}.xml"

    result = await librarian_store(
        collection=collection,
        document_name=doc_name,
        content=xml_content,
    )

    return result.success


async def ingest_git_repo(
    url: str,
    branch: str = "main",
    library_name: str = "",
    config: Optional[IngestConfig] = None,
) -> IngestResult:
    """
    Clone and ingest a git repository.

    Args:
        url: Git repository URL
        branch: Branch to clone (default: main)
        library_name: Human-readable name (derived from URL if empty)
        config: Ingestion configuration

    Returns:
        IngestResult with statistics and library_id
    """
    if config is None:
        config = IngestConfig(branch=branch)

    # Derive library name from URL if not provided
    if not library_name:
        # Extract repo name from URL
        # https://github.com/user/repo.git -> repo
        # git@github.com:user/repo.git -> repo
        name = url.rstrip("/").rstrip(".git").split("/")[-1].split(":")[-1]
        library_name = name

    # Generate unique library ID
    library_id = f"{library_name}-{uuid.uuid4().hex[:8]}"

    result = IngestResult(
        library_id=library_id,
        library_name=library_name,
        files_processed=0,
        chunks_created=0,
        index_built=False,
    )

    # Create temp directory for clone
    temp_dir = Path(tempfile.mkdtemp(prefix="librarian_"))

    try:
        # Clone repository
        await _clone_repo(url, config.branch, temp_dir)

        # Collection path in eXist-db
        collection = f"/db/librarian/{library_id}/chunks"

        # Track language statistics
        lang_stats: dict[str, int] = {}

        # Process files
        async for file_path in _walk_files(temp_dir, config):
            try:
                # Read file content
                content = file_path.read_text(encoding="utf-8", errors="replace")

                # Get relative path for storage
                rel_path = str(file_path.relative_to(temp_dir))

                # Detect language and update stats
                language = detect_language(rel_path)
                lang_stats[language] = lang_stats.get(language, 0) + 1

                # Chunk the file
                chunks = chunk_file(content, rel_path)

                # Store each chunk
                for chunk in chunks:
                    success = await _store_chunk(chunk, library_id, collection)
                    if success:
                        result.chunks_created += 1
                    else:
                        result.errors.append(f"Failed to store chunk: {chunk.chunk_id}")

                result.files_processed += 1

            except Exception as e:
                result.errors.append(f"Error processing {file_path}: {e}")
                logger.warning(f"Error processing {file_path}: {e}")

        result.stats = lang_stats

        # Build index
        from xml_pipeline.librarian.index import build_index

        try:
            await build_index(library_id, library_name, url)
            result.index_built = True
        except Exception as e:
            result.errors.append(f"Index build failed: {e}")
            logger.warning(f"Index build failed: {e}")

        logger.info(
            f"Ingested {library_name}: {result.files_processed} files, "
            f"{result.chunks_created} chunks"
        )

    finally:
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result


async def ingest_directory(
    path: str | Path,
    library_name: str,
    config: Optional[IngestConfig] = None,
) -> IngestResult:
    """
    Ingest a local directory (for testing or local codebases).

    Args:
        path: Path to directory
        library_name: Human-readable name
        config: Ingestion configuration

    Returns:
        IngestResult with statistics and library_id
    """
    if config is None:
        config = IngestConfig()

    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {path}")

    # Generate unique library ID
    library_id = f"{library_name}-{uuid.uuid4().hex[:8]}"

    result = IngestResult(
        library_id=library_id,
        library_name=library_name,
        files_processed=0,
        chunks_created=0,
        index_built=False,
    )

    collection = f"/db/librarian/{library_id}/chunks"
    lang_stats: dict[str, int] = {}

    async for file_path in _walk_files(root, config):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            rel_path = str(file_path.relative_to(root))

            language = detect_language(rel_path)
            lang_stats[language] = lang_stats.get(language, 0) + 1

            chunks = chunk_file(content, rel_path)

            for chunk in chunks:
                success = await _store_chunk(chunk, library_id, collection)
                if success:
                    result.chunks_created += 1
                else:
                    result.errors.append(f"Failed to store chunk: {chunk.chunk_id}")

            result.files_processed += 1

        except Exception as e:
            result.errors.append(f"Error processing {file_path}: {e}")
            logger.warning(f"Error processing {file_path}: {e}")

    result.stats = lang_stats

    # Build index
    from xml_pipeline.librarian.index import build_index

    try:
        await build_index(library_id, library_name, str(root))
        result.index_built = True
    except Exception as e:
        result.errors.append(f"Index build failed: {e}")

    return result
