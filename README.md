# xml-pipeline

**Schema-driven XML message bus for multi-agent systems.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

xml-pipeline is a Python library for building multi-agent systems with validated XML message passing. Agents communicate through typed payloads, validated against auto-generated XSD schemas, with built-in LLM routing and conversation memory.

## Why XML?

JSON was a quick hack that became the default for AI tool calling, where its brittleness causes endless prompt surgery and validation headaches. xml-pipeline chooses XML deliberately:

- **Exact contracts** — XSD validation catches malformed messages before they cause problems
- **Tolerant parsing** — Repair mode recovers from LLM output quirks
- **Self-describing** — Namespaces prevent collision, schemas are discoverable
- **No escaping hell** — Mixed content, nested structures, all handled cleanly

Read the [full rationale](docs/why-not-json.md).

## Installation

```bash
pip install xml-pipeline

# With LLM provider support
pip install xml-pipeline[anthropic]    # Anthropic Claude
pip install xml-pipeline[openai]       # OpenAI GPT

# With all features
pip install xml-pipeline[all]
```

## Quick Start

### 1. Define a payload

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    name: str
```

### 2. Write a handler

```python
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

@xmlify
@dataclass
class GreetingReply:
    message: str

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    return HandlerResponse(
        payload=GreetingReply(message=f"Hello, {payload.name}!"),
        to="output",
    )
```

### 3. Configure the organism

```yaml
# organism.yaml
organism:
  name: hello-world

listeners:
  - name: greeter
    payload_class: myapp.Greeting
    handler: myapp.handle_greeting
    description: Greets users by name

  - name: output
    payload_class: myapp.GreetingReply
    handler: myapp.print_output
    description: Prints output
```

### 4. Run it

```python
import asyncio
from xml_pipeline.message_bus import bootstrap

async def main():
    pump = await bootstrap("organism.yaml")
    await pump.run()

asyncio.run(main())
```

## Console Example

Try the interactive console example:

```bash
pip install xml-pipeline[console]
python -m examples.console
```

```
> @greeter Alice
[greeter] Hello, Alice! Welcome to xml-pipeline.

> @echo Hello world
[echo] Hello world

> /quit
```

See [examples/console/](examples/console/) for the full source.

## Key Features

### Typed Message Passing

Payloads are Python dataclasses with automatic XSD generation:

```python
@xmlify
@dataclass
class Calculate:
    expression: str
    precision: int = 2
```

The library auto-generates:
- XSD schema for validation
- Example XML for documentation
- Usage instructions for LLM prompts

### LLM Router

Multi-backend LLM support with failover:

```yaml
llm:
  strategy: failover
  backends:
    - provider: anthropic
      api_key_env: ANTHROPIC_API_KEY
    - provider: openai
      api_key_env: OPENAI_API_KEY
```

```python
from xml_pipeline.llm import complete

response = await complete(
    model="claude-sonnet-4",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### Handler Security

Handlers are sandboxed. They cannot:
- Forge sender identity (injected by pump)
- Escape thread context (managed by registry)
- Route to undeclared peers (validated against config)
- Access other threads (opaque UUIDs)

### Conversation Memory

Thread-scoped context buffer tracks message history:

```python
from xml_pipeline.memory import get_context_buffer

buffer = get_context_buffer()
history = buffer.get_thread(metadata.thread_id)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         StreamPump                               │
│  • Parallel pipelines per listener                               │
│  • Repair → C14N → Validate → Deserialize → Route → Dispatch    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                          Handlers                                │
│  • Receive typed payload + metadata                              │
│  • Return HandlerResponse or None                                │
│  • Cannot forge identity or escape thread                        │
└─────────────────────────────────────────────────────────────────┘
```

See [docs/core-principles-v2.1.md](docs/core-principles-v2.1.md) for the full architecture.

## Documentation

| Document | Description |
|----------|-------------|
| [Core Principles](docs/core-principles-v2.1.md) | Architecture overview |
| [Handler Contract](docs/handler-contract-v2.1.md) | How to write handlers |
| [Message Pump](docs/message-pump-v2.1.md) | Pipeline processing |
| [LLM Router](docs/llm-router-v2.1.md) | Multi-backend LLM support |
| [Configuration](docs/configuration.md) | organism.yaml reference |
| [Why Not JSON?](docs/why-not-json.md) | Design rationale |

## Requirements

- Python 3.11+
- Dependencies: lxml, aiostream, pyyaml, httpx, cryptography

## License

MIT License. See [LICENSE](LICENSE).

---

*XML wins. Safely. Permanently.*
