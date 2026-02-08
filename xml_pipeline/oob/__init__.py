"""
oob — Out-of-band privileged channel for runtime organism control.

The OOB channel runs on a separate localhost-only WebSocket port and
handles privileged commands: listener management, message injection,
introspection, and shutdown. All requests require Ed25519 signatures
(unless in dev mode without identity configured).
"""

from xml_pipeline.oob.server import OOBServer

__all__ = ["OOBServer"]
