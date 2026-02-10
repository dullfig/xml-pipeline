"""
query.py — RAG-based query system for Premium Librarian.

Searches indexed codebases and synthesizes answers using Online LLM.
The flow: Search → Retrieve → Synthesize → Return with sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger(__name__)


def _escape_xquery_string(s: str) -> str:
    """Escape a string for safe embedding in XQuery string literals.

    XQuery uses doubled quotes for escaping inside string literals,
    not backslash escaping.  ``"`` → ``""`` and ``'`` → ``''``.
    """
    return s.replace('"', '""').replace("'", "''")


@dataclass
class Source:
    """A source chunk used in answering a query."""

    file_path: str
    name: str
    chunk_type: str
    start_line: int
    end_line: int
    relevance_score: float
    snippet: str = ""  # First ~200 chars of content


@dataclass
class QueryResult:
    """Result of a library query."""

    answer: str
    sources: list[Source] = field(default_factory=list)
    tokens_used: int = 0
    chunks_examined: int = 0
    error: str = ""


@dataclass
class RetrievedChunk:
    """A chunk retrieved from eXist-db for RAG."""

    chunk_id: str
    file_path: str
    name: str
    chunk_type: str
    language: str
    start_line: int
    end_line: int
    content: str
    docstring: str
    signature: str
    score: float


async def _search_chunks(
    library_id: str,
    query: str,
    max_results: int = 20,
) -> list[RetrievedChunk]:
    """
    Search for relevant chunks using Lucene full-text search.

    Returns chunks sorted by relevance score.
    """
    from xml_pipeline.tools.librarian import librarian_query

    # Escape query for XQuery
    query_escaped = _escape_xquery_string(query)

    # Full-text search using Lucene
    xquery = f"""
    declare namespace l = "https://xml-pipeline.org/ns/librarian/v1";
    import module namespace ft = "http://exist-db.org/xquery/lucene";

    for $chunk in collection("/db/librarian/{library_id}/chunks")//l:chunk
    let $content := $chunk/l:content/text()
    let $name := $chunk/l:name/text()
    let $docstring := $chunk/l:docstring/text()
    let $score := (
        if (ft:query($content, "{query_escaped}")) then ft:score($content) * 2
        else if (ft:query($name, "{query_escaped}")) then ft:score($name) * 3
        else if (ft:query($docstring, "{query_escaped}")) then ft:score($docstring)
        else 0
    )
    where $score > 0
    order by $score descending
    return <result score="{{$score}}">
      <id>{{$chunk/l:id/text()}}</id>
      <file-path>{{$chunk/l:file-path/text()}}</file-path>
      <name>{{$chunk/l:name/text()}}</name>
      <chunk-type>{{$chunk/l:chunk-type/text()}}</chunk-type>
      <language>{{$chunk/l:language/text()}}</language>
      <start-line>{{$chunk/l:start-line/text()}}</start-line>
      <end-line>{{$chunk/l:end-line/text()}}</end-line>
      <signature>{{$chunk/l:signature/text()}}</signature>
      <docstring>{{$chunk/l:docstring/text()}}</docstring>
      <content>{{$chunk/l:content/text()}}</content>
    </result>
    """

    result = await librarian_query(
        query=xquery,
        collection=f"/db/librarian/{library_id}",
    )

    chunks: list[RetrievedChunk] = []

    if not result.success:
        logger.warning(f"Search failed: {result.error}")
        # Fall back to simple query without Lucene
        return await _search_chunks_fallback(library_id, query, max_results)

    try:
        from lxml import etree

        xml_str = f"<results>{result.data.get('results', '')}</results>"
        root = etree.fromstring(xml_str.encode())

        for item in root.findall("result")[:max_results]:
            score = float(item.get("score", 0))

            chunks.append(
                RetrievedChunk(
                    chunk_id=item.findtext("id", ""),
                    file_path=item.findtext("file-path", ""),
                    name=item.findtext("name", ""),
                    chunk_type=item.findtext("chunk-type", ""),
                    language=item.findtext("language", ""),
                    start_line=int(item.findtext("start-line", "0")),
                    end_line=int(item.findtext("end-line", "0")),
                    content=item.findtext("content", ""),
                    docstring=item.findtext("docstring", ""),
                    signature=item.findtext("signature", ""),
                    score=score,
                )
            )

    except Exception as e:
        logger.warning(f"Failed to parse search results: {e}")

    return chunks


async def _search_chunks_fallback(
    library_id: str,
    query: str,
    max_results: int = 20,
) -> list[RetrievedChunk]:
    """
    Fallback search using contains() when Lucene is not available.

    Less accurate but works without Lucene indexing.
    """
    from xml_pipeline.tools.librarian import librarian_query

    # Simple contains search
    query_lower = _escape_xquery_string(query.lower())
    terms = query_lower.split()

    # Build contains conditions
    conditions = []
    for term in terms[:5]:  # Limit to 5 terms
        conditions.append(
            f'(contains(lower-case($chunk/l:content), "{term}") or '
            f'contains(lower-case($chunk/l:name), "{term}") or '
            f'contains(lower-case($chunk/l:docstring), "{term}"))'
        )

    where_clause = " or ".join(conditions) if conditions else "true()"

    xquery = f"""
    declare namespace l = "https://xml-pipeline.org/ns/librarian/v1";

    for $chunk in collection("/db/librarian/{library_id}/chunks")//l:chunk
    where {where_clause}
    return <result>
      <id>{{$chunk/l:id/text()}}</id>
      <file-path>{{$chunk/l:file-path/text()}}</file-path>
      <name>{{$chunk/l:name/text()}}</name>
      <chunk-type>{{$chunk/l:chunk-type/text()}}</chunk-type>
      <language>{{$chunk/l:language/text()}}</language>
      <start-line>{{$chunk/l:start-line/text()}}</start-line>
      <end-line>{{$chunk/l:end-line/text()}}</end-line>
      <signature>{{$chunk/l:signature/text()}}</signature>
      <docstring>{{$chunk/l:docstring/text()}}</docstring>
      <content>{{$chunk/l:content/text()}}</content>
    </result>
    """

    result = await librarian_query(
        query=xquery,
        collection=f"/db/librarian/{library_id}",
    )

    chunks: list[RetrievedChunk] = []

    if not result.success:
        logger.warning(f"Fallback search failed: {result.error}")
        return chunks

    try:
        from lxml import etree

        xml_str = f"<results>{result.data.get('results', '')}</results>"
        root = etree.fromstring(xml_str.encode())

        for i, item in enumerate(root.findall("result")[:max_results]):
            # Assign decreasing score based on order
            score = 1.0 - (i * 0.05)

            chunks.append(
                RetrievedChunk(
                    chunk_id=item.findtext("id", ""),
                    file_path=item.findtext("file-path", ""),
                    name=item.findtext("name", ""),
                    chunk_type=item.findtext("chunk-type", ""),
                    language=item.findtext("language", ""),
                    start_line=int(item.findtext("start-line", "0")),
                    end_line=int(item.findtext("end-line", "0")),
                    content=item.findtext("content", ""),
                    docstring=item.findtext("docstring", ""),
                    signature=item.findtext("signature", ""),
                    score=score,
                )
            )

    except Exception as e:
        logger.warning(f"Failed to parse fallback search results: {e}")

    return chunks


def _build_rag_prompt(
    question: str,
    chunks: list[RetrievedChunk],
    library_name: str,
) -> str:
    """Build the RAG prompt with retrieved context."""
    context_parts = []

    for i, chunk in enumerate(chunks, 1):
        header = f"[{i}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
        if chunk.signature:
            header += f"\n    {chunk.signature}"

        # Truncate content if too long
        content = chunk.content
        if len(content) > 2000:
            content = content[:2000] + "\n... (truncated)"

        context_parts.append(f"{header}\n```{chunk.language}\n{content}\n```")

    context = "\n\n".join(context_parts)

    return f"""You are a code assistant analyzing the "{library_name}" codebase.

