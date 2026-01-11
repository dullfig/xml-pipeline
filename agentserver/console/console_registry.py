"""
console_registry.py — Global console reference for handlers.

This module provides a central place to register and retrieve
the active console instance, avoiding Python module import issues.
"""

_console = None


def set_console(console):
    """Set the active console instance."""
    global _console
    _console = console


def get_console():
    """Get the active console instance (or None)."""
    return _console
