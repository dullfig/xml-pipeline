"""
console — Secure console interface for organism operators.

Provides password-protected access to privileged operations
via local keyboard input only (no network exposure).
"""

from agentserver.console.secure_console import SecureConsole, PasswordManager

__all__ = ["SecureConsole", "PasswordManager"]
