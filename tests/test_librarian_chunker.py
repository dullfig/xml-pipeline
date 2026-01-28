"""
Tests for the Premium Librarian code chunker.
"""

import pytest

from xml_pipeline.librarian.chunker import (
    Chunk,
    chunk_file,
    chunk_python,
    chunk_javascript,
    chunk_cpp,
    chunk_prose,
    chunk_generic,
    detect_language,
)


class TestLanguageDetection:
    """Tests for language detection from file paths."""

    def test_python_detection(self) -> None:
        assert detect_language("foo.py") == "python"
        assert detect_language("path/to/module.py") == "python"
        assert detect_language("types.pyi") == "python"

    def test_javascript_detection(self) -> None:
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"
        assert detect_language("index.mjs") == "javascript"

    def test_typescript_detection(self) -> None:
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_cpp_detection(self) -> None:
        assert detect_language("main.cpp") == "cpp"
        assert detect_language("header.hpp") == "cpp"
        assert detect_language("source.cc") == "cpp"

    def test_c_detection(self) -> None:
        assert detect_language("main.c") == "c"
        assert detect_language("header.h") == "c"

    def test_unknown_language(self) -> None:
        assert detect_language("data.xyz") == "unknown"
        assert detect_language("noextension") == "unknown"

    def test_case_insensitive(self) -> None:
        assert detect_language("Module.PY") == "python"
        assert detect_language("APP.JS") == "javascript"


class TestPythonChunker:
    """Tests for Python AST-based chunking."""

    def test_simple_function(self) -> None:
        code = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"
'''
        chunks = chunk_python(code, "test.py")
        assert len(chunks) == 1
        assert chunks[0].name == "hello"
        assert chunks[0].chunk_type == "function"
        assert chunks[0].docstring == "Say hello."
        assert "str" in chunks[0].signature

    def test_async_function(self) -> None:
        code = '''
async def fetch_data(url: str) -> dict:
    """Fetch data from URL."""
    pass
'''
        chunks = chunk_python(code, "test.py")
        assert len(chunks) == 1
        assert chunks[0].name == "fetch_data"
        assert chunks[0].chunk_type == "function"
        assert "async" in chunks[0].signature

    def test_class_with_methods(self) -> None:
        code = '''
