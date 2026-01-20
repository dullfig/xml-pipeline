"""
LSP Server lifecycle manager.

Manages language server instances that can be shared across
multiple editor sessions. Supports multiple language servers:
- yaml-language-server (for config files)
- asls (for AssemblyScript listener source)
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Optional, Union

from .client import YAMLLSPClient, is_lsp_available
from .asls_client import ASLSClient, ASLSConfig, is_asls_available

logger = logging.getLogger(__name__)


class LSPServerType(Enum):
    """Supported language server types."""
    YAML = "yaml"
    ASSEMBLYSCRIPT = "assemblyscript"


# Type alias for any LSP client
LSPClient = Union[YAMLLSPClient, ASLSClient]


class LSPServerManager:
    """
    Manages the lifecycle of LSP servers.

    Provides singleton client instances that start on first use
    and stop when explicitly requested or when the process exits.

    Supports multiple language servers running concurrently.
    """

    def __init__(self):
        self._clients: dict[LSPServerType, LSPClient] = {}
        self._ref_counts: dict[LSPServerType, int] = {}
        self._lock = asyncio.Lock()

    def is_running(self, server_type: LSPServerType = LSPServerType.YAML) -> bool:
        """Check if a specific LSP server is running."""
        client = self._clients.get(server_type)
        return client is not None and client._initialized

    async def get_client(
        self,
        server_type: LSPServerType = LSPServerType.YAML,
        asls_config: Optional[ASLSConfig] = None,
    ) -> Optional[LSPClient]:
        """
        Get an LSP client, starting the server if needed.

        Args:
            server_type: Which language server to get
            asls_config: Configuration for ASLS (only used if server_type is ASSEMBLYSCRIPT)

        Returns None if the requested LSP is not available.
        """
        async with self._lock:
            # Check if already running
            if server_type in self._clients:
                client = self._clients[server_type]
                if client._initialized:
                    self._ref_counts[server_type] = self._ref_counts.get(server_type, 0) + 1
                    return client

            # Start the appropriate server
            if server_type == LSPServerType.YAML:
                return await self._start_yaml_server()
            elif server_type == LSPServerType.ASSEMBLYSCRIPT:
                return await self._start_asls_server(asls_config)
            else:
                logger.error(f"Unknown LSP server type: {server_type}")
                return None

    async def _start_yaml_server(self) -> Optional[YAMLLSPClient]:
        """Start the YAML language server."""
        available, reason = is_lsp_available()
        if not available:
            logger.info(f"YAML LSP not available: {reason}")
            return None

        client = YAMLLSPClient()
        success = await client.start()

        if success:
            self._clients[LSPServerType.YAML] = client
            self._ref_counts[LSPServerType.YAML] = 1
            logger.info("yaml-language-server started")
            return client
        else:
            return None

    async def _start_asls_server(
        self, config: Optional[ASLSConfig] = None
    ) -> Optional[ASLSClient]:
        """Start the AssemblyScript language server."""
        available, reason = is_asls_available()
        if not available:
            logger.info(f"ASLS not available: {reason}")
            return None

        client = ASLSClient(config=config)
        success = await client.start()

        if success:
            self._clients[LSPServerType.ASSEMBLYSCRIPT] = client
            self._ref_counts[LSPServerType.ASSEMBLYSCRIPT] = 1
            logger.info("AssemblyScript language server started")
            return client
        else:
            return None

    async def release_client(
        self, server_type: LSPServerType = LSPServerType.YAML
    ) -> None:
        """
        Release a reference to a client.

        Stops the server when the last reference is released.
        """
        async with self._lock:
            if server_type not in self._ref_counts:
                return

            self._ref_counts[server_type] -= 1

            if self._ref_counts[server_type] <= 0:
                client = self._clients.pop(server_type, None)
                self._ref_counts.pop(server_type, None)

                if client is not None:
                    await client.stop()
                    logger.info(f"{server_type.value} language server stopped")

    async def stop(self, server_type: Optional[LSPServerType] = None) -> None:
        """
        Force stop LSP server(s).

        Args:
            server_type: Specific server to stop, or None to stop all
        """
        async with self._lock:
            if server_type is not None:
                # Stop specific server
                client = self._clients.pop(server_type, None)
                self._ref_counts.pop(server_type, None)
                if client is not None:
                    await client.stop()
                    logger.info(f"{server_type.value} language server stopped (forced)")
            else:
                # Stop all servers
                for st, client in list(self._clients.items()):
                    await client.stop()
                    logger.info(f"{st.value} language server stopped (forced)")
                self._clients.clear()
                self._ref_counts.clear()

    async def stop_all(self) -> None:
        """Force stop all LSP servers."""
        await self.stop(None)

    # Convenience methods for YAML (backwards compatible)

    async def get_yaml_client(self) -> Optional[YAMLLSPClient]:
        """Get YAML LSP client (convenience method)."""
        client = await self.get_client(LSPServerType.YAML)
        return client if isinstance(client, YAMLLSPClient) else None

    async def get_asls_client(
        self, config: Optional[ASLSConfig] = None
    ) -> Optional[ASLSClient]:
        """Get AssemblyScript LSP client (convenience method)."""
        client = await self.get_client(LSPServerType.ASSEMBLYSCRIPT, asls_config=config)
        return client if isinstance(client, ASLSClient) else None

    # Context manager for YAML (backwards compatible)

    async def __aenter__(self) -> Optional[YAMLLSPClient]:
        """Context manager entry - get YAML client."""
        return await self.get_yaml_client()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - release YAML client."""
        await self.release_client(LSPServerType.YAML)


# Global singleton
_manager: Optional[LSPServerManager] = None


def get_lsp_manager() -> LSPServerManager:
    """Get the global LSP server manager."""
    global _manager
    if _manager is None:
        _manager = LSPServerManager()
    return _manager


async def ensure_lsp_stopped() -> None:
    """Ensure all LSP servers are stopped. Call on application shutdown."""
    if _manager is not None:
        await _manager.stop_all()
