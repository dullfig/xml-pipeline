"""
HTTP/WebSocket server for xml-pipeline.

Provides:
- REST API for auth and management
- WebSocket for console and GUI clients
"""

from .app import create_app, run_server

__all__ = ["create_app", "run_server"]
