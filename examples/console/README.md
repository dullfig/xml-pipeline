# Console Example

A minimal interactive console demonstrating xml-pipeline basics.

## Quick Start

```bash
# From the repo root
python -m examples.console

# Or with a custom config
python -m examples.console path/to/organism.yaml
```

## What's Included

```
examples/console/
├── __init__.py      # Package exports
├── __main__.py      # Entry point
├── console.py       # Console implementation
├── handlers.py      # Example handlers
├── organism.yaml    # Example config
└── README.md        # This file
```

## Example Session

```
==================================================
  xml-pipeline console
==================================================

Organism: console-example
Listeners: 3
Type /help for commands

> /listeners

Listeners:
  console-output       Prints output to console
  echo                 Echoes back your message
  greeter              Greets you by name

> @greeter Alice
[sending to greeter]
[greeter] Hello, Alice! Welcome to xml-pipeline.

> @echo Hello, world!
[sending to echo]
[echo] Hello, world!

> /quit
Shutting down...
Goodbye!
```

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/listeners` | List registered listeners |
| `/status` | Show organism status |
| `/quit` | Exit |

## Sending Messages

Use `@listener message` to send a message:

```
@greeter Alice      # Greet Alice
@echo Hello!        # Echo back "Hello!"
```

## Optional Dependencies

For a better terminal experience, install prompt_toolkit:

```bash
pip install prompt_toolkit
```

Without it, the console falls back to basic `input()`.

## Customization

This example is designed to be copied and modified. Key extension points:

1. **Add handlers** — Create new payload classes and handlers in `handlers.py`
2. **Update config** — Add listeners to `organism.yaml`
3. **Modify console** — Change commands or output formatting in `console.py`

### Example: Adding a Calculator

```python
# handlers.py
@xmlify
@dataclass
class Calculate:
    expression: str

@xmlify
@dataclass
class CalculateResult:
    result: str

async def handle_calculate(payload: Calculate, metadata: HandlerMetadata) -> HandlerResponse:
    try:
        result = eval(payload.expression)  # (Use simpleeval in production!)
        text = f"{payload.expression} = {result}"
    except Exception as e:
        text = f"Error: {e}"

    return HandlerResponse(
        payload=ConsoleOutput(source="calculator", text=text),
        to="console-output",
    )
```

```yaml
# organism.yaml
listeners:
  - name: calc
    payload_class: examples.console.handlers.Calculate
    handler: examples.console.handlers.handle_calculate
    description: Evaluates math expressions
```

Then: `@calc 2 + 2` → `[calculator] 2 + 2 = 4`

## Architecture

```
User Input (@greeter Alice)
    │
    ▼
┌─────────────────────────────────────┐
│  Console                            │
│  - Parses input                     │
│  - Creates Greeting payload         │
│  - Injects into pump                │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  StreamPump                         │
│  - Validates envelope               │
│  - Routes to greeter listener       │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  handle_greeting()                  │
│  - Receives Greeting payload        │
│  - Returns ConsoleOutput            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│  handle_print()                     │
│  - Receives ConsoleOutput           │
│  - Displays on console              │
└─────────────────────────────────────┘
```

## Using in Your Project

```python
from xml_pipeline.message_bus import bootstrap
from examples.console import Console

async def main():
    pump = await bootstrap("my_organism.yaml")
    console = Console(pump)

    pump_task = asyncio.create_task(pump.run())
    try:
        await console.run()
    finally:
        pump_task.cancel()
        await pump.shutdown()
```

Or copy the entire `examples/console/` directory and modify as needed.
