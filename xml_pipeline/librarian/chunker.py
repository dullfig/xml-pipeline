"""
chunker.py — AST-based code chunking for intelligent RAG retrieval.

Chunks source files into semantically meaningful units (functions, classes, modules)
preserving context like docstrings, signatures, and imports.

Supported languages:
- Python (ast.parse)
- JavaScript/TypeScript (regex-based)
- C++ (regex-based)
"""

from __future__ import annotations

import ast
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Chunk:
    """A semantically meaningful code chunk."""

    content: str
    file_path: str
    start_line: int
    end_line: int
    chunk_type: str  # "function", "class", "method", "module", "block"
    name: str  # Function/class name or file name for modules
    language: str
    imports: list[str] = field(default_factory=list)
    docstring: str = ""
    signature: str = ""  # Function signature for context
    parent_class: str = ""  # Class name if this is a method

    @property
    def chunk_id(self) -> str:
        """Generate unique ID for this chunk."""
        content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:12]
        return f"{self.file_path}:{self.name}:{content_hash}"

    @property
    def line_count(self) -> int:
        """Number of lines in this chunk."""
        return self.end_line - self.start_line + 1


# Language detection by file extension
LANGUAGE_MAP = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".txt": "text",
}

# Max lines per chunk before splitting
MAX_CHUNK_LINES = 500


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    suffix = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(suffix, "unknown")


def chunk_file(content: str, file_path: str) -> list[Chunk]:
    """
    Chunk a file based on detected language.

    Dispatches to language-specific chunker or falls back to
    line-based chunking for unknown languages.
    """
    language = detect_language(file_path)

    if language == "python":
        return chunk_python(content, file_path)
    elif language in ("javascript", "typescript"):
        return chunk_javascript(content, file_path)
    elif language in ("c", "cpp"):
        return chunk_cpp(content, file_path)
    elif language in ("markdown", "restructuredtext", "text"):
        return chunk_prose(content, file_path, language)
    else:
        # Generic line-based chunking
        return chunk_generic(content, file_path, language)


def chunk_python(content: str, file_path: str) -> list[Chunk]:
    """
    AST-based Python chunking.

    Extracts:
    - Module-level imports (as context)
    - Functions (with docstrings)
    - Classes (with methods)
    - Top-level code blocks
    """
    chunks: list[Chunk] = []
    lines = content.splitlines()

    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Fall back to generic chunking on parse error
        return chunk_generic(content, file_path, "python")

    # Extract imports for context
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names)
            imports.append(f"from {module} import {names}")

    # Process top-level definitions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            chunk = _extract_python_function(node, lines, file_path, imports)
            chunks.append(chunk)

        elif isinstance(node, ast.ClassDef):
            # Create chunk for class definition + methods
            class_chunks = _extract_python_class(node, lines, file_path, imports)
            chunks.extend(class_chunks)

    # If no chunks extracted, create a module chunk
    if not chunks and content.strip():
        chunks.append(
            Chunk(
                content=content,
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                chunk_type="module",
                name=Path(file_path).stem,
                language="python",
                imports=imports,
            )
        )

    return chunks


def _extract_python_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    file_path: str,
    imports: list[str],
    parent_class: str = "",
) -> Chunk:
    """Extract a Python function as a chunk."""
    start_line = node.lineno
    end_line = node.end_lineno or start_line

    # Get source lines (1-indexed)
    func_lines = lines[start_line - 1 : end_line]
    content = "\n".join(func_lines)

    # Extract docstring
    docstring = ast.get_docstring(node) or ""

    # Build signature
    args = []
    for arg in node.args.args:
        arg_str = arg.arg
        if arg.annotation:
            try:
                arg_str += f": {ast.unparse(arg.annotation)}"
            except Exception:
                pass
        args.append(arg_str)

    returns = ""
    if node.returns:
        try:
            returns = f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass

    async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    signature = f"{async_prefix}def {node.name}({', '.join(args)}){returns}"

    chunk_type = "method" if parent_class else "function"

    return Chunk(
        content=content,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        chunk_type=chunk_type,
        name=node.name,
        language="python",
        imports=imports,
        docstring=docstring,
        signature=signature,
        parent_class=parent_class,
    )


