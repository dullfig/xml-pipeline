# AgentServer (xml-pipeline)

A tamper-proof nervous system for multi-agent AI systems using XML as the sovereign wire format. AgentServer provides a schema-driven, Turing-complete message bus where agents communicate through validated XML payloads, with automatic XSD generation, handler isolation, and built-in security guarantees against agent misbehavior.

**Version:** 0.4.0

## Tech Stack

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| Runtime | Python | 3.11+ | Async-first, type-hinted codebase |
| Streaming | aiostream | 0.5+ | Stream-based message pipeline with fan-out |
| XML Processing | lxml | Latest | XSD validation, C14N normalization, repair |
| Serialization | xmlable | vendored | Dataclass ↔ XML round-trip with auto-XSD |
| Config | PyYAML | Latest | Organism configuration (organism.yaml) |
| Crypto | cryptography | Latest | Ed25519 identity keys for signing |
| HTTP | httpx | 0.27+ | LLM backend communication |
| Math evaluation | simpleeval | 0.9+ | Safe expression evaluation (calculator tool) |
| Case conversion | pyhumps | Latest | Snake/camel case conversion |

> **Note:** TUI console and authentication are available in [OpenBlox](https://openblox.ai).

## Quick Start

```bash
# Prerequisites
# - Python 3.11 or higher
# - pip (or uv/pipx for faster installs)

# Clone and setup
git clone <repo-url>
cd xml-pipeline
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

# Install with all features
pip install -e ".[all]"

# Or minimal install + specific features
pip install -e "."                    # Core only
pip install -e ".[anthropic]"         # + Anthropic SDK

# Configure environment
cp .env.example .env
# Edit .env to add your API keys (XAI_API_KEY, ANTHROPIC_API_KEY, etc.)

# Run the organism
xml-pipeline run config/organism.yaml
xp run config/organism.yaml  # Short alias

# Try the console example
pip install -e ".[console]"
python -m examples.console

# Run tests
pip install -e ".[test]"
pytest tests/ -v
```

## Project Structure

```
xml-pipeline/
├── xml_pipeline/              # Main package
│   ├── config/               # Config loading and templates
│   ├── listeners/            # Listener implementations and examples
│   ├── llm/                  # LLM router, backends, token bucket
│   ├── memory/               # Context buffer for conversation history
│   ├── message_bus/          # Core message pump and pipeline
│   │   ├── steps/            # Pipeline steps (repair, c14n, validation, etc.)
│   │   ├── stream_pump.py    # Main aiostream-based pump
│   │   ├── message_state.py  # Message state dataclass
│   │   ├── thread_registry.py # Opaque UUID ↔ call chain mapping
│   │   └── system_pipeline.py # External message injection
│   ├── platform/             # Platform-level APIs (prompt registry, LLM API)
│   ├── primitives/           # System message types (Boot, TodoUntil, etc.)
│   ├── prompts/              # System prompts (no_paperclippers, etc.)
│   ├── schema/               # XSD schema files
│   ├── tools/                # Native tools (files, shell, search, etc.)
│   ├── workers/              # Background worker process registry
│   ├── librarian/            # Premium Librarian (codebase intelligence)
│   └── utils/                # Shared utilities
├── config/                   # Example organism configurations
├── docs/                     # Architecture and design docs
├── examples/                 # Example console and integrations
├── handlers/                 # Example message handlers
├── tests/                    # pytest test suite
├── third_party/              # Vendored dependencies
│   └── xmlable/              # XML serialization library
└── pyproject.toml            # Project metadata and dependencies
```

> **Note:** Authentication (`auth/`) and TUI console (`console/`) are available in [OpenBlox](https://openblox.ai).

## Architecture Overview

AgentServer implements a stream-based message pump where all communication flows through validated XML envelopes. The architecture enforces strict isolation between handlers (untrusted code) and the system (trusted zone).

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TRUSTED ZONE (System)                        │
│  • Thread registry (UUID ↔ call chain mapping)                      │
│  • Listener registry (name → peers, schema)                         │
│  • Envelope injection (<from>, <thread>, <to>)                      │
│  • Peer constraint enforcement                                      │
└─────────────────────────────────────────────────────────────────────┘
                               ↕
                    Coroutine Capture Boundary
                               ↕
┌─────────────────────────────────────────────────────────────────────┐
│                      UNTRUSTED ZONE (Handlers)                      │
│  • Receive typed payload + metadata                                 │
│  • Return HandlerResponse or None                                   │
│  • Cannot forge identity, escape thread, or probe topology          │
└─────────────────────────────────────────────────────────────────────┘
```

**Message Flow:**
1. Raw bytes → Repair → C14N → Envelope validation → Payload extraction
2. Thread assignment → XSD validation → Deserialization → Routing
3. Handler dispatch → Response wrapping → Re-injection

### Key Modules

| Module | Location | Purpose |
|--------|----------|---------|
| StreamPump | `xml_pipeline/message_bus/stream_pump.py` | Main message pump with aiostream pipeline |
| MessageState | `xml_pipeline/message_bus/message_state.py` | State object flowing through pipeline steps |
| ThreadRegistry | `xml_pipeline/message_bus/thread_registry.py` | Maps opaque UUIDs to call chains |
| SystemPipeline | `xml_pipeline/message_bus/system_pipeline.py` | External message injection (console, webhooks) |
| LLMRouter | `xml_pipeline/llm/router.py` | Multi-backend LLM routing with failover |
| ThreadBudgetRegistry | `xml_pipeline/message_bus/budget_registry.py` | Per-thread token limits and enforcement |
| UsageTracker | `xml_pipeline/llm/usage_tracker.py` | Production billing and gas usage metering |
| PromptRegistry | `xml_pipeline/platform/prompt_registry.py` | Immutable system prompt storage |
| ContextBuffer | `xml_pipeline/memory/context_buffer.py` | Conversation history per thread |
| WorkerRegistry | `xml_pipeline/workers/registry.py` | Background worker process management |
| PremiumLibrarian | `xml_pipeline/librarian/` | Codebase intelligence with RAG |

## Development Guidelines

### File Naming
- Python files: `snake_case.py` (e.g., `stream_pump.py`, `message_state.py`)
- Config files: `snake_case.yaml` or `kebab-case.yaml`
- Test files: `test_*.py` in `tests/` directory

### Code Naming
- Classes: `PascalCase` (e.g., `StreamPump`, `MessageState`, `HandlerResponse`)
- Functions/methods: `snake_case` (e.g., `repair_step`, `handle_greeting`)
- Variables: `snake_case` (e.g., `thread_id`, `payload_class`)
- Constants: `SCREAMING_SNAKE_CASE` (e.g., `MAX_FILE_SIZE`, `ROUTING_ERROR`)
- Private members: `_leading_underscore` (e.g., `_running`, `_registry`)
- Async functions: regular `snake_case`, no special prefix

### Payload Classes (xmlify pattern)
```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    """Incoming greeting request."""
    name: str
```

### Handler Pattern
```python
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    """Handler receives typed payload + metadata, returns HandlerResponse."""
    return HandlerResponse(
        payload=GreetingResponse(message="Hello!"),
        to="next-listener",
    )
```

### Import Order
1. `from __future__ import annotations` (if needed)
2. Standard library imports
3. Third-party imports (lxml, aiostream, etc.)
4. Local imports from `xml_pipeline.*`
5. Local imports from `third_party.*`

### Type Hints
- Always use type hints for function parameters and return types
- Use `from __future__ import annotations` for forward references
- MyPy is configured with `disallow_untyped_defs = true`

## Available Commands

| Command | Description |
|---------|-------------|
| `xml-pipeline run [config]` | Run organism from config file |
| `xml-pipeline init [name]` | Create new organism config template |
| `xml-pipeline check [config]` | Validate config without running |
| `xml-pipeline version` | Show version and installed features |
| `xp run [config]` | Short alias for xml-pipeline run |
| `python -m examples.console` | Run interactive console example |
| `pytest tests/ -v` | Run test suite |
| `pytest tests/test_pipeline_steps.py -v` | Run specific test file |

## Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `XAI_API_KEY` | For xAI | xAI (Grok) API key | `xai-...` |
| `ANTHROPIC_API_KEY` | For Anthropic | Anthropic (Claude) API key | `sk-ant-...` |
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key | `sk-...` |

## Testing

- **Location:** `tests/` directory
- **Framework:** pytest with pytest-asyncio
- **Pattern:** `test_*.py` files, classes prefixed with `Test`, methods with `test_`
- **Async tests:** Use `@pytest.mark.asyncio` decorator
- **Markers:** `@pytest.mark.slow`, `@pytest.mark.integration`
- **Coverage:** No explicit target, focus on pipeline step coverage

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_pipeline_steps.py -v

# Run tests matching pattern
pytest tests/ -v -k "repair"

# Skip slow tests
pytest tests/ -v -m "not slow"
```

## Organism Configuration

Organisms are configured via YAML files (default: `config/organism.yaml`).

See @docs/configuration.md for full reference.

```yaml
organism:
  name: my-organism
  port: 8765
  max_tokens_per_thread: 100000  # Token budget per thread

llm:
  strategy: failover
  backends:
    - provider: xai
      api_key_env: XAI_API_KEY

listeners:
  - name: greeter
    payload_class: handlers.hello.Greeting
    handler: handlers.hello.handle_greeting
    description: Greeting agent
    agent: true
    peers: [shouter]
    prompt: |
      You are a friendly greeter agent.
```

## Security Model

- **Handler Isolation:** Handlers cannot forge identity, escape threads, or probe topology
- **Peer Constraints:** Agents can only send to declared peers in config
- **Peer Tables:** Named, mutable peer mappings for thread-scoped privilege enforcement (e.g., premium/basic tiers). Table name embedded in thread chain prefix behind opaque UUID. Dispatch re-reads table on every message — mid-conversation revocation supported
- **Opaque Thread UUIDs:** Handlers see only UUIDs, never internal call chains
- **Envelope Injection:** `<from>`, `<thread>`, `<to>` always set by system, never by handlers
- **OOB Channel:** Privileged commands use separate localhost-only channel

## Token Budget & Usage Tracking

The platform provides three layers of token tracking:

| Layer | Module | Purpose |
|-------|--------|---------|
| Per-agent | `LLMRouter._agent_usage` | Internal token tracking per agent |
| Per-thread | `ThreadBudgetRegistry` | Enforcement limits (blocks LLM calls) |
| Platform | `UsageTracker` | Production billing and gas metering |

### Thread Budget Enforcement

Each thread has a token budget (default: 100,000 tokens). LLM calls are blocked when exhausted:

```python
from xml_pipeline.message_bus import get_budget_registry, BudgetExhaustedError

registry = get_budget_registry()

# Check before LLM call (automatic in router)
try:
    registry.check_budget(thread_id, estimated_tokens=1000)
except BudgetExhaustedError as e:
    print(f"Thread {e.thread_id} exhausted: {e.used}/{e.max_tokens}")
```

Configure via `organism.yaml`:

```yaml
organism:
  name: my-organism
  max_tokens_per_thread: 100000  # Default
```

### Usage Tracking (Billing)

Subscribe to usage events for production billing:

```python
from xml_pipeline.llm import get_usage_tracker

tracker = get_usage_tracker()

# Subscribe to events (for billing webhook, database, etc.)
def record_usage(event):
    billing_db.record(
        org_id=event.metadata.get("org_id"),
        tokens=event.total_tokens,
        cost=event.estimated_cost,  # USD estimate
    )

tracker.subscribe(record_usage)

# Query totals
totals = tracker.get_totals()
print(f"Total tokens: {totals['total_tokens']}")
print(f"Total cost: ${totals['total_cost']}")
```

## Message Envelope Format

All messages use the universal envelope with namespace `https://xml-pipeline.org/ns/envelope/v1`:

```xml
<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>greeter</from>
    <to>shouter</to>
    <thread>550e8400-e29b-41d4-a716-446655440000</thread>
  </meta>
  <Greeting xmlns="">
    <name>Alice</name>
  </Greeting>
</message>
```

## Pipeline Steps

Messages flow through these processing stages:

1. **repair_step** — Fix malformed XML using lxml recover mode
2. **c14n_step** — Canonicalize XML (Exclusive C14N)
3. **envelope_validation_step** — Verify `<message>` structure against envelope.xsd
4. **payload_extraction_step** — Extract payload element from envelope
5. **thread_assignment_step** — Assign or inherit thread UUID
6. **xsd_validation_step** — Validate payload against listener's schema
7. **deserialization** — XML → typed @xmlify dataclass

## Optional Dependencies

```bash
# LLM providers
pip install xml-pipeline[anthropic]   # Anthropic SDK
pip install xml-pipeline[openai]      # OpenAI SDK

# Tool backends
pip install xml-pipeline[redis]       # Distributed key-value store
pip install xml-pipeline[search]      # DuckDuckGo search
pip install xml-pipeline[librarian]   # Codebase intelligence (GitPython)

# Console example
pip install xml-pipeline[console]     # prompt_toolkit for examples

# Everything
pip install xml-pipeline[all]

# Development (includes all + mypy + ruff)
pip install xml-pipeline[dev]
```

> **Note:** Authentication features are available in [OpenBlox](https://openblox.ai).

## Native Tools

The project includes built-in tool implementations in `xml_pipeline/tools/`:

| Tool | File | Purpose |
|------|------|---------|
| calculate | `calculate.py` | Math expression evaluation (also a listener — see below) |
| fetch | `fetch.py` | HTTP requests |
| files | `files.py` | File system operations |
| shell | `shell.py` | Shell command execution |
| search | `search.py` | Web search (DuckDuckGo) |
| keyvalue | `keyvalue.py` | Key-value storage (Redis optional) |
| convert | `convert.py` | Data format conversion |
| librarian | `librarian.py` | Documentation lookup |

### Calculator Tool-as-Listener

The calculator is the first native tool with full message-bus integration: `@xmlify` payload classes, a handler function, and the existing `@tool`-decorated function — all in `xml_pipeline/tools/calculate.py`.

**Evaluation engine:** `simpleeval` (safe expression evaluation — no `ast` visitor). Supports `+ - * / // % **`, comparisons, 18 math functions (`sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `log`, `log10`, `log2`, `exp`, `floor`, `ceil`, `abs`, `round`, `min`, `max`, `pow`), and 4 constants (`pi`, `e`, `tau`, `inf`).

**Payload classes:**

```python
from xml_pipeline.tools import Calculate, CalculateResult

# Request
Calculate(expression="sqrt(16) + pi")

# Response — echoes expression, result as string, error empty on success
CalculateResult(expression="sqrt(16) + pi", result="7.141592653589793", error="")
```

**Handler:** `handle_calculate(payload, metadata)` — always uses `.respond()` (returns to caller, never forwards). On error, `result` is empty and `error` contains the message.

**Registration as a listener:**

```python
pump.register("calculator", handle_calculate, Calculate,
              description="Evaluate math expressions safely")
```

**Direct use (without message bus):**

```python
from xml_pipeline.tools import calculate
from xml_pipeline.tools.calculate import safe_eval

# @tool wrapper → ToolResult
result = await calculate(expression="2 ** 10")  # ToolResult(success=True, data=1024)

# Raw evaluation → int/float/bool
value = safe_eval("2 ** 10")  # 1024
```

**Known limitation:** `simpleeval` does not support ternary expressions (`a if cond else b`).

## System Primitives

Built-in message types in `xml_pipeline/primitives/`:

| Primitive | Purpose |
|-----------|---------|
| `Boot` | Organism initialization message |
| `TodoUntil` | Register a watcher for expected response |
| `TodoComplete` | Close a registered watcher |
| `TextInput` | User text input from console |
| `TextOutput` | Text output to console |

## Premium Librarian (Codebase Intelligence)

The Premium Librarian provides RLM-powered codebase intelligence using RAG (Retrieval-Augmented Generation). It ingests codebases, chunks them intelligently, stores in eXist-db, and answers natural language queries.

### Architecture

```
┌─────────────┐    ┌─────────────┐    ┌─────────────────┐
│   Ingest    │───▶│   Chunker   │───▶│   eXist-db      │
│  (git/tar)  │    │  (AST-based)│    │  (XML storage)  │
└─────────────┘    └─────────────┘    └────────┬────────┘
                                               │
┌─────────────┐    ┌─────────────┐    ┌────────▼────────┐
│   Query     │───▶│  RAG Search │───▶│   Online LLM    │
│  (natural   │    │  (XQuery +  │    │  (synthesis)    │
│   language) │    │   Lucene)   │    └─────────────────┘
└─────────────┘    └─────────────┘
```

### Components

| Module | Location | Purpose |
|--------|----------|---------|
| Chunker | `xml_pipeline/librarian/chunker.py` | AST-based code chunking (Python, JS, C++) |
| Ingest | `xml_pipeline/librarian/ingest.py` | Git clone + file walking + storage |
| Index | `xml_pipeline/librarian/index.py` | Structural index (files, functions, classes) |
| Query | `xml_pipeline/librarian/query.py` | RAG search + LLM synthesis |
| Primitives | `xml_pipeline/librarian/primitives.py` | XML payloads for message bus |
| Handler | `xml_pipeline/librarian/handler.py` | Message handlers |

### Usage

```python
from xml_pipeline.librarian import ingest_git_repo, query_library

# Ingest a codebase
result = await ingest_git_repo(
    url="https://github.com/example/repo.git",
    branch="main",
    library_name="my-lib",
)
print(f"Ingested {result.files_processed} files, {result.chunks_created} chunks")

# Query the library
answer = await query_library(
    library_id=result.library_id,
    question="How does the authentication system work?",
)
print(answer.answer)
for source in answer.sources:
    print(f"  - {source.file_path}:{source.start_line}-{source.end_line}")
```

### Supported Languages

| Language | Chunking Method | Features |
|----------|-----------------|----------|
| Python | AST-based | Functions, classes, methods, imports, docstrings |
| JavaScript/TypeScript | Regex-based | Functions, arrow functions, classes, JSDoc |
| C/C++ | Regex-based | Functions, classes, structs, Doxygen |
| Markdown/RST | Heading-based | Sections by headings |
| Other | Line-based | Generic chunking with smart breaks |

### Message Payloads

| Payload | Purpose |
|---------|---------|
| `LibrarianIngest` | Request to ingest a git repository |
| `LibrarianIngested` | Response with library_id and stats |
| `LibrarianQuery` | Natural language query request |
| `LibrarianAnswer` | Answer with sources and token usage |
| `LibrarianList` | Request to list all libraries |
| `LibrarianLibraries` | Response with library list |

### Requirements

- eXist-db running for chunk storage
- LLM router configured for query synthesis
- Install with: `pip install xml-pipeline[librarian]`

## Additional Resources

- @docs/api.md — **Public Python API reference** (stable imports for downstream)
- @docs/core-principles-v2.1.md — Single source of truth for architecture
- @docs/message-pump-v2.1.md — Message pump implementation details
- @docs/handler-contract-v2.1.md — Handler interface specification
- @docs/llm-router-v2.1.md — LLM backend abstraction
- @docs/platform-architecture.md — Platform-level APIs
- @docs/native_tools.md — Native tool implementations
- @docs/primitives.md — System primitives reference (includes thread lifecycle)
- @docs/configuration.md — Organism configuration reference
- @docs/split-config.md — Split configuration architecture
- @docs/why-not-json.md — Rationale for XML over JSON

> **Note:** Console, authentication, and LSP integration documentation is in [OpenBlox](https://openblox.ai).


## Skill Usage Guide

When working on tasks involving these technologies, invoke the corresponding skill:

| Skill | Invoke When |
|-------|-------------|
| simpleeval | Evaluates math expressions safely in the calculator tool/listener |
| pyhumps | Converts between snake_case and camelCase naming conventions |
| xmlable | Manages dataclass ↔ XML serialization and automatic XSD generation |
| pyyaml | Loads and validates organism.yaml configuration files |
| cryptography | Implements Ed25519 identity keys for signing and federation auth |
| httpx | Handles async HTTP requests for LLM backend communication |
| aiostream | Implements stream-based message pipeline with concurrent fan-out processing |
| lxml | Handles XML processing, XSD validation, C14N normalization, and repair |
| python | Manages async-first Python 3.11+ codebase with type hints and dataclasses |
| pytest | Runs async test suite with pytest-asyncio fixtures and markers |
