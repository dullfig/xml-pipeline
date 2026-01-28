"""
Premium Librarian — RLM-powered codebase intelligence.

Ingests codebases, chunks them intelligently, stores in eXist-db,
and answers natural language queries using Online LLM + RAG.

Usage:
    from xml_pipeline.librarian import ingest_git_repo, query_library

    # Ingest a codebase
    result = await ingest_git_repo(
        url="https://github.com/example/repo.git",
        library_name="my-lib",
    )

    # Query it
    answer = await query_library(
        library_id=result.library_id,
        question="What does this codebase do?",
    )
"""

from xml_pipeline.librarian.chunker import (
    Chunk,
    chunk_file,
    chunk_python,
    chunk_javascript,
    chunk_cpp,
    detect_language,
)
from xml_pipeline.librarian.ingest import (
    IngestResult,
    ingest_git_repo,
)
from xml_pipeline.librarian.index import (
    LibraryIndex,
    build_index,
    get_index,
)
from xml_pipeline.librarian.query import (
    Source,
    QueryResult,
    query_library,
)
from xml_pipeline.librarian.primitives import (
    LibrarianIngest,
    LibrarianIngested,
    LibrarianQuery,
    LibrarianAnswer,
    LibrarianList,
    LibrarianLibraries,
    LibrarianDelete,
    LibrarianDeleted,
    LibrarianGetChunk,
    LibrarianChunk,
    LibraryInfo,
)
from xml_pipeline.librarian.handler import (
    handle_librarian_ingest,
    handle_librarian_query,
    handle_librarian_list,
    handle_librarian_delete,
    handle_librarian_get_chunk,
)

__all__ = [
    # Chunker
    "Chunk",
    "chunk_file",
    "chunk_python",
    "chunk_javascript",
    "chunk_cpp",
    "detect_language",
    # Ingest
    "IngestResult",
    "ingest_git_repo",
    # Index
    "LibraryIndex",
    "build_index",
    "get_index",
    # Query
    "Source",
    "QueryResult",
    "query_library",
    # Primitives
    "LibrarianIngest",
    "LibrarianIngested",
    "LibrarianQuery",
    "LibrarianAnswer",
    "LibrarianList",
    "LibrarianLibraries",
    "LibrarianDelete",
    "LibrarianDeleted",
    "LibrarianGetChunk",
    "LibrarianChunk",
    "LibraryInfo",
    # Handlers
    "handle_librarian_ingest",
    "handle_librarian_query",
    "handle_librarian_list",
    "handle_librarian_delete",
    "handle_librarian_get_chunk",
]