def _extract_python_class(
    node: ast.ClassDef,
    lines: list[str],
    file_path: str,
    imports: list[str],
) -> list[Chunk]:
    """Extract a Python class and its methods as chunks."""
    chunks: list[Chunk] = []

    start_line = node.lineno
    end_line = node.end_lineno or start_line

    # Get full class source
    class_lines = lines[start_line - 1 : end_line]
    class_content = "\n".join(class_lines)

    # Class docstring
    docstring = ast.get_docstring(node) or ""

    # Build class signature with bases
    bases = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            pass

    base_str = f"({', '.join(bases)})" if bases else ""
    signature = f"class {node.name}{base_str}"

    # If class is small enough, keep as single chunk
    if len(class_lines) <= MAX_CHUNK_LINES:
        chunks.append(
            Chunk(
                content=class_content,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                chunk_type="class",
                name=node.name,
                language="python",
                imports=imports,
                docstring=docstring,
                signature=signature,
            )
        )
    else:
        # Large class: chunk into class header + individual methods
        # First, create a class header chunk (up to first method or ~50 lines)
        header_end = start_line + min(50, len(class_lines) - 1)

        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                header_end = child.lineno - 1
                break

        header_lines = lines[start_line - 1 : header_end]
        chunks.append(
            Chunk(
                content="\n".join(header_lines),
                file_path=file_path,
                start_line=start_line,
                end_line=header_end,
                chunk_type="class",
                name=node.name,
                language="python",
                imports=imports,
                docstring=docstring,
                signature=signature,
            )
        )

        # Then extract each method
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                method_chunk = _extract_python_function(
                    child, lines, file_path, imports, parent_class=node.name
                )
                chunks.append(method_chunk)

    return chunks


def chunk_javascript(content: str, file_path: str) -> list[Chunk]:
    """
    Regex-based JavaScript/TypeScript chunking.

    Extracts:
    - Function declarations
    - Arrow functions assigned to const/let
    - Class definitions
    - Export statements
    """
    chunks: list[Chunk] = []
    lines = content.splitlines()
    language = detect_language(file_path)

    # Extract imports
    imports: list[str] = []
    import_pattern = re.compile(
        r'^(?:import\s+.*?from\s+[\'"].*?[\'"]|import\s+[\'"].*?[\'"]|'
        r'const\s+\w+\s*=\s*require\([\'"].*?[\'"]\))',
        re.MULTILINE,
    )
    for match in import_pattern.finditer(content):
        imports.append(match.group(0))

    # Function pattern: function name(...) or async function name(...)
    func_pattern = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)",
        re.MULTILINE,
    )

    # Arrow function pattern: const name = (...) => or const name = async (...) =>
    arrow_pattern = re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        re.MULTILINE,
    )

    # Class pattern
    class_pattern = re.compile(
        r"^(?:export\s+)?(?:default\s+)?class\s+(\w+)",
        re.MULTILINE,
    )

    # Find all definitions and their positions
    definitions: list[tuple[int, str, str, str]] = []  # (line, type, name, signature)

    for match in func_pattern.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        definitions.append((line_num, "function", match.group(1), match.group(0)))

    for match in arrow_pattern.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        definitions.append((line_num, "function", match.group(1), match.group(0)))

    for match in class_pattern.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        definitions.append((line_num, "class", match.group(1), match.group(0)))

    # Sort by line number
    definitions.sort(key=lambda x: x[0])

    # Create chunks
    for i, (start_line, chunk_type, name, signature) in enumerate(definitions):
        # End line is start of next definition - 1, or end of file
        if i + 1 < len(definitions):
            end_line = definitions[i + 1][0] - 1
        else:
            end_line = len(lines)

        # Trim trailing empty lines
        while end_line > start_line and not lines[end_line - 1].strip():
            end_line -= 1

        chunk_lines = lines[start_line - 1 : end_line]
        chunk_content = "\n".join(chunk_lines)

        # Extract JSDoc comment if present
        docstring = ""
        if start_line > 1:
            prev_line = lines[start_line - 2].strip()
            if prev_line.endswith("*/"):
                # Look back for JSDoc start
                doc_lines = []
                for j in range(start_line - 2, max(0, start_line - 20), -1):
                    doc_lines.insert(0, lines[j])
                    if "/**" in lines[j]:
                        break
                docstring = "\n".join(doc_lines)

        chunks.append(
            Chunk(
                content=chunk_content,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                chunk_type=chunk_type,
                name=name,
                language=language,
                imports=imports,
                docstring=docstring,
                signature=signature,
            )
        )

    # If no chunks, create module chunk
    if not chunks and content.strip():
        chunks.append(
            Chunk(
                content=content,
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                chunk_type="module",
                name=Path(file_path).stem,
                language=language,
                imports=imports,
            )
        )

    return chunks


