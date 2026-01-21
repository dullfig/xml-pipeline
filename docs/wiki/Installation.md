# Installation

## Requirements

- Python 3.11 or higher
- pip, uv, or pipx

## Install from PyPI

```bash
pip install xml-pipeline
```

## Install with Optional Features

xml-pipeline has optional dependencies for different use cases:

```bash
# Core only (minimal)
pip install xml-pipeline

# With Anthropic Claude support
pip install xml-pipeline[anthropic]

# With OpenAI support
pip install xml-pipeline[openai]

# With Redis for distributed deployments
pip install xml-pipeline[redis]

# With web search capability
pip install xml-pipeline[search]

# With interactive console (for examples)
pip install xml-pipeline[console]

# Everything
pip install xml-pipeline[all]

# Development (includes testing tools)
pip install xml-pipeline[dev]
```

## Install from Source

```bash
# Clone the repository
git clone https://github.com/xml-pipeline/xml-pipeline.git
cd xml-pipeline

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/macOS)
source .venv/bin/activate

# Install in development mode
pip install -e ".[all]"
```

## Verify Installation

```bash
# Check version
xml-pipeline version

# Or use the short alias
xp version
```

Expected output:
```
xml-pipeline 0.4.0
Python 3.11.x
Features: anthropic, console, redis, search
```

## Environment Variables

Create a `.env` file in your project root for API keys:

```env
# LLM Provider Keys (add the ones you need)
XAI_API_KEY=xai-...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

The library automatically loads `.env` files via python-dotenv.

## Next Steps

- [[Quick Start]] — Run your first organism
- [[Configuration]] — Learn about organism.yaml
