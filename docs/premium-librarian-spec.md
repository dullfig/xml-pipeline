# Premium Librarian — RLM-Powered Codebase Intelligence

**Status:** Design Spec  
**Author:** Dan & Donna  
**Date:** 2026-01-26

## Overview

The Premium Librarian is an RLM-powered (Recursive Language Model) tool that can ingest entire codebases or document corpora — millions of tokens — and answer natural language queries about them.

Unlike traditional code search (grep, ripgrep, Sourcegraph), the Premium Librarian *understands* structure, relationships, and intent. It can answer questions like:

- "Where are edges calculated in OpenCASCADE?"
- "How does the authentication flow work?"
- "What would break if I changed this interface?"

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PREMIUM LIBRARIAN                            │
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│  │   Ingest    │───▶│   Chunker   │───▶│      eXist-db           │ │
│  │   (upload)  │    │  (4 types   │    │  (versioned storage)    │ │
│  │             │    │   + WASM)   │    │                         │ │
│  └─────────────┘    └─────────────┘    └───────────┬─────────────┘ │
│                                                     │               │
│                                                     ▼               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│  │   Query     │───▶│    RLM      │───▶│     Index / Map         │ │
│  │  (natural   │    │  Processor  │    │  (structure, relations) │ │
│  │   language) │    │             │    │                         │ │
│  └─────────────┘    └──────┬──────┘    └─────────────────────────┘ │
│                            │                                        │
│                            ▼                                        │
│                    ┌─────────────┐                                  │
│                    │  Response   │                                  │
│                    │  (answer +  │                                  │
│                    │   sources)  │                                  │
│                    └─────────────┘                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Chunkers

The RLM algorithm needs content-aware chunking. Different content types have different natural boundaries.

### Built-in Chunkers

| Chunker | Content Types | Chunking Strategy |
|---------|---------------|-------------------|
| **Code** | .py, .js, .ts, .cpp, .c, .java, .go, .rs, etc. | Functions, classes, modules. Preserves imports, docstrings, signatures. |
| **Prose** | .md, .txt, .rst, .adoc | Paragraphs, sections, chapters. Preserves headings, structure. |
| **Structured** | .yaml, .json, .xml, .toml, .ini | Schema-aware. Preserves hierarchy, keys, nesting. |
| **Tabular** | .csv, .tsv, .parquet | Row groups with headers. Preserves column semantics. |

### Content Type Detection

1. File extension mapping (fast path)
2. MIME type detection
3. Content sniffing (magic bytes, heuristics)
4. User override via config

### Custom WASM Chunkers

For specialized formats, users can provide their own chunker:

```typescript
// chunker.ts (AssemblyScript)
import { Chunk, ChunkMetadata } from "@openblox/chunker-sdk";

export function chunk(content: string, metadata: ChunkMetadata): Chunk[] {
  // Custom logic for proprietary format
  const chunks: Chunk[] = [];
  
  // Parse your format, emit chunks
  // Each chunk has: content, startLine, endLine, type, name
  
  return chunks;
}
```

**Compile & upload:**
```bash
asc chunker.ts -o chunker.wasm
# Upload via API or CLI
```

**Security:**
- WASM runs sandboxed (can't escape, can't access filesystem)
- CPU/memory limits enforced
- Chunker is pure function: string in, chunks out

## Ingest Pipeline

### 1. Upload

```
POST /api/v1/libraries
{
  "name": "opencascade",
  "source": {
    "type": "git",
    "url": "https://github.com/Open-Cascade-SAS/OCCT.git",
    "branch": "master"
  },
  // OR
  "source": {
    "type": "upload",
    "archive": "<base64 tarball>"
  }
}
```

### 2. Chunking

For each file:
1. Detect content type
2. Select chunker (built-in or custom WASM)
3. Chunk content
4. Store chunks in eXist-db with metadata

**Chunk metadata:**
```json
{
  "id": "chunk-uuid",
  "library_id": "lib-uuid",
  "file_path": "src/BRepBuilderAPI/BRepBuilderAPI_MakeEdge.cxx",
  "start_line": 142,
  "end_line": 387,
  "type": "function",
  "name": "BRepBuilderAPI_MakeEdge::Build",
  "language": "cpp",
  "imports": ["gp_Pnt", "TopoDS_Edge", "BRepLib"],
  "calls": ["GCPnts_TangentialDeflection", "BRep_Builder"],
  "version": "v7.8.0",
  "indexed_at": "2026-01-26T..."
}
```

### 3. Indexing (Background)

After chunking, RLM builds structural index:

- **Call graph**: What calls what
- **Type hierarchy**: Classes, inheritance
- **Module map**: How code is organized
- **Symbol table**: Functions, classes, constants
- **Dependency graph**: Imports, includes