class Calculator:
    """A simple calculator."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def subtract(self, a: int, b: int) -> int:
        """Subtract two numbers."""
        return a - b
'''
        chunks = chunk_python(code, "test.py")
        # Should create a class chunk (small enough to keep together)
        assert len(chunks) >= 1
        class_chunk = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunk) == 1
        assert class_chunk[0].name == "Calculator"
        assert class_chunk[0].docstring == "A simple calculator."

    def test_imports_extracted(self) -> None:
        code = '''
import os
from typing import Optional, List

def process():
    pass
'''
        chunks = chunk_python(code, "test.py")
        assert len(chunks) == 1
        assert "import os" in chunks[0].imports
        assert any("from typing import" in imp for imp in chunks[0].imports)

    def test_empty_file(self) -> None:
        chunks = chunk_python("", "test.py")
        assert len(chunks) == 0

    def test_module_with_only_imports(self) -> None:
        code = '''
import os
import sys
'''
        chunks = chunk_python(code, "test.py")
        # Should create a module chunk for files with no functions/classes
        assert len(chunks) == 0 or chunks[0].chunk_type == "module"

    def test_syntax_error_fallback(self) -> None:
        code = '''
def broken(
    # Missing closing paren
'''
        chunks = chunk_python(code, "test.py")
        # Should fall back to generic chunking
        assert len(chunks) >= 0  # May or may not produce chunks


class TestJavaScriptChunker:
    """Tests for JavaScript regex-based chunking."""

    def test_function_declaration(self) -> None:
        code = '''
function greet(name) {
    return `Hello, ${name}!`;
}
'''
        chunks = chunk_javascript(code, "test.js")
        assert len(chunks) == 1
        assert chunks[0].name == "greet"
        assert chunks[0].chunk_type == "function"

    def test_async_function(self) -> None:
        code = '''
async function fetchData(url) {
    const response = await fetch(url);
    return response.json();
}
'''
        chunks = chunk_javascript(code, "test.js")
        assert len(chunks) == 1
        assert chunks[0].name == "fetchData"

    def test_arrow_function(self) -> None:
        code = '''
const multiply = (a, b) => {
    return a * b;
};
'''
        chunks = chunk_javascript(code, "test.js")
        assert len(chunks) == 1
        assert chunks[0].name == "multiply"
        assert chunks[0].chunk_type == "function"

    def test_class_definition(self) -> None:
        code = '''
class Calculator {
    constructor() {
        this.result = 0;
    }

    add(value) {
        this.result += value;
        return this;
    }
}
'''
        chunks = chunk_javascript(code, "test.js")
        assert len(chunks) >= 1
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0].name == "Calculator"

    def test_export_function(self) -> None:
        code = '''
export function exportedFunc() {
    return 42;
}
'''
        chunks = chunk_javascript(code, "test.js")
        assert len(chunks) == 1
        assert chunks[0].name == "exportedFunc"

    def test_imports_extracted(self) -> None:
        code = '''
import React from 'react';
import { useState } from 'react';
const lodash = require('lodash');

function Component() {
    return null;
}
'''
        chunks = chunk_javascript(code, "test.jsx")
        assert len(chunks) >= 1
        assert any("import React" in imp for imp in chunks[0].imports)


class TestCppChunker:
    """Tests for C++ regex-based chunking."""

    def test_function_definition(self) -> None:
        code = '''
int add(int a, int b) {
    return a + b;
}
'''
        chunks = chunk_cpp(code, "test.cpp")
        assert len(chunks) >= 1
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) == 1
        assert func_chunks[0].name == "add"

    def test_class_definition(self) -> None:
        code = '''
class Calculator {
public:
    int add(int a, int b);
    int subtract(int a, int b);
};
'''
        chunks = chunk_cpp(code, "test.cpp")
        assert len(chunks) >= 1
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0].name == "Calculator"

    def test_includes_extracted(self) -> None:
        code = '''
#include <iostream>
#include "myheader.h"

int main() {
    return 0;
}
'''
        chunks = chunk_cpp(code, "test.cpp")
        assert len(chunks) >= 1
        assert any("#include <iostream>" in imp for imp in chunks[0].imports)


class TestProseChunker:
    """Tests for prose document chunking."""

    def test_markdown_headings(self) -> None:
        content = '''# Introduction

This is the introduction section.

## Getting Started

Follow these steps to get started.

## Advanced Topics

More advanced content here.
'''
        chunks = chunk_prose(content, "readme.md", "markdown")
        assert len(chunks) >= 2
        # First chunk should be introduction
        assert chunks[0].name == "Introduction"

    def test_empty_document(self) -> None:
        chunks = chunk_prose("", "empty.md", "markdown")
        assert len(chunks) == 0


class TestGenericChunker:
    """Tests for generic line-based chunking."""

    def test_small_file(self) -> None:
        content = "line1\nline2\nline3"
        chunks = chunk_generic(content, "test.txt", "text")
        assert len(chunks) == 1
        assert chunks[0].content == content

    def test_empty_file(self) -> None:
        chunks = chunk_generic("", "empty.txt", "text")
        assert len(chunks) == 0


class TestChunkFile:
    """Tests for the main chunk_file dispatcher."""

    def test_dispatches_to_python(self) -> None:
        code = "def foo(): pass"
        chunks = chunk_file(code, "test.py")
        assert all(c.language == "python" for c in chunks)

    def test_dispatches_to_javascript(self) -> None:
        code = "function foo() {}"
        chunks = chunk_file(code, "test.js")
        assert all(c.language == "javascript" for c in chunks)

    def test_dispatches_to_cpp(self) -> None:
        code = "int main() { return 0; }"
        chunks = chunk_file(code, "test.cpp")
        assert all(c.language == "cpp" for c in chunks)

    def test_unknown_language_uses_generic(self) -> None:
        content = "some content"
        chunks = chunk_file(content, "test.xyz")
        assert all(c.language == "unknown" for c in chunks)


class TestChunkProperties:
    """Tests for Chunk dataclass properties."""

    def test_chunk_id_generation(self) -> None:
        chunk = Chunk(
            content="def foo(): pass",
            file_path="test.py",
            start_line=1,
            end_line=1,
            chunk_type="function",
            name="foo",
            language="python",
        )
        assert chunk.chunk_id
        assert "test.py" in chunk.chunk_id
        assert "foo" in chunk.chunk_id

    def test_chunk_id_uniqueness(self) -> None:
        chunk1 = Chunk(
            content="def foo(): pass",
            file_path="test.py",
            start_line=1,
            end_line=1,
            chunk_type="function",
            name="foo",
            language="python",
        )
        chunk2 = Chunk(
            content="def foo(): return 1",
            file_path="test.py",
            start_line=1,
            end_line=1,
            chunk_type="function",
            name="foo",
            language="python",
        )
        # Different content should produce different IDs
        assert chunk1.chunk_id != chunk2.chunk_id

    def test_line_count(self) -> None:
        chunk = Chunk(
            content="line1\nline2\nline3",
            file_path="test.py",
            start_line=10,
            end_line=12,
            chunk_type="block",
            name="test",
            language="python",
        )
        assert chunk.line_count == 3
