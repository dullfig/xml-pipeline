"""
compiler.py — WASI-sandboxed compilation via wasmtime.

Runs asc.wasm (AssemblyScript compiler) inside a WASI sandbox with
filesystem preopens limited to a temporary workspace. No environment
variables, no network, no host access beyond the preopened directories.

This is deliberately separate from the existing WASM tool infrastructure
(loader.py, dispatcher.py) which uses pure-compute sandboxes with
capability-gated host functions. The compiler needs WASI for filesystem
access; regular tools should never get WASI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Guarded import — wasmtime is optional
try:
    import wasmtime
    WASMTIME_AVAILABLE = True
except ImportError:
    WASMTIME_AVAILABLE = False


@dataclass
class CompilerConfig:
    """Configuration for the WASI compiler."""
    asc_wasm_path: str           # Path to asc.wasm
    stdlib_path: str = ""        # Path to AS standard library
    timeout_seconds: float = 60.0
    memory_limit_mb: int = 256   # Higher than regular tools — compilation is heavy


@dataclass
class CompileResult:
    """Result of a WASI compilation."""
    success: bool
    wasm_bytes: bytes = b""
    stdout: str = ""
    stderr: str = ""
    error: str = ""


class WasiCompiler:
    """
    Runs the AssemblyScript compiler (asc.wasm) inside a WASI sandbox.

    All filesystem access is restricted to a temporary workspace directory
    via WASI preopens. No env vars, no network, no host functions.
    """

    def __init__(self, config: CompilerConfig) -> None:
        if not WASMTIME_AVAILABLE:
            raise RuntimeError(
                "wasmtime is required for WASI compilation. "
                "Install with: pip install xml-pipeline[wasm]"
            )
        self._config = config
        self._asc_path = Path(config.asc_wasm_path)
        if not self._asc_path.exists():
            raise FileNotFoundError(
                f"asc.wasm not found: {self._asc_path}"
            )

        # Pre-compile the engine and module (reusable across compilations)
        engine_config = wasmtime.Config()
        engine_config.epoch_interruption = True
        self._engine = wasmtime.Engine(engine_config)
        self._module = wasmtime.Module.from_file(
            self._engine, str(self._asc_path)
        )
        logger.info("WasiCompiler initialized: %s", self._asc_path)

    @property
    def config(self) -> CompilerConfig:
        return self._config

    def compile(self, source_files: Dict[str, str]) -> CompileResult:
        """
        Compile AssemblyScript source files to WASM.

        This is a blocking method — call via asyncio.to_thread().

        Args:
            source_files: Mapping of filename -> content.
                          Must contain at least "main.ts".

        Returns:
            CompileResult with success/failure status and output.
        """
        if "main.ts" not in source_files:
            return CompileResult(
                success=False,
                error="source_files must contain 'main.ts'",
            )

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                workspace = Path(tmpdir) / "workspace"
                workspace.mkdir()

                # Write source files
                for name, content in source_files.items():
                    (workspace / name).write_text(content, encoding="utf-8")

                # Capture files for stdout/stderr
                stdout_path = Path(tmpdir) / "stdout.txt"
                stderr_path = Path(tmpdir) / "stderr.txt"
                stdout_path.touch()
                stderr_path.touch()

                # Configure WASI
                wasi_config = wasmtime.WasiConfig()
                wasi_config.argv = [
                    "asc",
                    "/workspace/main.ts",
                    "-o", "/workspace/output.wasm",
                    "--optimize",
                ]
                wasi_config.preopen_dir(str(workspace), "/workspace")
                if self._config.stdlib_path:
                    stdlib = Path(self._config.stdlib_path)
                    if stdlib.exists():
                        wasi_config.preopen_dir(str(stdlib), "/lib")
                wasi_config.stdout_file(str(stdout_path))
                wasi_config.stderr_file(str(stderr_path))

                # Create store with limits
                store = wasmtime.Store(self._engine)
                store.set_wasi(wasi_config)
                store.set_epoch_deadline(1)
                limit_bytes = self._config.memory_limit_mb * 1024 * 1024
                store.set_limits(memory_size=limit_bytes)

                # Link WASI and instantiate
                linker = wasmtime.Linker(self._engine)
                linker.define_wasi()
                instance = linker.instantiate(store, self._module)

                # Call _start (WASI entry point)
                start_fn = instance.exports(store).get("_start")
                if start_fn is None:
                    return CompileResult(
                        success=False,
                        error="asc.wasm missing _start export",
                    )

                try:
                    start_fn(store)
                except Exception as exc:
                    # Compilation may fail with a trap — that's the compiler
                    # exiting with non-zero status
                    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
                    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
                    return CompileResult(
                        success=False,
                        stdout=stdout_text,
                        stderr=stderr_text,
                        error=str(exc),
                    )

                # Read outputs
                stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
                stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")

                # Read compiled output
                output_path = workspace / "output.wasm"
                if output_path.exists():
                    wasm_bytes = output_path.read_bytes()
                    return CompileResult(
                        success=True,
                        wasm_bytes=wasm_bytes,
                        stdout=stdout_text,
                        stderr=stderr_text,
                    )
                else:
                    return CompileResult(
                        success=False,
                        stdout=stdout_text,
                        stderr=stderr_text,
                        error="Compiler did not produce output.wasm",
                    )

        except Exception as exc:
            return CompileResult(
                success=False,
                error=f"Compilation error: {exc}",
            )

    def interrupt(self) -> None:
        """Bump engine epoch to interrupt in-flight compilation."""
        self._engine.increment_epoch()


# ============================================================================
# Singleton
# ============================================================================

_compiler: Optional[WasiCompiler] = None


def get_compiler() -> WasiCompiler:
    """Get the global WASI compiler singleton."""
    if _compiler is None:
        raise RuntimeError(
            "WASI compiler not configured. Call configure_compiler() first "
            "or add a 'compiler:' section to organism.yaml."
        )
    return _compiler


def configure_compiler(config: CompilerConfig) -> WasiCompiler:
    """Configure and set the global WASI compiler singleton."""
    global _compiler
    _compiler = WasiCompiler(config)
    return _compiler


def reset_compiler() -> None:
    """Reset the global compiler singleton (for tests)."""
    global _compiler
    _compiler = None
