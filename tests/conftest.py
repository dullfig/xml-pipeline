"""
conftest.py — Shared pytest configuration and fixtures

This file is automatically loaded by pytest.
"""

import pytest
import sys
from pathlib import Path

# Ensure the project root is in the path for imports
# BUT don't import the root package (it has heavy deps)
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Tell pytest to ignore the root __init__.py
collect_ignore_glob = ["../__init__.py"]


# ============================================================================
# Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )


# ============================================================================
# Fixtures available to all tests
# ============================================================================

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset global singletons before each test to ensure isolation."""
    # Clear registries before test
    try:
        from xml_pipeline.platform.prompt_registry import get_prompt_registry
        get_prompt_registry().clear()
    except ImportError:
        pass

    try:
        from xml_pipeline.memory.context_buffer import get_context_buffer
        get_context_buffer().clear()
    except ImportError:
        pass

    try:
        from xml_pipeline.message_bus.thread_registry import get_registry
        get_registry().clear()
    except (ImportError, AttributeError):
        pass

    yield  # Run the test

    # Clear after test too for good measure
    try:
        from xml_pipeline.platform.prompt_registry import get_prompt_registry
        get_prompt_registry().clear()
    except ImportError:
        pass

    try:
        from xml_pipeline.memory.context_buffer import get_context_buffer
        get_context_buffer().clear()
    except ImportError:
        pass

    try:
        from xml_pipeline.message_bus.thread_registry import get_registry
        get_registry().clear()
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def sample_thread_id():
    """A valid UUID for testing."""
    return "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def sample_from_id():
    """A valid sender ID for testing."""
    return "calculator.add"
