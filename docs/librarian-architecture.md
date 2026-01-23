# Librarian Architecture — RLM-Powered Document Intelligence

**Status:** Design
**Date:** January 2026

## Overview

The Librarian is an agent that ingests, indexes, and queries large document collections using the **Recursive Language Model (RLM)** pattern. It can handle codebases, documentation, and structured data at scales far beyond LLM context windows (10M+ tokens).

Key insight from [MIT RLM research](https://arxiv.org/abs/...): Long contexts should be loaded as **variables in a REPL environment**, not fed directly to the neural network. The LLM writes code to examine, decompose, and recursively query chunks.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  RLM-Powered Librarian                                          │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Ingestion Pipeline                                         │  │
│  │                                                            │  │
│  │  Source → Detect Type → Select Chunker → Index → Store    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Query Engine (RLM Pattern)                                 │  │
│  │                                                            │  │
│  │  Query → Search → Filter → Recursive Sub-Query → Answer   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Storage Layer                                              │  │
│  │                                                            │  │
│  │  eXist-db (XML) + Vector Embeddings + Dependency Graph    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## The RLM Pattern

Traditional LLM usage stuffs entire documents into the prompt. This fails at scale:
- Context windows have hard limits (128K-1M tokens)
- Performance degrades with context length ("context rot")
- Cost scales linearly with input size

**RLM approach:**

1. **Load as Variable**: Documents become references, not inline content
2. **Programmatic Access**: LLM writes code to peek into chunks
3. **Recursive Sub-Queries**: `llm_query(chunk, question)` for focused analysis
4. **Aggregation**: Combine sub-query results into final answer

```python
# RLM-style pseudocode
async def handle_query(query: str, codebase: CodebaseRef):
    # 1. Search index for relevant chunks (not full content)
    hits = await search_index(codebase, query)

    # 2. Filter if too many results
    if len(hits) > 10:
        hits = await llm_filter(hits, query)  # LLM picks most relevant

    # 3. Recursive sub-queries on each chunk
    findings = []
    for hit in hits:
        chunk = await load_chunk(hit)
        result = await llm_query(
            f"Analyze this for: {query}\n\n{chunk}"
        )
        findings.append(result)

    # 4. Aggregate into final answer
    return await llm_synthesize(findings, query)
```

## Hybrid Chunking Architecture

Chunking is domain-specific. A C++ class should stay together; a legal clause shouldn't be split mid-sentence. We use a hybrid approach:

### Built-in Chunkers (Fast Path)

| Chunker | File Types | Strategy | Implementation |
|---------|------------|----------|----------------|
| **Code** | .c, .cpp, .py, .js, .rs, ... | AST-aware splitting | tree-sitter |
| **Markdown/Docs** | .md, .rst, .txt | Heading hierarchy | Custom parser |
| **Structured Data** | .json, .xml, .yaml | Schema-aware | lxml + json |
| **Plain Text** | emails, logs, notes | Semantic paragraphs | Sentence boundaries |

These cover ~90% of use cases with optimized, predictable behavior.

### WASM Factory (Fallback for Unknown Types)

For novel formats, the AI generates a custom chunker:

```
User uploads proprietary format
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 1: Sample Analysis                                    │
│                                                            │
│ AI examines sample files:                                 │
│ - Structure patterns                                      │
│ - Record boundaries                                       │
│ - Semantic units                                          │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 2: Generate Chunker (Rust → WASM)                    │
│                                                            │
│ AI writes Rust code implementing the chunker interface    │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 3: Compile & Validate                                │
│                                                            │
│ cargo build --target wasm32-wasi                          │
│ Test on sample files                                      │
│ AI reviews output quality                                 │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 4: Deploy                                            │
│                                                            │
│ Store in user's WASM modules                              │
│ Optional: publish to marketplace                          │
└───────────────────────────────────────────────────────────┘
```

### WASM Chunker Interface (WIT)

```wit
// chunker.wit
interface chunker {
    record chunk {
        id: string,
        content: string,
        metadata: list<tuple<string, string>>,
        parent-id: option<string>,
        children: list<string>,
    }

    record chunker-config {
        file-type: string,
        max-chunk-size: u32,
        preserve-context: bool,
        custom-params: list<tuple<string, string>>,
    }

    // Analyze sample data, return chunking config
    analyze: func(sample: string, file-type: string) -> chunker-config

    // Chunk a file using the config
    chunk-file: func(content: string, config: chunker-config) -> list<chunk>
}
```

## Ingestion Pipeline

### Step 1: Source Acquisition

```python
@dataclass
class IngestionSource:
    type: Literal["git", "upload", "url", "s3"]
    location: str
    filter: str | None = None  # e.g., "*.cpp", "docs/**/*.md"
```

Supported sources:
- **Git repository**: Clone and track branches
- **File upload**: Direct upload via UI
- **URL**: Fetch remote documents
- **S3/Cloud storage**: Enterprise integrations

### Step 2: Type Detection

```python
def detect_type(file_path: str, content: bytes) -> FileType:
    # 1. Check extension
    ext = Path(file_path).suffix.lower()
    if ext in CODE_EXTENSIONS:
        return FileType.CODE

    # 2. Check magic bytes
    if content.startswith(b'%PDF'):
        return FileType.PDF

    # 3. Content analysis
    if looks_like_markdown(content):
        return FileType.MARKDOWN

    return FileType.PLAIN_TEXT
```

### Step 3: Chunking

```python
def select_chunker(file_type: FileType, user_config: ChunkerConfig) -> Chunker:
    # User override
    if user_config.custom_wasm:
        return WasmChunker(user_config.custom_wasm)

    # Built-in chunkers
    match file_type:
        case FileType.CODE:
            return TreeSitterChunker(language=detect_language(file_type))
        case FileType.MARKDOWN:
            return MarkdownChunker()
        case FileType.JSON | FileType.XML | FileType.YAML:
            return StructuredDataChunker()
        case _:
            return PlainTextChunker()
```

### Step 4: Indexing

Each chunk is indexed in multiple ways:

| Index Type | Purpose | Implementation |
|------------|---------|----------------|
| **Full-text** | Keyword search | eXist-db Lucene |
| **Vector** | Semantic similarity | Embeddings (OpenAI/local) |
| **Graph** | Relationships | Class hierarchy, imports, references |
| **Metadata** | Filtering | File path, type, timestamp |

### Step 5: Storage

```xml
<!-- Chunk stored in eXist-db -->
<chunk xmlns="https://bloxserver.io/ns/librarian/v1">
  <id>opencascade:BRepBuilderAPI_MakeEdge:constructor_1</id>
  <source>
    <repo>opencascade</repo>
    <path>src/BRepBuilderAPI/BRepBuilderAPI_MakeEdge.cxx</path>
    <lines start="42" end="87"/>
  </source>
  <type>function</type>
  <metadata>
    <class>BRepBuilderAPI_MakeEdge</class>
    <visibility>public</visibility>
    <params>const TopoDS_Vertex&amp;, const TopoDS_Vertex&amp;</params>
  </metadata>
  <content><![CDATA[
BRepBuilderAPI_MakeEdge::BRepBuilderAPI_MakeEdge(
    const TopoDS_Vertex& V1,
    const TopoDS_Vertex& V2)
{
    // ... implementation
}
  ]]></content>
  <embedding>[0.023, -0.041, 0.089, ...]</embedding>
</chunk>
```

## Query Engine

### Query Flow

```
User: "How does BRepBuilderAPI_MakeEdge handle degenerate curves?"
                    │
                    ▼
┌───────────────────────────────────────────────────────────┐
│ Step 1: Search                                            │
│                                                           │
│ - Vector search: find semantically similar chunks         │
│ - Keyword search: "BRepBuilderAPI_MakeEdge" + "degenerate"│
│ - Graph traversal: class hierarchy, method calls          │
│                                                           │
│ Result: 47 potentially relevant chunks                    │
└───────────────────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────┐
│ Step 2: Filter (LLM-assisted)                             │
│                                                           │
│ Too many chunks for direct analysis.                      │
│ LLM reviews summaries, picks top 8 most relevant.         │
│                                                           │
│ Selected:                                                 │
│ - BRepBuilderAPI_MakeEdge constructors (3 chunks)        │
│ - Edge validation methods (2 chunks)                      │
│ - Degenerate curve handling (2 chunks)                    │
│ - Error reporting (1 chunk)                               │
└───────────────────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────┐
│ Step 3: Recursive Sub-Queries                             │
│                                                           │
│ For each chunk, focused LLM query:                        │
│                                                           │
│ llm_query(chunk_1, "How does this handle degenerate...")  │
│ llm_query(chunk_2, "What validation happens here...")     │
│ llm_query(chunk_3, "What errors are raised for...")       │
│ ...                                                       │
│                                                           │
│ 8 parallel sub-queries → 8 focused findings               │
└───────────────────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────┐
│ Step 4: Synthesize                                        │
│                                                           │
│ LLM combines findings into coherent answer:               │
│                                                           │
│ "BRepBuilderAPI_MakeEdge handles degenerate curves by:   │
│  1. Checking curve bounds in the constructor...           │
│  2. Calling BRepCheck_Edge for validation...              │
│  3. Setting myError to BRepBuilderAPI_CurveTooSmall..."   │
└───────────────────────────────────────────────────────────┘
```

### Handler Implementation

```python
@xmlify
@dataclass
class LibrarianQuery:
    """Query the librarian for information."""
    collection: str          # Which indexed collection
    question: str            # Natural language question
    max_chunks: int = 10     # Limit for recursive queries
    include_sources: bool = True

@xmlify
@dataclass
class LibrarianResponse:
    """Response from librarian with sources."""
    answer: str
    sources: list[SourceReference]
    confidence: float

async def handle_librarian_query(
    payload: LibrarianQuery,
    metadata: HandlerMetadata
) -> HandlerResponse:
    """RLM-style query handler."""

    # 1. Search for relevant chunks
    hits = await search_collection(
        payload.collection,
        payload.question,
        limit=50  # Cast wide net
    )

    # 2. Filter if needed
    if len(hits) > payload.max_chunks:
        hits = await llm_filter_chunks(
            hits,
            payload.question,
            limit=payload.max_chunks
        )

    # 3. Recursive sub-queries
    findings = await asyncio.gather(*[
        llm_analyze_chunk(chunk, payload.question)
        for chunk in hits
    ])

    # 4. Synthesize answer
    answer = await llm_synthesize(findings, payload.question)

    # 5. Build response
    sources = [
        SourceReference(
            path=hit.source_path,
            lines=(hit.start_line, hit.end_line),
            relevance=hit.score
        )
        for hit in hits
    ]

    return HandlerResponse.respond(
        payload=LibrarianResponse(
            answer=answer,
            sources=sources if payload.include_sources else [],
            confidence=calculate_confidence(findings)
        )
    )
```

## Storage Layer

### eXist-db (Primary Store)

XML-native database for chunk storage and XQuery retrieval.

**Why eXist-db:**
- Native XQuery for complex queries
- Full-text search with Lucene
- XML validation against schemas
- Transactional updates

**Collections structure:**
```
/db/librarian/
├── collections/
│   ├── {user_id}/
│   │   ├── {collection_id}/
│   │   │   ├── metadata.xml
│   │   │   ├── chunks/
│   │   │   │   ├── chunk_001.xml
│   │   │   │   ├── chunk_002.xml
│   │   │   │   └── ...
│   │   │   └── index/
│   │   │       └── embeddings.bin
```

### Vector Embeddings

For semantic search, chunks are embedded using:
- OpenAI `text-embedding-3-small` (cloud)
- Sentence Transformers (local/self-hosted)

Embeddings stored alongside chunks or in dedicated vector DB (Qdrant/Pinecone for scale).

### Dependency Graph

For code collections, track relationships:
- **Class hierarchy**: inheritance, interfaces
- **Imports**: file dependencies
- **Call graph**: function → function references

Stored in eXist-db as XML or external graph DB for complex traversals.

## Configuration

### organism.yaml

```yaml
listeners:
  - name: librarian
    handler: xml_pipeline.tools.librarian.handle_librarian_query
    payload_class: xml_pipeline.tools.librarian.LibrarianQuery
    description: Query indexed document collections
    agent: true
    peers: []  # Terminal handler
    config:
      exist_db:
        url: "http://localhost:8080/exist"
        user_env: EXIST_USER
        password_env: EXIST_PASSWORD
      embeddings:
        provider: openai  # or "local"
        model: text-embedding-3-small
      chunkers:
        code:
          max_chunk_size: 2000
          overlap: 200
        markdown:
          split_on_headings: true
          min_heading_level: 2
```

### Ingestion API

```python
# Ingest a git repository
await librarian.ingest(
    source=GitSource(
        url="https://github.com/Open-Cascade-SAS/OCCT",
        branch="master",
        filter="src/**/*.cxx"
    ),
    collection="opencascade",
    chunker_config=CodeChunkerConfig(
        language="cpp",
        max_chunk_size=2000
    )
)

# Query the collection
response = await librarian.query(
    collection="opencascade",
    question="How does BRepBuilderAPI_MakeEdge handle curves?"
)
```

## Scaling Considerations

| Scale | Storage | Search | Compute |
|-------|---------|--------|---------|
| Small (<10K chunks) | eXist-db local | In-DB Lucene | Single node |
| Medium (10K-1M) | eXist-db cluster | + Vector DB | Multi-worker |
| Large (1M+) | Sharded storage | Distributed search | GPU embeddings |

## Security

- **Collection isolation**: Users can only query their own collections
- **WASM sandbox**: Custom chunkers run in isolated WASM runtime
- **Rate limiting**: Prevent abuse of recursive queries
- **Audit logging**: Track all queries for compliance

## Future Enhancements

1. **Incremental updates**: Re-index only changed files
2. **Cross-collection queries**: Search across multiple codebases
3. **Collaborative collections**: Shared team libraries
4. **Query caching**: Cache common sub-queries
5. **Streaming ingestion**: Real-time updates from git webhooks

---

## References

- [Recursive Language Models (MIT)](docs/mit-paper.pdf) — Foundational research on RLM pattern
- [tree-sitter](https://tree-sitter.github.io/) — AST-aware code parsing
- [eXist-db](http://exist-db.org/) — XML-native database
- [BloxServer Architecture](bloxserver-architecture.md) — Platform overview
