"""
index.py — Library index management for Premium Librarian.

Builds and queries structural indices for ingested codebases.
The index provides fast lookup of files, functions, and classes
without needing full-text search.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger(__name__)


@dataclass
class LibraryIndex:
    """Structural index for an ingested library."""

    library_id: str
    name: str
    source_url: str
    created_at: str
    files: list[str] = field(default_factory=list)
    functions: dict[str, str] = field(default_factory=dict)  # name → file path
    classes: dict[str, str] = field(default_factory=dict)  # name → file path
    modules: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def total_chunks(self) -> int:
        """Total number of chunks in this library."""
        return self.stats.get("chunks", 0)

    @property
    def total_files(self) -> int:
        """Total number of files in this library."""
        return len(self.files)


def _index_to_xml(index: LibraryIndex) -> str:
    """Convert index to XML document for storage."""
    files_xml = "\n".join(f"    <file>{xml_escape(f)}</file>" for f in index.files)

    functions_xml = "\n".join(
        f'    <function name="{xml_escape(name)}" file="{xml_escape(path)}"/>'
        for name, path in index.functions.items()
    )

    classes_xml = "\n".join(
        f'    <class name="{xml_escape(name)}" file="{xml_escape(path)}"/>'
        for name, path in index.classes.items()
    )

    modules_xml = "\n".join(f"    <module>{xml_escape(m)}</module>" for m in index.modules)

    stats_xml = "\n".join(
        f'    <stat name="{xml_escape(k)}">{v}</stat>'
        for k, v in index.stats.items()
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<library-index xmlns="https://xml-pipeline.org/ns/librarian/v1">
  <library-id>{xml_escape(index.library_id)}</library-id>
  <name>{xml_escape(index.name)}</name>
  <source-url>{xml_escape(index.source_url)}</source-url>
  <created-at>{xml_escape(index.created_at)}</created-at>
  <files>
{files_xml}
  </files>
  <functions>
{functions_xml}
  </functions>
  <classes>
{classes_xml}
  </classes>
  <modules>
{modules_xml}
  </modules>
  <stats>
{stats_xml}
  </stats>
</library-index>"""


def _parse_index_xml(xml_content: str) -> Optional[LibraryIndex]:
    """Parse index XML back to LibraryIndex object."""
    try:
        from lxml import etree

        root = etree.fromstring(xml_content.encode())
        ns = {"l": "https://xml-pipeline.org/ns/librarian/v1"}

        library_id = root.findtext("l:library-id", "", namespaces=ns)
        name = root.findtext("l:name", "", namespaces=ns)
        source_url = root.findtext("l:source-url", "", namespaces=ns)
        created_at = root.findtext("l:created-at", "", namespaces=ns)

        files = [f.text or "" for f in root.findall("l:files/l:file", namespaces=ns)]

        functions = {
            f.get("name", ""): f.get("file", "")
            for f in root.findall("l:functions/l:function", namespaces=ns)
        }

        classes = {
            c.get("name", ""): c.get("file", "")
            for c in root.findall("l:classes/l:class", namespaces=ns)
        }

        modules = [m.text or "" for m in root.findall("l:modules/l:module", namespaces=ns)]

        stats = {
            s.get("name", ""): int(s.text or 0)
            for s in root.findall("l:stats/l:stat", namespaces=ns)
        }

        return LibraryIndex(
            library_id=library_id,
            name=name,
            source_url=source_url,
            created_at=created_at,
            files=files,
            functions=functions,
            classes=classes,
            modules=modules,
            stats=stats,
        )

    except Exception as e:
        logger.error(f"Failed to parse index XML: {e}")
        return None


