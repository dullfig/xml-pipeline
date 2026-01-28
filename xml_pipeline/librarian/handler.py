"""
handler.py — Message handlers for Premium Librarian.

These handlers process librarian requests through the organism's message bus.
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape as xml_escape

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

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
)

logger = logging.getLogger(__name__)


async def handle_librarian_ingest(
    payload: LibrarianIngest,
    metadata: HandlerMetadata,
) -> HandlerResponse:
    """
    Handle a codebase ingestion request.

    Clones the git repository, chunks all files, and stores in eXist-db.
    """
    from xml_pipeline.librarian.ingest import ingest_git_repo

    logger.info(f"Ingesting codebase from {payload.git_url}")

    try:
        result = await ingest_git_repo(
            url=payload.git_url,
            branch=payload.branch,
            library_name=payload.library_name,
        )

        return HandlerResponse.respond(
            payload=LibrarianIngested(
                library_id=result.library_id,
                library_name=result.library_name,
                files_processed=result.files_processed,
                chunks_created=result.chunks_created,
                index_built=result.index_built,
                errors="\n".join(result.errors) if result.errors else "",
            )
        )

    except Exception as e:
        logger.error(f"Ingest failed: {e}")
        return HandlerResponse.respond(
            payload=LibrarianIngested(
                library_id="",
                library_name=payload.library_name or "",
                files_processed=0,
                chunks_created=0,
                index_built=False,
                errors=str(e),
            )
        )


async def handle_librarian_query(
    payload: LibrarianQuery,
    metadata: HandlerMetadata,
) -> HandlerResponse:
    """
    Handle a library query request.

    Searches for relevant code chunks and synthesizes an answer using LLM.
    """
    from xml_pipeline.librarian.query import query_library, format_sources_xml

    logger.info(f"Querying library {payload.library_id}: {payload.question[:100]}...")

    try:
        result = await query_library(
            library_id=payload.library_id,
            question=payload.question,
            max_chunks=payload.max_chunks,
            model=payload.model,
        )

        sources_xml = format_sources_xml(result.sources) if result.sources else ""

        return HandlerResponse.respond(
            payload=LibrarianAnswer(
                answer=result.answer,
                sources=sources_xml,
                tokens_used=result.tokens_used,
                chunks_examined=result.chunks_examined,
                error=result.error,
            )
        )

    except Exception as e:
        logger.error(f"Query failed: {e}")
        return HandlerResponse.respond(
            payload=LibrarianAnswer(
                answer="",
                sources="",
                tokens_used=0,
                chunks_examined=0,
                error=str(e),
            )
        )


async def handle_librarian_list(
    payload: LibrarianList,
    metadata: HandlerMetadata,
) -> HandlerResponse:
    """
    Handle a request to list all ingested libraries.
    """
    from xml_pipeline.librarian.index import list_libraries

    logger.info("Listing all libraries")

    try:
        libraries = await list_libraries()

        # Format libraries as XML
        lib_items = []
        for lib in libraries:
            lib_items.append(
                f"""  <library>
    <library-id>{xml_escape(lib.library_id)}</library-id>
    <name>{xml_escape(lib.name)}</name>
    <source-url>{xml_escape(lib.source_url)}</source-url>
    <created-at>{xml_escape(lib.created_at)}</created-at>
    <total-files>{lib.total_files}</total-files>
    <total-chunks>{lib.total_chunks}</total-chunks>
  </library>"""
            )

        libraries_xml = "<libraries>\n" + "\n".join(lib_items) + "\n</libraries>"

        return HandlerResponse.respond(
            payload=LibrarianLibraries(
                count=len(libraries),
                libraries=libraries_xml,
            )
        )

    except Exception as e:
        logger.error(f"List failed: {e}")
        return HandlerResponse.respond(
            payload=LibrarianLibraries(
                count=0,
                libraries="<libraries></libraries>",
            )
        )


async def handle_librarian_delete(
    payload: LibrarianDelete,
    metadata: HandlerMetadata,
) -> HandlerResponse:
    """
    Handle a request to delete a library.
    """
    from xml_pipeline.librarian.index import delete_library

    logger.info(f"Deleting library {payload.library_id}")

    try:
        success = await delete_library(payload.library_id)

        return HandlerResponse.respond(
            payload=LibrarianDeleted(
                library_id=payload.library_id,
                success=success,
                error="" if success else "Delete operation failed",
            )
        )

    except Exception as e:
        logger.error(f"Delete failed: {e}")
        return HandlerResponse.respond(
            payload=LibrarianDeleted(
                library_id=payload.library_id,
                success=False,
                error=str(e),
            )
        )


async def handle_librarian_get_chunk(
    payload: LibrarianGetChunk,
    metadata: HandlerMetadata,
) -> HandlerResponse:
    """
    Handle a request to retrieve a specific code chunk.
    """
    from xml_pipeline.librarian.query import get_chunk_by_id

    logger.info(f"Getting chunk {payload.chunk_id} from library {payload.library_id}")

    try:
        chunk = await get_chunk_by_id(payload.library_id, payload.chunk_id)

        if chunk is None:
            return HandlerResponse.respond(
                payload=LibrarianChunk(
                    chunk_id=payload.chunk_id,
                    error=f"Chunk not found: {payload.chunk_id}",
                )
            )

        return HandlerResponse.respond(
            payload=LibrarianChunk(
                chunk_id=chunk.chunk_id,
                file_path=chunk.file_path,
                name=chunk.name,
                chunk_type=chunk.chunk_type,
                language=chunk.language,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                docstring=chunk.docstring,
                signature=chunk.signature,
                error="",
            )
        )

    except Exception as e:
        logger.error(f"Get chunk failed: {e}")
        return HandlerResponse.respond(
            payload=LibrarianChunk(
                chunk_id=payload.chunk_id,
                error=str(e),
            )
        )
