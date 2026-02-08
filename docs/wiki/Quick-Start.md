# Quick Start

Get an organism running in 5 minutes.

## 1. Install the Package

```bash
pip install xml-pipeline[console]
```

## 2. Create a Project Directory

```bash
mkdir my-organism
cd my-organism
```

## 3. Initialize Configuration

```bash
xml-pipeline init my-organism
```

This creates:
```
my-organism/
├── config/
│   └── organism.yaml
├── handlers/
│   └── hello.py
└── .env.example
```

## 4. Examine the Generated Files

### config/organism.yaml

```yaml
organism:
  name: my-organism
  port: 8765

listeners:
  - name: greeter
    payload_class: handlers.hello.Greeting
    handler: handlers.hello.handle_greeting
    description: A friendly greeting handler
    peers: []
```

### handlers/hello.py

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify
from xml_pipeline import HandlerMetadata, HandlerResponse

@xmlify
@dataclass
class Greeting:
    """A greeting request."""
    name: str

@xmlify
@dataclass
class GreetingResponse:
    """A greeting response."""
    message: str

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    """Handle a greeting and respond."""
    return HandlerResponse(
        payload=GreetingResponse(message=f"Hello, {payload.name}!"),
        to=metadata.from_id,  # Reply to sender
    )
```

## 5. Run the Organism

```bash
xml-pipeline run config/organism.yaml
```

You should see:
```
Organism: my-organism
Listeners: 1
Root thread: abc123-...
Routing: ['greeter.greeting']
```

## 6. Try the Interactive Console

If you installed with `[console]`:

```bash
python -m examples.console
```

Type `@greeter Alice` to send a greeting message.

## What Just Happened?

1. **Payload defined** — `Greeting` dataclass with `@xmlify` decorator
2. **XSD generated** — Schema auto-created at `schemas/greeter/v1.xsd`
3. **Handler registered** — `handle_greeting` mapped to `greeter.greeting` root tag
4. **Message pump started** — Waiting for messages

## Understanding the Message Flow

```
Input: @greeter Alice
   │
   ▼
┌─────────────────────────────────────┐
│  XML Envelope Created               │
│  <message>                          │
│    <meta>                           │
│      <from>console</from>           │
│      <to>greeter</to>               │
│      <thread>uuid-123</thread>      │
│    </meta>                          │
│    <greeting>                       │
│      <name>Alice</name>             │
│    </greeting>                      │
│  </message>                         │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│  Pipeline Processing                │
│  1. Repair (fix malformed XML)      │
│  2. C14N (canonicalize)             │
│  3. Envelope validation             │
│  4. Payload extraction              │
│  5. XSD validation                  │
│  6. Deserialization → Greeting      │
│  7. Route to greeter handler        │
└─────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────┐
│  Handler Execution                  │
│  handle_greeting(                   │
│    payload=Greeting(name="Alice"),  │
│    metadata=HandlerMetadata(...)    │
│  )                                  │
│  → HandlerResponse(...)             │
└─────────────────────────────────────┘
   │
   ▼
Output: Hello, Alice!
```

## Next Steps

- [[Writing Handlers]] — Create your own handlers
- [[Configuration]] — Customize organism.yaml
- [[Hello World Tutorial]] — Step-by-step tutorial