def chunk_cpp(content: str, file_path: str) -> list[Chunk]:
    """
    Regex-based C/C++ chunking.

    Extracts:
    - Function definitions
    - Class definitions
    - Struct definitions
    """
    chunks: list[Chunk] = []
    lines = content.splitlines()
    language = detect_language(file_path)

    # Extract includes
    imports: list[str] = []
    include_pattern = re.compile(r'^#include\s+[<"].*?[>"]', re.MULTILINE)
    for match in include_pattern.finditer(content):
        imports.append(match.group(0))

    # Function pattern (simplified): return_type name(params) {
    # This is a simplified pattern that won't catch all cases
    func_pattern = re.compile(
        r"^(?:(?:static|inline|virtual|explicit|constexpr|template\s*<[^>]*>\s*)*"
        r"(?:\w+(?:::\w+)*\s+)+)"  # Return type
        r"(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?[{;]",
        re.MULTILINE,
    )

    # Class/struct pattern
    class_pattern = re.compile(
        r"^(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+(\w+)",
        re.MULTILINE,
    )

    definitions: list[tuple[int, str, str, str]] = []

    for match in func_pattern.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        name = match.group(1)
        # Skip common false positives
        if name not in ("if", "while", "for", "switch", "return"):
            definitions.append((line_num, "function", name, match.group(0).strip()))

    for match in class_pattern.finditer(content):
        line_num = content[: match.start()].count("\n") + 1
        definitions.append((line_num, "class", match.group(1), match.group(0)))

    definitions.sort(key=lambda x: x[0])

    # Create chunks (similar to JS)
    for i, (start_line, chunk_type, name, signature) in enumerate(definitions):
        if i + 1 < len(definitions):
            end_line = definitions[i + 1][0] - 1
        else:
            end_line = len(lines)

        while end_line > start_line and not lines[end_line - 1].strip():
            end_line -= 1

        # For functions, try to find matching brace
        if chunk_type == "function":
            brace_count = 0
            found_open = False
            for j in range(start_line - 1, min(end_line, len(lines))):
                for char in lines[j]:
                    if char == "{":
                        brace_count += 1
                        found_open = True
                    elif char == "}":
                        brace_count -= 1
                        if found_open and brace_count == 0:
                            end_line = j + 1
                            break
                if found_open and brace_count == 0:
                    break

        chunk_lines = lines[start_line - 1 : end_line]
        chunk_content = "\n".join(chunk_lines)

        # Extract Doxygen comment if present
        docstring = ""
        if start_line > 1:
            prev_line = lines[start_line - 2].strip()
            if prev_line.endswith("*/"):
                doc_lines = []
                for j in range(start_line - 2, max(0, start_line - 30), -1):
                    doc_lines.insert(0, lines[j])
                    if "/**" in lines[j] or "/*!" in lines[j]:
                        break
                docstring = "\n".join(doc_lines)

        chunks.append(
            Chunk(
                content=chunk_content,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                chunk_type=chunk_type,
                name=name,
                language=language,
                imports=imports,
                docstring=docstring,
                signature=signature,
            )
        )

    if not chunks and content.strip():
        chunks.append(
            Chunk(
                content=content,
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                chunk_type="module",
                name=Path(file_path).stem,
                language=language,
                imports=imports,
            )
        )

    return chunks