This runs as a background job (can take hours for large codebases).

## Query Pipeline

### 1. Query

```
POST /api/v1/libraries/{id}/query
{
  "question": "Where are edges calculated?",
  "max_tokens": 8000,
  "include_sources": true
}
```

### 2. RLM Processing

The RLM receives:
```
You have access to a library "opencascade" with 2.3M lines of C++ code.

Structural index available:
- 4,231 classes
- 47,892 functions  
- 12 main modules: BRepBuilderAPI, BRepAlgoAPI, ...

User question: "Where are edges calculated?"

Available tools:
- search(query) → relevant chunks
- get_chunk(id) → full chunk content
- get_structure(path) → module/class structure
- recursive_query(sub_question) → ask yourself about a subset
```

RLM then:
1. Searches for "edge" in symbol table
2. Finds `BRepBuilderAPI_MakeEdge`, `BRepAlgo_EdgeConnector`, etc.
3. Recursively queries: "What does BRepBuilderAPI_MakeEdge do?"
4. Retrieves relevant chunks, synthesizes answer

### 3. Response

```json
{
  "answer": "Edge calculation in OpenCASCADE primarily happens in the BRepBuilderAPI module...",
  
  "sources": [
    {
      "file": "src/BRepBuilderAPI/BRepBuilderAPI_MakeEdge.cxx",
      "lines": "142-387",
      "relevance": 0.95,
      "snippet": "void BRepBuilderAPI_MakeEdge::Build() { ... }"
    },
    {
      "file": "src/BRepAlgo/BRepAlgo_EdgeConnector.cxx", 
      "lines": "89-201",
      "relevance": 0.82,
      "snippet": "..."
    }
  ],
  
  "tokens_used": 47832,
  "chunks_examined": 127,
  "cost": "$0.34"
}
```

## Storage (eXist-db)

### Why eXist-db?

- **Versioning**: Track codebase changes over time
- **XML-native**: Fits with xml-pipeline philosophy
- **XQuery**: Powerful querying for structured data
- **Efficient**: Handles millions of documents

### Schema

```xml
<library id="opencascade" version="v7.8.0">
  <metadata>
    <name>OpenCASCADE</name>
    <source>git:https://github.com/Open-Cascade-SAS/OCCT.git</source>
    <indexed_at>2026-01-26T08:00:00Z</indexed_at>
    <stats>
      <files>12847</files>
      <lines>2341892</lines>
      <chunks>89234</chunks>
    </stats>
  </metadata>
  
  <structure>
    <module name="BRepBuilderAPI">
      <class name="BRepBuilderAPI_MakeEdge">
        <function name="Build" file="..." lines="142-387"/>
        ...
      </class>
    </module>
  </structure>
  
  <chunks>
    <chunk id="..." file="..." type="function" name="Build">
      <content>...</content>
      <relations>
        <calls>GCPnts_TangentialDeflection</calls>
        <imports>gp_Pnt</imports>
      </relations>
    </chunk>
  </chunks>
</library>
```

## Pricing (Premium Tier)

| Operation | Cost |
|-----------|------|
| Ingest (per 1M tokens) | $2.00 |
| Index (per library) | $5.00 - $50.00 (depends on size) |
| Query (per query) | $0.10 - $2.00 (depends on complexity) |
| Storage (per GB/month) | $0.50 |

**Why premium:**
- Compute-intensive (lots of LLM calls)
- Storage-intensive (versioned codebases)
- High value (saves weeks of manual exploration)

## Use Cases

### 1. Legacy Code Understanding
"I inherited a 500K line Fortran codebase. Help me understand the data flow."

### 2. API Discovery
"How do I create a NURBS surface with specific knot vectors?"

### 3. Impact Analysis
"What would break if I deprecated this function?"

### 4. Onboarding
"Explain the architecture of this codebase to a new developer."

### 5. Code Review Assistance
"Does this change follow the patterns used elsewhere in the codebase?"

## Implementation Phases

### Phase 1: MVP
- [ ] Basic ingest (git clone, tarball upload)
- [ ] Code chunker (Python, JavaScript, C++)
- [ ] eXist-db storage
- [ ] Simple RLM query (search + retrieve)

### Phase 2: Full Chunkers
- [ ] Prose chunker
- [ ] Structured chunker (YAML/JSON/XML)
- [ ] Tabular chunker
- [ ] WASM chunker SDK

### Phase 3: Deep Indexing
- [ ] Call graph extraction
- [ ] Type hierarchy
- [ ] Cross-reference index
- [ ] Incremental re-indexing on changes

### Phase 4: Advanced Queries
- [ ] Multi-turn conversations about code
- [ ] "What if" analysis
- [ ] Code generation informed by codebase patterns

---

*"Finally understand the codebase you inherited."*