async def build_index(
    library_id: str,
    library_name: str,
    source_url: str,
) -> LibraryIndex:
    """
    Build structural index from stored chunks.

    Queries eXist-db for all chunks belonging to this library
    and extracts structural information.
    """
    from xml_pipeline.tools.librarian import librarian_query, librarian_store

    # Query for all chunks in this library
    xquery = f"""
    declare namespace l = "https://xml-pipeline.org/ns/librarian/v1";
    for $chunk in collection("/db/librarian/{library_id}/chunks")//l:chunk
    return <item>
      <file>{{$chunk/l:file-path/text()}}</file>
      <type>{{$chunk/l:chunk-type/text()}}</type>
      <name>{{$chunk/l:name/text()}}</name>
      <language>{{$chunk/l:language/text()}}</language>
    </item>
    """

    result = await librarian_query(query=xquery, collection=f"/db/librarian/{library_id}")

    if not result.success:
        logger.warning(f"Failed to query chunks for index: {result.error}")
        # Create minimal index
        index = LibraryIndex(
            library_id=library_id,
            name=library_name,
            source_url=source_url,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    else:
        # Parse results
        files: set[str] = set()
        functions: dict[str, str] = {}
        classes: dict[str, str] = {}
        modules: list[str] = []
        lang_stats: dict[str, int] = {}
        chunk_count = 0

        try:
            from lxml import etree

            # Wrap results in root element for parsing
            xml_str = f"<results>{result.data.get('results', '')}</results>"
            root = etree.fromstring(xml_str.encode())

            for item in root.findall("item"):
                chunk_count += 1
                file_path = item.findtext("file", "")
                chunk_type = item.findtext("type", "")
                name = item.findtext("name", "")
                language = item.findtext("language", "")

                if file_path:
                    files.add(file_path)

                if chunk_type == "function" or chunk_type == "method":
                    functions[name] = file_path
                elif chunk_type == "class":
                    classes[name] = file_path
                elif chunk_type == "module":
                    modules.append(file_path)

                if language:
                    lang_stats[language] = lang_stats.get(language, 0) + 1

        except Exception as e:
            logger.warning(f"Failed to parse chunk query results: {e}")

        index = LibraryIndex(
            library_id=library_id,
            name=library_name,
            source_url=source_url,
            created_at=datetime.now(timezone.utc).isoformat(),
            files=sorted(files),
            functions=functions,
            classes=classes,
            modules=modules,
            stats={
                "chunks": chunk_count,
                "files": len(files),
                "functions": len(functions),
                "classes": len(classes),
                **{f"lang_{k}": v for k, v in lang_stats.items()},
            },
        )

    # Store index document
    index_xml = _index_to_xml(index)
    store_result = await librarian_store(
        collection=f"/db/librarian/{library_id}",
        document_name="index.xml",
        content=index_xml,
    )

    if not store_result.success:
        logger.warning(f"Failed to store index: {store_result.error}")

    return index


async def get_index(library_id: str) -> Optional[LibraryIndex]:
    """
    Retrieve library index from eXist-db.

    Returns None if index doesn't exist.
    """
    from xml_pipeline.tools.librarian import librarian_get

    result = await librarian_get(f"/db/librarian/{library_id}/index.xml")

    if not result.success:
        return None

    content = result.data.get("content", "")
    return _parse_index_xml(content)


async def list_libraries() -> list[LibraryIndex]:
    """
    List all ingested libraries.

    Returns list of LibraryIndex objects for all libraries in eXist-db.
    """
    from xml_pipeline.tools.librarian import librarian_query

    xquery = """
    declare namespace l = "https://xml-pipeline.org/ns/librarian/v1";
    for $index in collection("/db/librarian")//l:library-index
    return $index
    """

    result = await librarian_query(query=xquery, collection="/db/librarian")

    if not result.success:
        logger.warning(f"Failed to list libraries: {result.error}")
        return []

    libraries: list[LibraryIndex] = []

    try:
        from lxml import etree

        # Parse each index document
        xml_str = result.data.get("results", "")
        if xml_str.strip():
            # Wrap in root element
            wrapped = f"<results>{xml_str}</results>"
            root = etree.fromstring(wrapped.encode())

            for index_elem in root.findall(
                "{https://xml-pipeline.org/ns/librarian/v1}library-index"
            ):
                index_xml = etree.tostring(index_elem, encoding="unicode")
                index = _parse_index_xml(index_xml)
                if index:
                    libraries.append(index)

    except Exception as e:
        logger.warning(f"Failed to parse library list: {e}")

    return libraries


async def delete_library(library_id: str) -> bool:
    """
    Delete a library and all its chunks from eXist-db.

    Returns True if successful.
    """
    from xml_pipeline.tools.librarian import librarian_query

    # Delete the entire collection
    xquery = f"""
    xmldb:remove("/db/librarian/{library_id}")
    """

    result = await librarian_query(query=xquery)

    if not result.success:
        logger.warning(f"Failed to delete library {library_id}: {result.error}")
        return False

    return True
