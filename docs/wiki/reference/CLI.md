# CLI Reference

xml-pipeline provides a command-line interface for running and managing organisms.

## Commands

### xml-pipeline run

Run an organism from a configuration file.

```bash
xml-pipeline run [CONFIG_PATH]
```

**Arguments:**
- `CONFIG_PATH` — Path to organism.yaml (default: `config/organism.yaml`)

**Examples:**
```bash
xml-pipeline run config/organism.yaml
xml-pipeline run                        # Uses default path
xp run config/my-organism.yaml          # Short alias
```

### xml-pipeline init

Create a new organism configuration from template.

```bash
xml-pipeline init [NAME]
```

**Arguments:**
- `NAME` — Organism name (default: `my-organism`)

**Creates:**
```
{NAME}/
├── config/
│   └── organism.yaml
├── handlers/
│   └── hello.py
└── .env.example
```

**Examples:**
```bash
xml-pipeline init my-agent
xml-pipeline init                       # Uses default name
```

### xml-pipeline check

Validate configuration without running.

```bash
xml-pipeline check [CONFIG_PATH]
```

**Arguments:**
- `CONFIG_PATH` — Path to organism.yaml (default: `config/organism.yaml`)

**Output:**
```
Config valid: hello-world
  Listeners: 3
  LLM backends: 1
```

**Examples:**
```bash
xml-pipeline check config/organism.yaml
xml-pipeline check                      # Uses default path
```

### xml-pipeline version

Show version and installed features.

```bash
xml-pipeline version
```

**Output:**
```
xml-pipeline 0.4.0
Python 3.11.5
Features: anthropic, console, redis, search
```

## Short Alias

The `xp` command is a short alias for `xml-pipeline`:

```bash
xp run config/organism.yaml
xp init my-agent
xp check
xp version
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `XAI_API_KEY` | xAI (Grok) API key |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) API key |
| `OPENAI_API_KEY` | OpenAI API key |

Create a `.env` file in your project root:

```env
XAI_API_KEY=xai-...
ANTHROPIC_API_KEY=sk-ant-...
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Configuration error |
| 2 | Runtime error |

## See Also

- [[Installation]] — Installing xml-pipeline
- [[Quick Start]] — Getting started
- [[Configuration]] — Configuration reference
