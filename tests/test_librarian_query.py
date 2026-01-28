"""
Integration tests for Premium Librarian query system.

These tests require:
- eXist-db running (for storage)
- LLM router configured (for synthesis)

Mark with @pytest.mark.integration to skip in CI without dependencies.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from xml_pipeline.librarian.query import (
    QueryResult,
    Source,
    RetrievedChunk,
    _build_rag_prompt,
    format_sources_xml,
)
from xml_pipeline.librarian.index import LibraryIndex


class TestBuildRagPrompt:
    """Tests for RAG prompt construction."""

    def test_builds_prompt_with_context(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="test:foo:abc123",
                file_path="src/utils.py",
                name="calculate",
                chunk_type="function",
                language="python",
                start_line=10,
                end_line=20,
                content="def calculate(x): return x * 2",
                docstring="Calculate double.",
                signature="def calculate(x) -> int",
                score=0.9,
            ),
            RetrievedChunk(
                chunk_id="test:bar:def456",
                file_path="src/main.py",
                name="main",
                chunk_type="function",
                language="python",
                start_line=1,
                end_line=5,
                content="def main(): print('hello')",
                docstring="",
                signature="def main()",
                score=0.7,
            ),
        ]

        prompt = _build_rag_prompt(
            question="How does the calculate function work?",
            chunks=chunks,
            library_name="test-lib",
        )

        # Verify prompt structure
        assert "test-lib" in prompt
        assert "calculate function" in prompt
        assert "src/utils.py" in prompt
        assert "src/main.py" in prompt
        assert "[1]" in prompt
        assert "[2]" in prompt
        assert "```python" in prompt

    def test_truncates_long_content(self) -> None:
        long_content = "x" * 3000  # Longer than 2000 char limit
        chunks = [
            RetrievedChunk(
                chunk_id="test:long:123",
                file_path="long.py",
                name="long_func",
                chunk_type="function",
                language="python",
                start_line=1,
                end_line=100,
                content=long_content,
                docstring="",
                signature="",
                score=0.5,
            ),
        ]

        prompt = _build_rag_prompt("What?", chunks, "lib")

        # Content should be truncated
        assert "(truncated)" in prompt
        # Should not contain full content
        assert long_content not in prompt

    def test_empty_chunks_list(self) -> None:
        prompt = _build_rag_prompt("What?", [], "lib")
        assert "lib" in prompt
        assert "Question" in prompt


class TestFormatSourcesXml:
    """Tests for XML source formatting."""

    def test_formats_sources_as_xml(self) -> None:
        sources = [
            Source(
                file_path="src/app.py",
                name="process",
                chunk_type="function",
                start_line=10,
                end_line=25,
                relevance_score=0.95,
                snippet="def process(data): ...",
            ),
        ]

        xml = format_sources_xml(sources)

        assert "<sources>" in xml
        assert "</sources>" in xml
        assert "<source index=\"1\">" in xml
        assert "<file-path>src/app.py</file-path>" in xml
        assert "<name>process</name>" in xml
        assert "<type>function</type>" in xml
        assert "<lines>10-25</lines>" in xml
        assert "<score>0.95</score>" in xml

    def test_escapes_special_characters(self) -> None:
        sources = [
            Source(
                file_path="src/<special>.py",
                name="func&name",
                chunk_type="function",
                start_line=1,
                end_line=1,
                relevance_score=0.5,
                snippet="code with <tags> & entities",
            ),
        ]

        xml = format_sources_xml(sources)

        # XML entities should be escaped
        assert "&lt;special&gt;" in xml
        assert "func&amp;name" in xml

    def test_empty_sources_list(self) -> None:
        xml = format_sources_xml([])

        assert "<sources>" in xml
        assert "</sources>" in xml


class TestQueryResultDataclass:
    """Tests for QueryResult dataclass."""

    def test_default_values(self) -> None:
        result = QueryResult(answer="Test answer")

        assert result.answer == "Test answer"
        assert result.sources == []
        assert result.tokens_used == 0
        assert result.chunks_examined == 0
        assert result.error == ""

    def test_with_sources(self) -> None:
        sources = [
            Source(
                file_path="test.py",
                name="test",
                chunk_type="function",
                start_line=1,
                end_line=10,
                relevance_score=0.9,
            ),
        ]

        result = QueryResult(
            answer="Test answer",
            sources=sources,
            tokens_used=100,
            chunks_examined=5,
        )

        assert len(result.sources) == 1
        assert result.tokens_used == 100
        assert result.chunks_examined == 5


class TestRetrievedChunk:
    """Tests for RetrievedChunk dataclass."""

    def test_all_fields(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="lib:file:hash",
            file_path="src/module.py",
            name="my_function",
            chunk_type="function",
            language="python",
            start_line=10,
            end_line=20,
            content="def my_function(): pass",
            docstring="Does something.",
            signature="def my_function() -> None",
            score=0.85,
        )

        assert chunk.chunk_id == "lib:file:hash"
        assert chunk.file_path == "src/module.py"
        assert chunk.name == "my_function"
        assert chunk.language == "python"
        assert chunk.score == 0.85


@pytest.mark.integration
class TestQueryLibraryIntegration:
    """Integration tests requiring eXist-db and LLM."""

    async def test_query_nonexistent_library(self) -> None:
        """Query should return error for non-existent library."""
        from xml_pipeline.librarian.query import query_library

        # Mock get_index to return None - patch at index module level
        with patch("xml_pipeline.librarian.index.get_index", new_callable=AsyncMock) as mock_get_index:
            mock_get_index.return_value = None

            result = await query_library(
                library_id="nonexistent-lib-xyz",
                question="What does this do?",
            )

            assert result.error
            assert "not found" in result.error.lower()

    async def test_query_with_no_relevant_chunks(self) -> None:
        """Query should handle case where search returns no results."""
        from xml_pipeline.librarian.query import query_library

        mock_index = LibraryIndex(
            library_id="test-lib",
            name="Test Library",
            source_url="https://example.com/repo",
            created_at="2024-01-01T00:00:00Z",
        )

        # Patch get_index at the index module level (where it's defined)
        # and _search_chunks at query module level
        with patch("xml_pipeline.librarian.index.get_index", new_callable=AsyncMock) as mock_get_index:
            mock_get_index.return_value = mock_index

            with patch("xml_pipeline.librarian.query._search_chunks", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = []

                result = await query_library(
                    library_id="test-lib",
                    question="What does foo do?",
                )

                assert "No relevant code found" in result.answer
                assert result.chunks_examined == 0


class TestLibraryIndex:
    """Tests for LibraryIndex dataclass."""

    def test_properties(self) -> None:
        index = LibraryIndex(
            library_id="test-id",
            name="Test Lib",
            source_url="https://github.com/test/repo",
            created_at="2024-01-01",
            files=["a.py", "b.py", "c.py"],
            functions={"func1": "a.py", "func2": "b.py"},
            classes={"MyClass": "c.py"},
            stats={"chunks": 10, "files": 3},
        )

        assert index.total_chunks == 10
        assert index.total_files == 3

    def test_empty_stats(self) -> None:
        index = LibraryIndex(
            library_id="test",
            name="Test",
            source_url="",
            created_at="",
        )

        assert index.total_chunks == 0
        assert index.total_files == 0
