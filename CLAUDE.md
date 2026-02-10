# AgentServer (xml-pipeline)

A tamper-proof nervous system for multi-agent AI systems using XML as the sovereign wire format. Schema-driven, Turing-complete message bus with automatic XSD generation, handler isolation, and built-in security guarantees.

**Version:** 0.4.0

See @docs/api.md for the stable public API.
See @docs/configuration.md for organism.yaml format.

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Runtime | Python 3.11+ | Async-first, type-hinted codebase |
| Streaming | aiostream 0.5+ | Stream-based message pipeline with fan-out |
| XML | lxml | XSD validation, C14N normalization, repair |
| Serialization | xmlable (vendored) | Dataclass <-> XML round-trip with auto-XSD |
| Config | PyYAML | Organism configuration (organism.yaml) |
| Crypto | cryptography | Ed25519 identity keys for signing |
| HTTP | httpx 0.27+ | LLM backend communication |
| Math | simpleeval 0.9+ | Safe expression evaluation (calculator) |

## Quick Start

```bash
pip install -e ".[all]"
cp .env.example .env
pytest tests/ -v
xml-pipeline run config/organism.yaml
```

## Project Structure

```
xml-pipeline/
├── xml_pipeline/              # Main package
│   ├── config/               # Config loading and templates
│   ├── listeners/            # Listener implementations
│   ├── llm/                  # LLM router, backends, token bucket
│   ├── memory/               # Context buffer for conversation history
│   ├── message_bus/          # Core message pump and pipeline
│   │   ├── steps/            # Pipeline steps (repair, c14n, validation)
│   │   ├── stream_pump.py    # Main StreamPump class
│   │   ├── events.py         # PumpEvent types
│   │   ├── pump_config.py    # ListenerConfig, OrganismConfig, Listener
│   │   ├── config_loader.py  # YAML config parser
│   │   ├── pipeline.py       # Pipeline helpers (extract, validate, deser)
│   │   ├── singleton.py      # Global pump singleton
│   │   ├── message_state.py  # MessageState, HandlerMetadata, HandlerResponse
│   │   ├── thread_registry.py # Opaque UUID <-> call chain mapping
│   │   └── system_pipeline.py # External message injection
│   ├── oob/                  # Out-of-band privileged channel
│   ├── crypto/               # Identity keys, TOTP authentication
│   ├── platform/             # Prompt registry, LLM API
│   ├── primitives/           # System message types (Boot, TodoUntil)
│   ├── tools/                # Native tools (calculate, fetch, search)
│   ├── workers/              # Background worker process registry
│   └── librarian/            # Codebase intelligence (RAG)
├── config/                   # Example organism configurations
├── docs/                     # Architecture and design docs
├── tests/                    # pytest test suite
└── pyproject.toml            # Project metadata and dependencies
```

## Architecture Overview

Stream-based message pump where all communication flows through validated XML envelopes. Strict isolation between handlers (untrusted code) and the system (trusted zone).

**Message Flow:** Raw bytes -> Repair -> C14N -> Envelope validation -> Payload extraction -> Thread assignment -> XSD validation -> Deserialization -> Routing -> Handler dispatch -> Response wrapping -> Re-injection

## Development Guidelines

### Naming
- Classes: `PascalCase` | Functions/methods: `snake_case` | Constants: `SCREAMING_SNAKE_CASE`
- Private: `_leading_underscore` | Files: `snake_case.py` | Tests: `test_*.py`

### Payload Classes
```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    name: str
```

### Handler Pattern
```python
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    return HandlerResponse(payload=GreetingResponse(message="Hello!"), to="next-listener")
```

### Import Order
1. `from __future__ import annotations`
2. Standard library
3. Third-party (lxml, aiostream)
4. `xml_pipeline.*`
5. `third_party.*`

## Available Commands

| Command | Description |
|---------|-------------|
| `xml-pipeline run [config]` | Run organism from config file |
| `xml-pipeline check [config]` | Validate config without running |
| `xml-pipeline keygen` | Generate Ed25519 identity key |
| `xml-pipeline keygen --totp` | Generate TOTP secret + provisioning URI |
| `pytest tests/ -v` | Run test suite |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `XAI_API_KEY` | xAI (Grok) API key |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `ORGANISM_TOTP_SECRET` | Base32 TOTP secret for OOB auth |

## Testing

- **Framework:** pytest with pytest-asyncio
- **Async tests:** `@pytest.mark.asyncio`
- **Markers:** `@pytest.mark.slow`, `@pytest.mark.integration`

```bash
pytest tests/ -v                           # All tests
pytest tests/test_pipeline_steps.py -v     # Specific file
pytest tests/ -v -k "repair"              # Pattern match
```

## Security Model

- **Handler Isolation:** Handlers cannot forge identity, escape threads, or probe topology
- **Peer Constraints:** Agents can only send to declared peers in config
- **Peer Tables:** Named, mutable peer mappings for thread-scoped privilege enforcement. Subtract-only ceiling hierarchy. Declarable in YAML or via API/OOB.
- **TOTP Authentication:** RFC 6238 on OOB channel as second factor alongside Ed25519
- **Opaque Thread UUIDs:** Handlers see only UUIDs, never internal call chains
- **Envelope Injection:** `<from>`, `<thread>`, `<to>` always set by system
- **OOB Channel:** Privileged commands on separate localhost-only channel

## Native Tools

| Tool | File | Purpose |
|------|------|---------|
| calculate | `calculate.py` | Math expression evaluation (also a listener) |
| fetch | `fetch.py` | HTTP requests |
| files | `files.py` | File system operations (disabled) |
| shell | `shell.py` | Shell command execution (OS-isolated via xp-exec; @tool disabled, handler active) |
| search | `search.py` | Web search (DuckDuckGo) |
| keyvalue | `keyvalue.py` | Key-value storage (SQLite/Redis) |
| convert | `convert.py` | Data format conversion |
| librarian | `librarian.py` | Documentation lookup |

## Additional Resources

These docs are available for deep-dives into specific subsystems:

- docs/core-principles-v2.1.md — Architecture principles and security model
- docs/handler-contract-v2.1.md — Handler interface specification
- docs/message-pump-v2.1.md — Pump pipeline internals
- docs/llm-router-v2.1.md — LLM routing and backends
- docs/platform-architecture.md — Platform-level APIs
- docs/native_tools.md — Tool implementations
- docs/primitives.md — System message types and thread lifecycle
- docs/split-config.md — Split configuration architecture
- docs/why-not-json.md — Rationale for XML over JSON

## Skill Usage Guide

| Skill | Invoke When |
|-------|-------------|
| simpleeval | Evaluates math expressions safely in the calculator tool/listener |
| pyhumps | Converts between snake_case and camelCase naming conventions |
| xmlable | Manages dataclass <-> XML serialization and automatic XSD generation |
| pyyaml | Loads and validates organism.yaml configuration files |
| cryptography | Implements Ed25519 identity keys for signing and federation auth |
| httpx | Handles async HTTP requests for LLM backend communication |
| aiostream | Implements stream-based message pipeline with concurrent fan-out processing |
| lxml | Handles XML processing, XSD validation, C14N normalization, and repair |
| python | Manages async-first Python 3.11+ codebase with type hints and dataclasses |
| pytest | Runs async test suite with pytest-asyncio fixtures and markers |
