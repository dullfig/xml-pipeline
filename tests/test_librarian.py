"""
Tests for librarian tools (exist-db integration).

All tests mock aiohttp — no real HTTP calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xml_pipeline.tools.librarian import (
    ExistDBConfig,
    _check_config,
    _escape_xquery_string,
    _resolve_path,
    configure_librarian,
    librarian_get,
    librarian_query,
    librarian_search,
    librarian_store,
)
import xml_pipeline.tools.librarian as librarian_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_config():
    """Reset librarian config before each test."""
    librarian_mod._config = None
    yield
    librarian_mod._config = None


@pytest.fixture
def configured():
    """Configure librarian with default settings."""
    configure_librarian(
        url="http://localhost:8080/exist/rest",
        username="admin",
        password="secret",
        default_collection="/db/agents",
    )


def _mock_response(status: int = 200, text: str = ""):
    """Create a mock aiohttp response as async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response):
    """Create a mock aiohttp.ClientSession."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.put = MagicMock(return_value=response)
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _fake_aiohttp(session):
    """Create a fake aiohttp module with BasicAuth and ClientSession."""
    mock_aiohttp = MagicMock()
    mock_aiohttp.BasicAuth = MagicMock(return_value=MagicMock())
    mock_aiohttp.ClientSession = MagicMock(return_value=session)
    return mock_aiohttp


def _patch_for_http(session):
    """Context manager that patches both AIOHTTP_AVAILABLE and aiohttp module."""
    mock_aiohttp = _fake_aiohttp(session)
    return _combined_patch(mock_aiohttp)


class _combined_patch:
    """Patches AIOHTTP_AVAILABLE=True and injects aiohttp mock."""
    def __init__(self, mock_aiohttp):
        self.mock_aiohttp = mock_aiohttp
        self.patches = []

    def __enter__(self):
        self.patches = [
            patch.object(librarian_mod, "AIOHTTP_AVAILABLE", True),
            patch.object(librarian_mod, "aiohttp", self.mock_aiohttp, create=True),
        ]
        for p in self.patches:
            p.__enter__()
        return self.mock_aiohttp

    def __exit__(self, *args):
        for p in reversed(self.patches):
            p.__exit__(*args)


# ============================================================================
# TestConfig
# ============================================================================

class TestConfig:
    """Test configuration and check functions."""

    def test_configure_sets_global(self):
        configure_librarian(url="http://test:9090/rest")
        assert librarian_mod._config is not None
        assert librarian_mod._config.url == "http://test:9090/rest"

    def test_check_config_no_aiohttp(self):
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", False):
            error = _check_config()
            assert error is not None
            assert "aiohttp" in error

    def test_check_config_ok(self, configured):
        """When configured and aiohttp available, no error."""
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", True):
            error = _check_config()
            assert error is None

    def test_check_config_unconfigured(self):
        """When not configured (but aiohttp available), returns config error."""
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", True):
            error = _check_config()
            assert error is not None
            assert "not configured" in error


# ============================================================================
# TestResolvePath
# ============================================================================

class TestResolvePath:
    """Test path resolution and traversal prevention."""

    def test_absolute_passthrough(self, configured):
        assert _resolve_path("/db/custom/doc.xml") == "/db/custom/doc.xml"

    def test_relative_prepends_collection(self, configured):
        assert _resolve_path("sub/doc.xml") == "/db/agents/sub/doc.xml"

    def test_traversal_blocked_relative(self, configured):
        with pytest.raises(ValueError, match="traversal"):
            _resolve_path("../../etc/passwd")

    def test_traversal_blocked_absolute(self, configured):
        with pytest.raises(ValueError, match="traversal"):
            _resolve_path("/db/../../../etc/passwd")

    def test_normalize_dot(self, configured):
        result = _resolve_path("/db/agents/./sub")
        assert result == "/db/agents/sub"


# ============================================================================
# TestEscapeXqueryString
# ============================================================================

class TestEscapeXqueryString:
    """Test XQuery string escaping."""

    def test_no_quotes(self):
        assert _escape_xquery_string("hello") == "hello"

    def test_double_quotes_escaped(self):
        assert _escape_xquery_string('say "hi"') == 'say ""hi""'

    def test_empty_string(self):
        assert _escape_xquery_string("") == ""


# ============================================================================
# TestLibrarianStore
# ============================================================================

class TestLibrarianStore:
    """Test librarian_store tool."""

    async def test_not_configured(self):
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", True):
            result = await librarian_store(collection="test", document_name="doc.xml", content="<x/>")
            assert result.success is False
            assert "not configured" in result.error

    async def test_success(self, configured):
        resp = _mock_response(status=201)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_store(collection="sub", document_name="doc.xml", content="<data/>")
            assert result.success is True
            assert result.data["path"] == "/db/agents/sub/doc.xml"

    async def test_error_status(self, configured):
        resp = _mock_response(status=500, text="Internal error")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_store(collection="sub", document_name="doc.xml", content="<data/>")
            assert result.success is False
            assert "500" in result.error

    async def test_connection_error(self, configured):
        session = MagicMock()
        session.put = MagicMock(side_effect=ConnectionError("refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with _patch_for_http(session):
            result = await librarian_store(collection="sub", document_name="doc.xml", content="<data/>")
            assert result.success is False
            assert "Store error" in result.error

    async def test_path_traversal_blocked(self, configured):
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", True):
            result = await librarian_store(collection="../../etc", document_name="passwd", content="x")
            assert result.success is False
            assert "traversal" in result.error


# ============================================================================
# TestLibrarianGet
# ============================================================================

class TestLibrarianGet:
    """Test librarian_get tool."""

    async def test_success(self, configured):
        resp = _mock_response(status=200, text="<doc>content</doc>")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_get(path="/db/agents/doc.xml")
            assert result.success is True
            assert result.data["content"] == "<doc>content</doc>"

    async def test_404_error(self, configured):
        resp = _mock_response(status=404)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_get(path="/db/agents/missing.xml")
            assert result.success is False
            assert "Not found" in result.error

    async def test_other_error(self, configured):
        resp = _mock_response(status=500)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_get(path="/db/agents/doc.xml")
            assert result.success is False
            assert "500" in result.error


# ============================================================================
# TestLibrarianQuery
# ============================================================================

class TestLibrarianQuery:
    """Test librarian_query tool."""

    async def test_success(self, configured):
        resp = _mock_response(status=200, text="<results/>")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_query(query="//message")
            assert result.success is True
            assert result.data["results"] == "<results/>"

    async def test_with_variables(self, configured):
        resp = _mock_response(status=200, text="<results/>")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_query(
                query="//msg[@from=$sender]",
                variables={"sender": "greeter"},
            )
            assert result.success is True

    async def test_xquery_injection_blocked(self, configured):
        """Variables with double quotes should be escaped, not injected."""
        captured_data = {}
        resp = _mock_response(200, "<r/>")

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        def capture_post(*args, **kwargs):
            captured_data.update(kwargs.get("data", {}))
            return resp

        session.post = MagicMock(side_effect=capture_post)

        with _patch_for_http(session):
            result = await librarian_query(
                query="//msg",
                variables={"x": 'value"; drop table users; --'},
            )
            assert result.success is True
            query_sent = captured_data.get("_query", "")
            assert '""' in query_sent

    async def test_error_status(self, configured):
        resp = _mock_response(status=400, text="XQuery syntax error")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_query(query="invalid xquery {{{{")
            assert result.success is False
            assert "400" in result.error


# ============================================================================
# TestLibrarianSearch
# ============================================================================

class TestLibrarianSearch:
    """Test librarian_search tool."""

    async def test_success(self, configured):
        resp = _mock_response(status=200, text="<result><path>/doc</path><score>0.9</score></result>")
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await librarian_search(query="test query")
            assert result.success is True
            assert result.data["query"] == "test query"

    async def test_lucene_injection_blocked(self, configured):
        """Quotes in search query should be escaped."""
        captured_data = {}
        resp = _mock_response(200, "<r/>")

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        def capture_post(*args, **kwargs):
            captured_data.update(kwargs.get("data", {}))
            return resp

        session.post = MagicMock(side_effect=capture_post)

        with _patch_for_http(session):
            result = await librarian_search(query='test" OR 1=1 --')
            assert result.success is True
            query_sent = captured_data.get("_query", "")
            assert '""' in query_sent

    async def test_num_results_forwarded(self, configured):
        captured_data = {}
        resp = _mock_response(200, "<r/>")

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        def capture_post(*args, **kwargs):
            captured_data.update(kwargs.get("data", {}))
            return resp

        session.post = MagicMock(side_effect=capture_post)

        with _patch_for_http(session):
            result = await librarian_search(query="test", num_results=5)
            assert result.success is True
            assert captured_data.get("_howmany") == "5"


# ============================================================================
# TestNoAiohttp
# ============================================================================

class TestNoAiohttp:
    """Test behavior when aiohttp is not available."""

    async def test_store_error(self):
        configure_librarian()
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", False):
            result = await librarian_store(collection="col", document_name="doc.xml", content="<x/>")
            assert result.success is False
            assert "aiohttp" in result.error

    async def test_get_error(self):
        configure_librarian()
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", False):
            result = await librarian_get(path="/path")
            assert result.success is False
            assert "aiohttp" in result.error

    async def test_query_error(self):
        configure_librarian()
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", False):
            result = await librarian_query(query="//x")
            assert result.success is False
            assert "aiohttp" in result.error

    async def test_search_error(self):
        configure_librarian()
        with patch.object(librarian_mod, "AIOHTTP_AVAILABLE", False):
            result = await librarian_search(query="test")
            assert result.success is False
            assert "aiohttp" in result.error
