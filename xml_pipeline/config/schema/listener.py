"""
JSON Schema for listener.yaml files.

This schema enables yaml-language-server to provide:
- Autocompletion for listener configuration fields
- Validation of field types
- Documentation on hover
"""

LISTENER_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://xml-pipeline.org/schemas/listener.schema.json",
    "title": "Listener Configuration",
    "description": "Configuration for an individual listener in xml-pipeline",
    "type": "object",
    "required": ["name"],
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string",
            "description": "Unique listener name. Becomes part of the XML root tag.",
            "pattern": "^[a-zA-Z][a-zA-Z0-9_.-]*$",
            "examples": ["greeter", "calculator.add", "search.google"],
        },
        "description": {
            "type": "string",
            "description": "Human-readable description. Required for tool prompt generation - leads auto-generated tool descriptions.",
            "examples": ["Greets users warmly", "Adds two integers and returns their sum"],
        },
        "agent": {
            "type": "boolean",
            "description": "Mark as LLM-powered agent. Agents get unique root tags (enabling blind self-iteration) and receive own_name in metadata.",
            "default": False,
        },
        "tool": {
            "type": "boolean",
            "description": "Mark as simple tool/function. Tools are stateless handlers that process requests and return results.",
            "default": False,
        },
        "gateway": {
            "type": "boolean",
            "description": "Mark as federation gateway. Gateways forward messages to remote organisms.",
            "default": False,
        },
        "broadcast": {
            "type": "boolean",
            "description": "Allow sharing root tag with other listeners. Enables parallel handling of the same message type.",
            "default": False,
        },
        "handler": {
            "type": "string",
            "description": "Python import path to the async handler function.",
            "pattern": "^[a-zA-Z_][a-zA-Z0-9_.]*$",
            "examples": [
                "handlers.hello.handle_greeting",
                "xml_pipeline.tools.calculate.calculate_handler",
            ],
        },
        "payload_class": {
            "type": "string",
            "description": "Python import path to the @xmlify dataclass that defines the message schema.",
            "pattern": "^[a-zA-Z_][a-zA-Z0-9_.]*$",
            "examples": [
                "handlers.hello.Greeting",
                "xml_pipeline.tools.calculate.CalculatePayload",
            ],
        },
        "prompt": {
            "type": "string",
            "description": "System prompt for LLM agents. Injected as the first system message. Can use YAML multiline syntax.",
            "examples": [
                "You are a friendly greeter. Keep responses short and enthusiastic.",
            ],
        },
        "model": {
            "type": "string",
            "description": "LLM model to use for this agent. Overrides the default model from LLM router.",
            "examples": ["grok-4.1", "claude-sonnet-4", "gpt-4o", "llama3"],
        },
        "peers": {
            "type": "array",
            "description": "List of listener names this listener can send messages to. Enforced by the message pump.",
            "items": {
                "type": "string",
                "pattern": "^[a-zA-Z][a-zA-Z0-9_.-]*$",
            },
            "uniqueItems": True,
            "examples": [["shouter", "logger"], ["calculator.add", "calculator.multiply"]],
        },
        "allowed_tools": {
            "type": "array",
            "description": "Explicitly allowed native tools. If set, only these tools are available.",
            "items": {
                "type": "string",
                "enum": [
                    "calculate",
                    "fetch",
                    "files",
                    "shell",
                    "search",
                    "keyvalue",
                    "convert",
                    "librarian",
                ],
            },
            "uniqueItems": True,
        },
        "blocked_tools": {
            "type": "array",
            "description": "Explicitly blocked native tools. These tools are never available.",
            "items": {
                "type": "string",
                "enum": [
                    "calculate",
                    "fetch",
                    "files",
                    "shell",
                    "search",
                    "keyvalue",
                    "convert",
                    "librarian",
                ],
            },
            "uniqueItems": True,
        },
        "temperature": {
            "type": "number",
            "description": "LLM temperature setting. Higher = more creative, lower = more focused.",
            "default": 0.7,
            "minimum": 0.0,
            "maximum": 2.0,
        },
        "max_tokens": {
            "type": "integer",
            "description": "Maximum tokens in LLM response.",
            "default": 4096,
            "minimum": 1,
        },
        "verbose": {
            "type": "boolean",
            "description": "Enable verbose logging for this listener.",
            "default": False,
        },
        "confirm_actions": {
            "type": "boolean",
            "description": "Require confirmation before tool calls.",
            "default": False,
        },
        "metadata": {
            "type": "object",
            "description": "Custom metadata available to the handler via metadata.custom.",
            "additionalProperties": True,
        },
    },
    "if": {
        "properties": {
            "agent": {"const": True}
        }
    },
    "then": {
        "required": ["prompt"],
        "properties": {
            "description": {
                "description": "Description is recommended for agents to improve tool generation."
            }
        }
    },
    "examples": [
        {
            "name": "greeter",
            "description": "Greeting agent",
            "agent": True,
            "handler": "handlers.hello.handle_greeting",
            "payload_class": "handlers.hello.Greeting",
            "prompt": "You are a friendly greeter. Respond warmly and briefly.",
            "peers": ["shouter"],
        },
        {
            "name": "calculator.add",
            "description": "Adds two integers and returns their sum",
            "tool": True,
            "handler": "handlers.calculator.add_handler",
            "payload_class": "handlers.calculator.AddPayload",
        },
    ],
}
