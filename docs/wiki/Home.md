# xml-pipeline

**A tamper-proof nervous system for multi-agent AI systems.**

xml-pipeline (also called AgentServer) provides a schema-driven, Turing-complete message bus where AI agents communicate through validated XML payloads. It features automatic XSD generation, handler isolation, and built-in security guarantees against agent misbehavior.

## Why XML?

While JSON dominates web APIs, XML provides critical features for secure multi-agent systems:

- **Schema validation** — XSD enforces exact contracts on the wire
- **Namespaces** — Safely mix vocabularies without collision
- **Canonicalization** — C14N enables deterministic signing
- **Repair tolerance** — Malformed XML can be recovered; malformed JSON cannot

See [[Why XML]] for the full rationale.

## Key Features

| Feature | Description |
|---------|-------------|
| **Schema-Driven** | Define payloads as Python dataclasses; XSD generated automatically |
| **Handler Isolation** | Handlers are sandboxed—cannot forge identity or escape threads |
| **Thread Tracking** | Opaque UUIDs hide topology; call chains tracked privately |
| **LLM Router** | Multi-backend routing with failover, rate limiting, retries |
| **WASM Sandboxing** | Foreign code runs in wasmtime with epoch interruption |
| **OOB Channel** | Privileged localhost-only control plane for hot-reload |
| **Peer Tables** | Thread-scoped privilege tiers (e.g., premium/basic) |

## Quick Links

### Getting Started
- [[Installation]] — Install the package
- [[Quick Start]] — Run your first organism in 5 minutes
- [[Configuration]] — Configure organisms via YAML

### Guides
- [[Writing Handlers]] — Create message handlers
- [[Using the LLM Router]] — Call language models from handlers

### Architecture
- [[Architecture Overview]] — How xml-pipeline works
- [[Message Pump]] — The streaming message processor
- [[Thread Registry]] — Call chain tracking with opaque UUIDs
- [[Shared Backend]] — Cross-process state with Redis

### Tutorials
- [[Hello World Tutorial]] — Build a greeting agent step by step

### Reference
- [[Handler Contract]] — Handler function signature and return types
- [[Configuration Reference]] — Complete organism.yaml specification
- [[CLI Reference]] — Command-line interface
- [Public API Reference](../api.md) — Stable Python imports for downstream consumers

## Version

Current version: **0.4.0**

## License

MIT License

## Links

- [GitHub Repository](https://github.com/xml-pipeline/xml-pipeline)
- [PyPI Package](https://pypi.org/project/xml-pipeline/)
