"""
test_wasi_compiler.py — Unit tests for the WASI compiler infrastructure.

Tests cover:
- CompilerConfig defaults and custom values
- CompileResult success/failure states
- Singleton lifecycle (get/configure/reset)
- Error handling for missing wasmtime and missing asc.wasm
- WasiCompiler.compile() missing main.ts
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from xml_pipeline.wasm.compiler import (
    CompilerConfig,
    CompileResult,
    WasiCompiler,
    get_compiler,
    configure_compiler,
    reset_compiler,
    WASMTIME_AVAILABLE,
)


# ============================================================================
# CompilerConfig
# ============================================================================


class TestCompilerConfig:

    def test_defaults(self):
        cfg = CompilerConfig(asc_wasm_path="/path/to/asc.wasm")
        assert cfg.asc_wasm_path == "/path/to/asc.wasm"
        assert cfg.stdlib_path == ""
        assert cfg.timeout_seconds == 60.0
        assert cfg.memory_limit_mb == 256

    def test_custom_values(self):
        cfg = CompilerConfig(
            asc_wasm_path="/custom/asc.wasm",
            stdlib_path="/custom/std",
            timeout_seconds=120.0,
            memory_limit_mb=512,
        )
        assert cfg.asc_wasm_path == "/custom/asc.wasm"
        assert cfg.stdlib_path == "/custom/std"
        assert cfg.timeout_seconds == 120.0
        assert cfg.memory_limit_mb == 512


# ============================================================================
# CompileResult
# ============================================================================


class TestCompileResult:

    def test_success_result(self):
        r = CompileResult(success=True, wasm_bytes=b"\x00wasm", stdout="OK")
        assert r.success is True
        assert r.wasm_bytes == b"\x00wasm"
        assert r.stdout == "OK"
        assert r.stderr == ""
        assert r.error == ""

    def test_failure_result(self):
        r = CompileResult(success=False, error="syntax error on line 3")
        assert r.success is False
        assert r.wasm_bytes == b""
        assert r.error == "syntax error on line 3"

    def test_defaults(self):
        r = CompileResult(success=False)
        assert r.wasm_bytes == b""
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.error == ""


# ============================================================================
# Singleton lifecycle
# ============================================================================


class TestSingleton:

    def setup_method(self):
        reset_compiler()

    def teardown_method(self):
        reset_compiler()

    def test_get_without_configure_raises(self):
        with pytest.raises(RuntimeError, match="not configured"):
            get_compiler()

    def test_reset_allows_reconfigure(self):
        """After reset, get_compiler raises again."""
        reset_compiler()
        with pytest.raises(RuntimeError, match="not configured"):
            get_compiler()

    @patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", False)
    def test_configure_without_wasmtime_raises(self):
        cfg = CompilerConfig(asc_wasm_path="/fake/asc.wasm")
        with pytest.raises(RuntimeError, match="wasmtime is required"):
            configure_compiler(cfg)

    @patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", True)
    def test_configure_missing_asc_raises(self):
        """If asc_wasm_path doesn't exist, FileNotFoundError is raised."""
        cfg = CompilerConfig(asc_wasm_path="/nonexistent/asc.wasm")
        with pytest.raises(FileNotFoundError, match="asc.wasm not found"):
            configure_compiler(cfg)


# ============================================================================
# WasiCompiler (mocked wasmtime)
# ============================================================================


class TestWasiCompilerInit:

    @patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", False)
    def test_init_without_wasmtime(self):
        cfg = CompilerConfig(asc_wasm_path="/fake/asc.wasm")
        with pytest.raises(RuntimeError, match="wasmtime is required"):
            WasiCompiler(cfg)

    @patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", True)
    def test_init_missing_file(self):
        cfg = CompilerConfig(asc_wasm_path="/definitely/not/here/asc.wasm")
        with pytest.raises(FileNotFoundError):
            WasiCompiler(cfg)


class TestCompileMethod:

    def test_missing_main_ts(self):
        """compile() with no main.ts returns error result."""
        # We need a real-enough compiler to test the method, so mock internals
        with patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", True):
            compiler = MagicMock(spec=WasiCompiler)
            compiler.compile = WasiCompiler.compile.__get__(compiler)
            compiler._config = CompilerConfig(asc_wasm_path="/fake/asc.wasm")

            result = compiler.compile({"helper.ts": "export function foo(): void {}"})
            assert result.success is False
            assert "main.ts" in result.error

    def test_compile_result_with_main_ts_but_exception(self):
        """compile() catches exceptions and returns error result."""
        with patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", True):
            compiler = MagicMock(spec=WasiCompiler)
            compiler.compile = WasiCompiler.compile.__get__(compiler)
            compiler._config = CompilerConfig(asc_wasm_path="/fake/asc.wasm")
            compiler._engine = MagicMock()
            compiler._module = MagicMock()

            # The tmpdir operations will work, but wasmtime mocks will fail
            # when trying to use WasiConfig etc., so we expect an error result
            result = compiler.compile({"main.ts": "export function foo(): void {}"})
            assert result.success is False
            assert result.error != ""


class TestInterrupt:

    def test_interrupt_bumps_epoch(self):
        """interrupt() calls engine.increment_epoch()."""
        with patch("xml_pipeline.wasm.compiler.WASMTIME_AVAILABLE", True):
            compiler = MagicMock(spec=WasiCompiler)
            compiler.interrupt = WasiCompiler.interrupt.__get__(compiler)
            compiler._engine = MagicMock()

            compiler.interrupt()
            compiler._engine.increment_epoch.assert_called_once()
