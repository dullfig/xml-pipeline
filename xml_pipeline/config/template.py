"""
Configuration templates for xml-pipeline.

Generates starter organism.yaml files.
"""


def create_organism_template(name: str = "my-organism") -> str:
    """Create a starter organism.yaml configuration."""
    return f'''# {name} - xml-pipeline organism configuration
# Documentation: https://github.com/yourorg/xml-pipeline

organism:
  name: {name}
  version: "0.1.0"
  description: "A multi-agent organism"

# =============================================================================
# LLM BACKENDS
# Configure which LLM providers to use. Agents will use these for inference.
# API keys are read from environment variables.
# =============================================================================
llm_backends:
  - name: primary
    provider: xai          # xai, anthropic, openai, ollama
    model: grok-2
    api_key_env: XAI_API_KEY
    priority: 0            # Lower = preferred

  # Uncomment to add fallback backends:
  # - name: fallback
  #   provider: anthropic
  #   model: claude-3-sonnet-20240229
  #   api_key_env: ANTHROPIC_API_KEY
  #   priority: 1

# =============================================================================
# LISTENERS
# Define agents, tools, and gateways that make up your organism.
# =============================================================================
listeners:
  # -----------------------------------------------------------------------------
  # Example agent - an LLM-powered assistant
  # -----------------------------------------------------------------------------
  - name: assistant
    agent: true
    description: "A helpful assistant that can use tools"
    prompt: |
      You are a helpful assistant. You can use tools to help users.
      Always be concise and accurate.
    model: grok-2          # Override default model (optional)
    peers:
      - calculator         # Can call calculator tool
      - fetcher            # Can call fetch tool
    allowed_tools:
      - calculate
      - fetch

  # -----------------------------------------------------------------------------
  # Example tool - calculator
  # -----------------------------------------------------------------------------
  - name: calculator
    tool: true
    description: "Evaluates mathematical expressions"
    handler: agentserver.tools.calculate:calculate_handler
    payload_class: agentserver.tools.calculate:CalculateRequest

  # -----------------------------------------------------------------------------
  # Example tool - HTTP fetcher
  # -----------------------------------------------------------------------------
  - name: fetcher
    tool: true
    description: "Fetches content from URLs"
    handler: agentserver.tools.fetch:fetch_handler
    payload_class: agentserver.tools.fetch:FetchRequest

# =============================================================================
# OPTIONAL: WebSocket server for remote connections
# Uncomment to enable. Requires: pip install xml-pipeline[server]
# =============================================================================
# server:
#   enabled: true
#   host: "127.0.0.1"
#   port: 8765

# =============================================================================
# OPTIONAL: Authentication for privileged operations
# Uncomment to enable. Requires: pip install xml-pipeline[auth]
# =============================================================================
# auth:
#   enabled: true
#   totp_secret_env: ORGANISM_TOTP_SECRET
'''


def create_minimal_template(name: str = "simple") -> str:
    """Create a minimal organism.yaml with just one agent."""
    return f'''organism:
  name: {name}

llm_backends:
  - name: default
    provider: xai
    model: grok-2
    api_key_env: XAI_API_KEY

listeners:
  - name: assistant
    agent: true
    description: "A simple assistant"
    prompt: "You are a helpful assistant."
'''