Answer the following question based ONLY on the provided code context.
If the answer is not in the context, say so clearly.
Reference specific files and line numbers when relevant.

## Code Context

{context}

## Question

{question}

## Instructions

1. Answer based on the code context above
2. Cite sources using [1], [2], etc. format
3. Include relevant code snippets if helpful
4. Be concise but complete"""


async def query_library(
    library_id: str,
    question: str,
    max_chunks: int = 20,
    model: str = "",
) -> QueryResult:
    """
    Query an ingested library using RAG.

    Args:
        library_id: ID of the ingested library
        question: Natural language question
        max_chunks: Maximum chunks to retrieve for context
        model: LLM model to use (empty = use default)

    Returns:
        QueryResult with answer and sources
    """
    from xml_pipeline.librarian.index import get_index
    from xml_pipeline.llm import complete

    # Get library info
    index = await get_index(library_id)
    if not index:
        return QueryResult(
            answer="",
            error=f"Library not found: {library_id}",
        )

    # Search for relevant chunks
    chunks = await _search_chunks(library_id, question, max_chunks)

    if not chunks:
        return QueryResult(
            answer=f"No relevant code found for your question in the '{index.name}' codebase.",
            chunks_examined=0,
        )

    # Build RAG prompt
    prompt = _build_rag_prompt(question, chunks, index.name)

    # Call LLM
    try:
        response = await complete(
            model=model or "grok-4.1",  # Default model
            messages=[
                {"role": "user", "content": prompt},
            ],
        )

        answer = response.content
        tokens_used = response.usage.get("total_tokens", 0)

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return QueryResult(
            answer="",
            error=f"Failed to generate answer: {e}",
            chunks_examined=len(chunks),
        )

    # Build sources list
    sources = [
        Source(
            file_path=chunk.file_path,
            name=chunk.name,
            chunk_type=chunk.chunk_type,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            relevance_score=chunk.score,
            snippet=chunk.content[:200] if chunk.content else "",
        )
        for chunk in chunks
    ]

    return QueryResult(
        answer=answer,
        sources=sources,
        tokens_used=tokens_used,
        chunks_examined=len(chunks),
    )


def format_sources_xml(sources: list[Source]) -> str:
    """Format sources as XML for LibrarianAnswer payload."""
    source_items = []

    for i, source in enumerate(sources, 1):
        snippet_escaped = xml_escape(source.snippet[:100]) if source.snippet else ""
        source_items.append(
            f"""  <source index="{i}">
    <file-path>{xml_escape(source.file_path)}</file-path>
    <name>{xml_escape(source.name)}</name>
    <type>{xml_escape(source.chunk_type)}</type>
    <lines>{source.start_line}-{source.end_line}</lines>
    <score>{source.relevance_score:.2f}</score>
    <snippet>{snippet_escaped}</snippet>
  </source>"""
        )

    return "<sources>\n" + "\n".join(source_items) + "\n</sources>"


async def get_chunk_by_id(library_id: str, chunk_id: str) -> Optional[RetrievedChunk]:
    """
    Retrieve a specific chunk by ID.

    Useful for follow-up queries about a specific piece of code.
    """
    from xml_pipeline.tools.librarian import librarian_query

    chunk_id_escaped = _escape_xquery_string(chunk_id)

    xquery = f"""
    declare namespace l = "https://xml-pipeline.org/ns/librarian/v1";

    for $chunk in collection("/db/librarian/{library_id}/chunks")//l:chunk
    where $chunk/l:id = "{chunk_id_escaped}"
    return $chunk
    """

    result = await librarian_query(
        query=xquery,
        collection=f"/db/librarian/{library_id}",
    )

    if not result.success:
        return None

    try:
        from lxml import etree

        ns = {"l": "https://xml-pipeline.org/ns/librarian/v1"}
        root = etree.fromstring(result.data.get("results", "").encode())

        chunk_elem = root if root.tag.endswith("chunk") else root.find("l:chunk", namespaces=ns)
        if chunk_elem is None:
            return None

        return RetrievedChunk(
            chunk_id=chunk_elem.findtext("l:id", "", namespaces=ns),
            file_path=chunk_elem.findtext("l:file-path", "", namespaces=ns),
            name=chunk_elem.findtext("l:name", "", namespaces=ns),
            chunk_type=chunk_elem.findtext("l:chunk-type", "", namespaces=ns),
            language=chunk_elem.findtext("l:language", "", namespaces=ns),
            start_line=int(chunk_elem.findtext("l:start-line", "0", namespaces=ns)),
            end_line=int(chunk_elem.findtext("l:end-line", "0", namespaces=ns)),
            content=chunk_elem.findtext("l:content", "", namespaces=ns),
            docstring=chunk_elem.findtext("l:docstring", "", namespaces=ns),
            signature=chunk_elem.findtext("l:signature", "", namespaces=ns),
            score=1.0,
        )

    except Exception as e:
        logger.warning(f"Failed to parse chunk: {e}")
        return None
