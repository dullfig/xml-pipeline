"""
LSP (Language Server Protocol) integration for the editor.

Provides:
- YAMLLSPClient: Wrapper for yaml-language-server communication
- ASLSClient: Wrapper for AssemblyScript language server communication
- LSPServerManager: Server lifecycle management
- LSPBridge: Integration with prompt_toolkit editor

Supported Language Servers:
- yaml-language-server: npm install -g yaml-language-server
- asls (AssemblyScript): npm install -g assemblyscript-lsp
"""

from __future__ import annotations

from .client import (
    YAMLLSPClient,
    LSPCompletion,
    LSPDiagnostic,
    LSPHover,
    is_lsp_available,
)
from .asls_client import (
    ASLSClient,
    ASLSConfig,
    is_asls_available,
    is_assemblyscript_file,
    ASSEMBLYSCRIPT_EXTENSIONS,
)
from .manager import (
    LSPServerManager,
    LSPServerType,
    get_lsp_manager,
    ensure_lsp_stopped,
)
from .bridge import (
    LSPCompleter,
    DiagnosticsProcessor,
)

__all__ = [
    # YAML Client
    "YAMLLSPClient",
    "LSPCompletion",
    "LSPDiagnostic",
    "LSPHover",
    "is_lsp_available",
    # AssemblyScript Client
    "ASLSClient",
    "ASLSConfig",
    "is_asls_available",
    "is_assemblyscript_file",
    "ASSEMBLYSCRIPT_EXTENSIONS",
    # Manager
    "LSPServerManager",
    "LSPServerType",
    "get_lsp_manager",
    "ensure_lsp_stopped",
    # Bridge
    "LSPCompleter",
    "DiagnosticsProcessor",
]
