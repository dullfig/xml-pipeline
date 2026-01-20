"""
Optional feature detection for xml-pipeline.

This module checks which optional dependencies are installed and provides
graceful degradation when features are unavailable.
"""

from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import Callable


def _check_import(module: str) -> bool:
    """Check if a module can be imported."""
    return find_spec(module) is not None


# Feature registry: feature_name -> (check_function, description)
# Note: auth, server, lsp moved to Nextra (proprietary)
FEATURES: dict[str, tuple[Callable[[], bool], str]] = {
    "anthropic": (lambda: _check_import("anthropic"), "Anthropic Claude SDK"),
    "openai": (lambda: _check_import("openai"), "OpenAI SDK"),
    "redis": (lambda: _check_import("redis"), "Redis for distributed keyvalue"),
    "search": (lambda: _check_import("duckduckgo_search"), "DuckDuckGo search"),
    "console": (lambda: _check_import("prompt_toolkit"), "Interactive console example"),
}


def get_available_features() -> dict[str, bool]:
    """Return dict of feature_name -> is_available."""
    return {name: check() for name, (check, _) in FEATURES.items()}


def is_feature_available(feature: str) -> bool:
    """Check if a specific feature is available."""
    if feature not in FEATURES:
        return False
    check, _ = FEATURES[feature]
    return check()


def require_feature(feature: str) -> None:
    """Raise ImportError if feature is not available."""
    if not is_feature_available(feature):
        _, description = FEATURES.get(feature, (None, feature))
        raise ImportError(
            f"Feature '{feature}' is not installed. "
            f"Install with: pip install xml-pipeline[{feature}]"
        )


@dataclass
class FeatureCheck:
    """Result of checking features against a config."""

    available: dict[str, bool] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)  # feature -> reason needed


def check_features(config) -> FeatureCheck:
    """
    Check which optional features are needed for a config.

    Returns FeatureCheck with available features and missing ones needed by config.
    """
    result = FeatureCheck(available=get_available_features())

    # Check LLM backends
    for backend in getattr(config, "llm_backends", []):
        provider = getattr(backend, "provider", "").lower()
        if provider == "anthropic" and not result.available.get("anthropic"):
            result.missing["anthropic"] = f"LLM backend '{backend.name}' uses Anthropic"
        if provider == "openai" and not result.available.get("openai"):
            result.missing["openai"] = f"LLM backend '{backend.name}' uses OpenAI"

    # Check tools
    for listener in getattr(config, "listeners", []):
        # If listener uses keyvalue tool and redis is configured
        # This would need more sophisticated detection based on tool config
        pass

    # Note: auth/server config sections are read but implemented in Nextra

    return result


# Lazy import helpers for optional dependencies
def get_redis_client():
    """Get Redis client, or raise helpful error."""
    require_feature("redis")
    import redis
    return redis


def get_anthropic_client():
    """Get Anthropic client, or raise helpful error."""
    require_feature("anthropic")
    import anthropic
    return anthropic


def get_openai_client():
    """Get OpenAI client, or raise helpful error."""
    require_feature("openai")
    import openai
    return openai
