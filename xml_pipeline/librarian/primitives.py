"""
primitives.py — XML payload dataclasses for Premium Librarian.

These are the message types that flow through the organism's message bus.

Note: Do NOT use `from __future__ import annotations` here
as it breaks the xmlify decorator which needs concrete types.
"""

from dataclasses import dataclass

from third_party.xmlable import xmlify


@xmlify
@dataclass
class LibrarianIngest:
    """
    Request to ingest a codebase into the Premium Librarian.

    Supports git URLs. The library will be cloned, chunked, and stored
    in eXist-db for subsequent querying.
    """

    git_url: str = ""
    branch: str = "main"
    library_name: str = ""  # Optional; derived from URL if empty


@xmlify
@dataclass
class LibrarianIngested:
    """
    Response after successful codebase ingestion.

    Contains the library_id needed for subsequent queries.
    """

    library_id: str = ""
    library_name: str = ""
    files_processed: int = 0
    chunks_created: int = 0
    index_built: bool = False
    errors: str = ""  # Newline-separated error messages


@xmlify
@dataclass
class LibrarianQuery:
    """
    Query an ingested library with a natural language question.

    The system will search for relevant code chunks and synthesize
    an answer using the configured LLM.
    """

    library_id: str = ""
    question: str = ""
    max_chunks: int = 20  # Max chunks to include in context
    model: str = ""  # Optional; uses default if empty


@xmlify
@dataclass
class LibrarianAnswer:
    """
    Response to a library query.

    Contains the synthesized answer and source references.
    """

    answer: str = ""
    sources: str = ""  # XML-formatted source list
    tokens_used: int = 0
    chunks_examined: int = 0
    error: str = ""


@xmlify
@dataclass
class LibrarianList:
    """
    Request to list all ingested libraries.
    """

    pass  # No parameters needed


@xmlify
@dataclass
class LibraryInfo:
    """
    Information about a single ingested library.
    """

    library_id: str = ""
    name: str = ""
    source_url: str = ""
    created_at: str = ""
    total_files: int = 0
    total_chunks: int = 0


@xmlify
@dataclass
class LibrarianLibraries:
    """
    Response listing all ingested libraries.
    """

    count: int = 0
    libraries: str = ""  # XML-formatted library list


@xmlify
@dataclass
class LibrarianDelete:
    """
    Request to delete an ingested library.
    """

    library_id: str = ""


@xmlify
@dataclass
class LibrarianDeleted:
    """
    Response after library deletion.
    """

    library_id: str = ""
    success: bool = False
    error: str = ""


@xmlify
@dataclass
class LibrarianGetChunk:
    """
    Request to retrieve a specific code chunk.

    Useful for examining source code referenced in a query response.
    """

    library_id: str = ""
    chunk_id: str = ""


@xmlify
@dataclass
class LibrarianChunk:
    """
    Response with a specific code chunk.
    """

    chunk_id: str = ""
    file_path: str = ""
    name: str = ""
    chunk_type: str = ""
    language: str = ""
    start_line: int = 0
    end_line: int = 0
    content: str = ""
    docstring: str = ""
    signature: str = ""
    error: str = ""