def chunk_prose(content: str, file_path: str, language: str) -> list[Chunk]:
    """
    Chunk prose documents (Markdown, RST, plain text).

    Splits on headings/sections, keeping chunks under MAX_CHUNK_LINES.
    """
    chunks: list[Chunk] = []
    lines = content.splitlines()

    # Markdown heading pattern
    if language == "markdown":
        heading_pattern = re.compile(r"^#{1,6}\s+(.+)$")
    else:
        heading_pattern = re.compile(r"^[=\-~]+$")  # RST underline headings

    current_chunk_lines: list[str] = []
    current_start = 1
    current_name = Path(file_path).stem

    for i, line in enumerate(lines, 1):
        match = heading_pattern.match(line)

        # New section or chunk too large
        if match or len(current_chunk_lines) >= MAX_CHUNK_LINES:
            # Save current chunk if non-empty
            if current_chunk_lines:
                chunks.append(
                    Chunk(
                        content="\n".join(current_chunk_lines),
                        file_path=file_path,
                        start_line=current_start,
                        end_line=i - 1,
                        chunk_type="section",
                        name=current_name,
                        language=language,
                    )
                )

            # Start new chunk
            current_chunk_lines = [line]
            current_start = i
            if match:
                current_name = match.group(1) if language == "markdown" else lines[i - 2] if i > 1 else current_name
        else:
            current_chunk_lines.append(line)

    # Save final chunk
    if current_chunk_lines:
        chunks.append(
            Chunk(
                content="\n".join(current_chunk_lines),
                file_path=file_path,
                start_line=current_start,
                end_line=len(lines),
                chunk_type="section",
                name=current_name,
                language=language,
            )
        )

    return chunks


def chunk_generic(content: str, file_path: str, language: str) -> list[Chunk]:
    """
    Generic line-based chunking for unknown languages.

    Splits content into MAX_CHUNK_LINES chunks, trying to break at empty lines.
    """
    chunks: list[Chunk] = []
    lines = content.splitlines()

    if not lines:
        return chunks

    current_chunk_lines: list[str] = []
    current_start = 1

    for i, line in enumerate(lines, 1):
        current_chunk_lines.append(line)

        # Check if we should split
        if len(current_chunk_lines) >= MAX_CHUNK_LINES:
            # Try to find a good break point (empty line in last 50 lines)
            break_at = len(current_chunk_lines)
            for j in range(len(current_chunk_lines) - 1, max(0, len(current_chunk_lines) - 50), -1):
                if not current_chunk_lines[j].strip():
                    break_at = j
                    break

            # Create chunk up to break point
            chunks.append(
                Chunk(
                    content="\n".join(current_chunk_lines[:break_at]),
                    file_path=file_path,
                    start_line=current_start,
                    end_line=current_start + break_at - 1,
                    chunk_type="block",
                    name=f"{Path(file_path).stem}:{current_start}",
                    language=language,
                )
            )

            # Keep remaining lines for next chunk
            current_chunk_lines = current_chunk_lines[break_at:]
            current_start = current_start + break_at

    # Save final chunk
    if current_chunk_lines:
        chunks.append(
            Chunk(
                content="\n".join(current_chunk_lines),
                file_path=file_path,
                start_line=current_start,
                end_line=len(lines),
                chunk_type="block",
                name=f"{Path(file_path).stem}:{current_start}",
                language=language,
            )
        )

    return chunks
