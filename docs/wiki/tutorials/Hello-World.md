# Hello World Tutorial

Build a complete greeting agent from scratch. By the end, you'll understand payloads, handlers, configuration, and message flow.

## What We're Building

A greeting system with three components:

```
User Input → Greeter Agent → Shouter Tool → Output
    │            │               │            │
    │            │               │            │
  "Alice"    "Hello,        "HELLO,      Displayed
             Alice!"        ALICE!"      to user
```

## Prerequisites

- Python 3.11+
- xml-pipeline installed (`pip install xml-pipeline[console]`)

## Step 1: Create Project Structure

```bash
mkdir hello-world
cd hello-world
mkdir -p config handlers
```

## Step 2: Define Payloads

Create `handlers/payloads.py`:

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    """Request to greet someone."""
    name: str

@xmlify
@dataclass
class GreetingResponse:
    """A friendly greeting."""
    message: str

@xmlify
@dataclass
class ShoutRequest:
    """Request to shout text."""
    text: str

@xmlify
@dataclass
class ShoutResponse:
    """Shouted text."""
    text: str

@xmlify
@dataclass
class ConsoleOutput:
    """Text to display."""
    text: str
    source: str = "system"
```

**What's happening:**
- `@xmlify` enables XML serialization
- `@dataclass` provides the fields
- Each class becomes a valid XML payload

## Step 3: Write Handlers

Create `handlers/greeter.py`:

```python
from xml_pipeline import HandlerMetadata, HandlerResponse
from handlers.payloads import Greeting, GreetingResponse, ShoutRequest

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Receive a greeting request, create a friendly message,
    then forward to the shouter to make it exciting.
    """
    # Create the greeting
    message = f"Hello, {payload.name}! Welcome to xml-pipeline!"

    # Forward to shouter (will come back to us? No - goes to output)
    return HandlerResponse(
        payload=ShoutRequest(text=message),
        to="shouter",
    )
```

Create `handlers/shouter.py`:

```python
from xml_pipeline import HandlerMetadata, HandlerResponse
from handlers.payloads import ShoutRequest, ConsoleOutput

async def handle_shout(payload: ShoutRequest, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Take text and SHOUT IT!
    Then send to console for display.
    """
    shouted = payload.text.upper() + "!!!"

    return HandlerResponse(
        payload=ConsoleOutput(text=shouted, source="shouter"),
        to="console-output",
    )
```

Create `handlers/output.py`:

```python
from xml_pipeline import HandlerMetadata
from handlers.payloads import ConsoleOutput

async def handle_output(payload: ConsoleOutput, metadata: HandlerMetadata) -> None:
    """
    Display text to console.
    Returns None to end the message chain.
    """
    print(f"\n[{payload.source}] {payload.text}\n")
    return None  # Chain ends here
```

## Step 4: Configure the Organism

Create `config/organism.yaml`:

```yaml
organism:
  name: hello-world

listeners:
  # The greeter agent
  - name: greeter
    payload_class: handlers.payloads.Greeting
    handler: handlers.greeter.handle_greeting
    description: "Greets people by name"
    peers:
      - shouter

  # The shouter tool
  - name: shouter
    payload_class: handlers.payloads.ShoutRequest
    handler: handlers.shouter.handle_shout
    description: "Makes text LOUD"
    peers:
      - console-output

  # Output handler
  - name: console-output
    payload_class: handlers.payloads.ConsoleOutput
    handler: handlers.output.handle_output
    description: "Displays text to console"
```

## Step 5: Verify Configuration

```bash
xml-pipeline check config/organism.yaml
```

Expected output:
```
Config valid: hello-world
  Listeners: 3
  LLM backends: 0
```

## Step 6: Create Test Script

Create `test_greeting.py`:

```python
import asyncio
from xml_pipeline import StreamPump
from handlers.payloads import Greeting

async def main():
    # Create the pump using the YAML config
    pump = await StreamPump.from_yaml("config/organism.yaml")

    print("Organism started! Injecting greeting...")

    # Inject a typed payload — no raw XML needed
    await pump.inject("greeter", Greeting(name="Alice"))

    # Give it time to process
    await asyncio.sleep(1)

    # Shutdown
    await pump.shutdown()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
```

## Step 7: Run It

```bash
python test_greeting.py
```

Expected output:
```
Organism started! Injecting greeting...

[shouter] HELLO, ALICE! WELCOME TO XML-PIPELINE!!!!

Done!
```

## What Just Happened?

Let's trace the message flow:

### 1. Message Injection

```python
await pump.inject("greeter", Greeting(name="Alice"))
```

The pump serializes the payload to XML and wraps it in an envelope with `from=external`, `to=greeter`.

### 2. Pipeline Processing

```
Raw bytes
    ↓
repair_step      → Valid XML tree
    ↓
c14n_step        → Canonicalized
    ↓
envelope_valid   → Matches envelope.xsd
    ↓
payload_extract  → Extracts <greeting>
    ↓
thread_assign    → UUID: abc-123
    ↓
xsd_validate     → Matches Greeting schema
    ↓
deserialize      → Greeting(name="Alice")
    ↓
routing          → target: greeter
```

### 3. Handler Dispatch

```python
# greeter receives:
payload = Greeting(name="Alice")
metadata.thread_id = "abc-123"
metadata.from_id = "external"
```

### 4. Response Processing

Greeter returns:
```python
HandlerResponse(
    payload=ShoutRequest(text="Hello, Alice!..."),
    to="shouter",
)
```

System:
1. Validates `shouter` is in greeter's peers ✓
2. Serializes ShoutRequest to XML
3. Wraps in envelope with `from=greeter`
4. Re-injects into pipeline

### 5. Chain Continues

Shouter receives ShoutRequest, returns ConsoleOutput to `console-output`.

### 6. Chain Terminates

`handle_output` returns `None` → chain ends.

## Exercises

### Exercise 1: Add a Counter

Modify the shouter to count how many times it's been called:

```python
# Hint: Use a module-level variable (simple) or
# the context buffer (proper way)
```

### Exercise 2: Add Error Handling

What happens if someone sends an empty name? Add validation:

```python
async def handle_greeting(payload: Greeting, metadata: HandlerMetadata):
    if not payload.name.strip():
        return HandlerResponse(
            payload=ConsoleOutput(text="Error: Name required", source="greeter"),
            to="console-output",
        )
    # ... rest of handler
```

### Exercise 3: Make Greeter an LLM Agent

Convert greeter to use an LLM for creative greetings:

```python
from xml_pipeline.llm import complete

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata):
    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": "Generate a creative, friendly greeting."},
            {"role": "user", "content": f"Greet someone named {payload.name}"},
        ],
    )

    return HandlerResponse(
        payload=ShoutRequest(text=response.content),
        to="shouter",
    )
```

Don't forget to add LLM configuration:

```yaml
llm:
  backends:
    - provider: xai
      api_key_env: XAI_API_KEY
```

## Summary

You've learned:
- **Payloads**: Define messages with `@xmlify` dataclasses
- **Handlers**: Async functions that process and respond
- **Configuration**: Wire everything in organism.yaml
- **Message Flow**: How messages traverse the pipeline
- **Chaining**: Handlers forward to each other via `HandlerResponse`

## Next Steps

- [[Writing Handlers]] — More handler patterns
- [[Configuration]] — Full configuration reference
- [[Architecture Overview]] — Deep dive into internals
