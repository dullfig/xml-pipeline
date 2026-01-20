# Split Configuration Architecture

**Status:** Implemented
**Date:** January 2026

The split configuration architecture allows separating listener definitions from the
core organism configuration, making it easier to manage large organisms with many listeners.

## Overview

Instead of a monolithic `organism.yaml` with all listeners embedded, you can:

```
~/.xml-pipeline/
├── organism.yaml           # Core settings only
└── listeners/              # Per-listener configs
    ├── greeter.yaml
    ├── calculator.yaml
    └── summarizer.yaml
```

## File Structure

### organism.yaml (Core Only)

```yaml
organism:
  name: hello-world
  port: 8765

llm:
  strategy: failover
  backends:
    - provider: xai
      api_key_env: XAI_API_KEY

listeners:
  directory: "~/.xml-pipeline/listeners"
  include: ["*.yaml"]
```

The `listeners` section can either:
1. **Inline definitions** — Traditional embedded listener list
2. **Directory reference** — Point to a folder of listener files

### Listener Files

Each listener file defines a single listener:

```yaml
# ~/.xml-pipeline/listeners/greeter.yaml
# yaml-language-server: $schema=~/.xml-pipeline/schemas/listener.schema.json

name: greeter
description: Greeting agent
agent: true
handler: handlers.hello.handle_greeting
payload_class: handlers.hello.Greeting
prompt: |
  You are a friendly greeter agent.
  Keep responses short and enthusiastic.
peers:
  - shouter
  - logger
```

### File Naming Convention

| Pattern | Example | Result |
|---------|---------|--------|
| `{name}.yaml` | `greeter.yaml` | Listener named "greeter" |
| `{category}.{name}.yaml` | `calculator.add.yaml` | Listener named "calculator.add" |

The filename (without extension) should match the `name` field inside the file.

## API

### Loading Split Config

```python
from xml_pipeline.config.split_loader import load_split_config, load_organism_yaml

# Load full config (organism + listeners)
config = load_split_config("config/organism.yaml")

# Load organism.yaml only (as raw YAML string)
yaml_content = load_organism_yaml()
```

### Listener Config Store

```python
from xml_pipeline.config.listeners import (
    ListenerConfigStore,
    get_listener_config_store,
    LISTENERS_DIR,
)

store = get_listener_config_store()

# List all listener configs
names = store.list_listeners()
# ['greeter', 'calculator.add', 'summarizer']

# Load a listener config
config = store.get("greeter")
# ListenerConfigData(name='greeter', description='...', ...)

# Load as YAML string
yaml_content = store.load_yaml("greeter")

# Save a listener config
store.save_yaml("greeter", updated_yaml)
```

## Console Integration

The `/config` command supports split configs:

| Command | Action |
|---------|--------|
| `/config` | Show current organism.yaml |
| `/config -e` | Edit organism.yaml |
| `/config @greeter` | Edit listeners/greeter.yaml |
| `/config --list` | List all listener configs |

Example session:
```
> /config --list
Listener configs in ~/.xml-pipeline/listeners:
  greeter
  calculator.add
  summarizer

> /config @greeter
[Opens editor for greeter.yaml with LSP support]
```

## Migration from Monolithic Config

### Step 1: Create Listeners Directory

```bash
mkdir -p ~/.xml-pipeline/listeners
```

### Step 2: Extract Listener Definitions

For each listener in your organism.yaml:

```yaml
# Before (in organism.yaml)
listeners:
  - name: greeter
    description: Greeting agent
    handler: handlers.hello.handle_greeting
    # ... more fields
```

Create a separate file:

```yaml
# ~/.xml-pipeline/listeners/greeter.yaml
name: greeter
description: Greeting agent
handler: handlers.hello.handle_greeting
# ... more fields
```

### Step 3: Update organism.yaml

Replace the inline listeners with a directory reference:

```yaml
# After (organism.yaml)
listeners:
  directory: "~/.xml-pipeline/listeners"
  include: ["*.yaml"]
```

### Step 4: Validate

Run the organism to verify configs load correctly:

```bash
python run_organism.py config/organism.yaml
```

## Schema Validation

Listener files can use JSON Schema validation via yaml-language-server:

```yaml
# yaml-language-server: $schema=~/.xml-pipeline/schemas/listener.schema.json
name: greeter
# ...
```

Generate schemas with:
```python
from xml_pipeline.config.schema import ensure_schemas
ensure_schemas()  # Creates ~/.xml-pipeline/schemas/
```

## Benefits

| Benefit | Description |
|---------|-------------|
| **Modularity** | Each listener is self-contained |
| **Version control** | Track listener changes independently |
| **Team collaboration** | Different people own different listeners |
| **Hot-reload friendly** | (Future) Reload single listener without restarting |
| **IDE support** | LSP works per-file with focused schema |

## Limitations

- Listener files must be YAML (no JSON support)
- Directory must be readable at startup
- Circular dependencies not detected (future improvement)
- Hot-reload of individual listeners not yet implemented

## Related Documentation

- [Configuration](configuration.md) — Full organism.yaml reference
- [LSP Integration](lsp-integration.md) — Editor support for config files
- [Secure Console](secure-console-v3.md) — Console commands

---

**v2.1 Feature** — January 2026
