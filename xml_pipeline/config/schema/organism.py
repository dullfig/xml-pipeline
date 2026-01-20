"""
JSON Schema for organism.yaml files.

This schema enables yaml-language-server to provide:
- Autocompletion for fields
- Validation of field types
- Documentation on hover
"""

ORGANISM_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://xml-pipeline.org/schemas/organism.schema.json",
    "title": "Organism Configuration",
    "description": "Configuration for an xml-pipeline organism",
    "type": "object",
    "required": ["organism"],
    "additionalProperties": False,
    "properties": {
        "organism": {
            "type": "object",
            "description": "Core organism settings",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name for this organism",
                    "pattern": "^[a-zA-Z][a-zA-Z0-9_-]*$",
                },
                "port": {
                    "type": "integer",
                    "description": "WebSocket server port",
                    "default": 8765,
                    "minimum": 1,
                    "maximum": 65535,
                },
                "version": {
                    "type": "string",
                    "description": "Organism version (semver)",
                    "default": "0.1.0",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description",
                },
                "identity": {
                    "type": "string",
                    "description": "Path to Ed25519 private key for signing",
                },
                "thread_scheduling": {
                    "type": "string",
                    "description": "Thread execution policy",
                    "enum": ["breadth-first", "depth-first"],
                    "default": "breadth-first",
                },
                "max_concurrent_pipelines": {
                    "type": "integer",
                    "description": "Maximum concurrent pipeline executions",
                    "default": 100,
                    "minimum": 1,
                },
                "max_concurrent_handlers": {
                    "type": "integer",
                    "description": "Maximum concurrent handler executions",
                    "default": 50,
                    "minimum": 1,
                },
                "max_concurrent_per_agent": {
                    "type": "integer",
                    "description": "Maximum concurrent requests per agent",
                    "default": 5,
                    "minimum": 1,
                },
            },
        },
        "tls": {
            "type": "object",
            "description": "TLS configuration for WebSocket server",
            "properties": {
                "cert": {
                    "type": "string",
                    "description": "Path to certificate file (PEM)",
                },
                "key": {
                    "type": "string",
                    "description": "Path to private key file (PEM)",
                },
            },
        },
        "oob": {
            "type": "object",
            "description": "Out-of-band privileged channel configuration",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                },
                "bind": {
                    "type": "string",
                    "description": "Bind address (localhost only for security)",
                    "default": "127.0.0.1",
                },
                "port": {
                    "type": "integer",
                    "description": "OOB channel port",
                    "minimum": 1,
                    "maximum": 65535,
                },
                "unix_socket": {
                    "type": "string",
                    "description": "Unix socket path (alternative to port)",
                },
            },
        },
        "meta": {
            "type": "object",
            "description": "Introspection settings",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                },
                "allow_list_capabilities": {
                    "type": "boolean",
                    "default": True,
                },
                "allow_schema_requests": {
                    "type": "string",
                    "enum": ["admin", "authenticated", "none"],
                    "default": "admin",
                },
                "allow_example_requests": {
                    "type": "string",
                    "enum": ["admin", "authenticated", "none"],
                    "default": "admin",
                },
                "allow_prompt_requests": {
                    "type": "string",
                    "enum": ["admin", "authenticated", "none"],
                    "default": "admin",
                },
                "allow_remote": {
                    "type": "boolean",
                    "description": "Allow federation peers to query meta",
                    "default": False,
                },
            },
        },
        "listeners": {
            "oneOf": [
                {
                    "type": "array",
                    "description": "Inline listener configurations (legacy format)",
                    "items": {
                        "$ref": "#/$defs/listener",
                    },
                },
                {
                    "type": "object",
                    "description": "Split listener configuration",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Path to listeners directory",
                            "default": "~/.xml-pipeline/listeners",
                        },
                        "include": {
                            "type": "array",
                            "description": "Glob patterns to include",
                            "items": {"type": "string"},
                            "default": ["*.yaml"],
                        },
                    },
                },
            ],
        },
        "gateways": {
            "type": "array",
            "description": "Federation gateway configurations",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Gateway identifier",
                    },
                    "remote_url": {
                        "type": "string",
                        "description": "Remote organism WebSocket URL",
                        "format": "uri",
                    },
                    "trusted_identity": {
                        "type": "string",
                        "description": "Path to trusted public key",
                    },
                    "description": {
                        "type": "string",
                    },
                },
            },
        },
        "llm": {
            "type": "object",
            "description": "LLM router configuration",
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": "Backend selection strategy",
                    "enum": ["failover", "round-robin", "least-loaded"],
                    "default": "failover",
                },
                "retries": {
                    "type": "integer",
                    "description": "Max retry attempts per request",
                    "default": 3,
                    "minimum": 0,
                },
                "retry_base_delay": {
                    "type": "number",
                    "description": "Base delay for exponential backoff (seconds)",
                    "default": 1.0,
                },
                "retry_max_delay": {
                    "type": "number",
                    "description": "Maximum delay between retries (seconds)",
                    "default": 60.0,
                },
                "backends": {
                    "type": "array",
                    "description": "LLM backend configurations",
                    "items": {
                        "$ref": "#/$defs/llmBackend",
                    },
                },
            },
        },
        "server": {
            "type": "object",
            "description": "WebSocket server configuration",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                },
                "host": {
                    "type": "string",
                    "default": "127.0.0.1",
                },
                "port": {
                    "type": "integer",
                    "default": 8765,
                    "minimum": 1,
                    "maximum": 65535,
                },
            },
        },
        "auth": {
            "type": "object",
            "description": "Authentication configuration",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": False,
                },
                "totp_secret_env": {
                    "type": "string",
                    "description": "Environment variable containing TOTP secret",
                    "default": "ORGANISM_TOTP_SECRET",
                },
            },
        },
    },
    "$defs": {
        "listener": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique listener name",
                    "pattern": "^[a-zA-Z][a-zA-Z0-9_.-]*$",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description (required for tool prompts)",
                },
                "agent": {
                    "type": "boolean",
                    "description": "LLM-powered agent (requires unique root tag)",
                    "default": False,
                },
                "tool": {
                    "type": "boolean",
                    "description": "Simple tool/function handler",
                    "default": False,
                },
                "gateway": {
                    "type": "boolean",
                    "description": "Federation gateway",
                    "default": False,
                },
                "broadcast": {
                    "type": "boolean",
                    "description": "Allow shared root tag with other listeners",
                    "default": False,
                },
                "handler": {
                    "type": "string",
                    "description": "Python import path to handler function",
                },
                "payload_class": {
                    "type": "string",
                    "description": "Python import path to @xmlify dataclass",
                },
                "prompt": {
                    "type": "string",
                    "description": "System prompt for LLM agent",
                },
                "model": {
                    "type": "string",
                    "description": "LLM model to use",
                },
                "peers": {
                    "type": "array",
                    "description": "Allowed message targets",
                    "items": {"type": "string"},
                },
                "allowed_tools": {
                    "type": "array",
                    "description": "Explicitly allowed tools",
                    "items": {"type": "string"},
                },
                "blocked_tools": {
                    "type": "array",
                    "description": "Explicitly blocked tools",
                    "items": {"type": "string"},
                },
            },
        },
        "llmBackend": {
            "type": "object",
            "required": ["provider"],
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "LLM provider type",
                    "enum": ["xai", "anthropic", "openai", "ollama"],
                },
                "api_key_env": {
                    "type": "string",
                    "description": "Environment variable containing API key",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority for failover (lower = preferred)",
                    "default": 0,
                },
                "rate_limit_tpm": {
                    "type": "integer",
                    "description": "Tokens per minute limit",
                },
                "max_concurrent": {
                    "type": "integer",
                    "description": "Maximum concurrent requests",
                    "default": 20,
                },
                "base_url": {
                    "type": "string",
                    "description": "Override default API endpoint",
                    "format": "uri",
                },
                "supported_models": {
                    "type": "array",
                    "description": "Model names this backend handles (Ollama)",
                    "items": {"type": "string"},
                },
            },
        },
    },
}
