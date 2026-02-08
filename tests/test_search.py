"""
Tests for web search tool.

All tests mock aiohttp — no real HTTP calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xml_pipeline.tools.search import (
    SearchConfig,
    configure_search,
    web_search,
)
import xml_pipeline.tools.search as search_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_config():
    """Reset search config before each test."""
    search_mod._config = None
    yield
    search_mod._config = None


def _mock_response(status: int = 200, json_data: dict | None = None):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response):
    """Create a mock aiohttp.ClientSession."""
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class _patch_for_http:
    """Patches AIOHTTP_AVAILABLE=True and injects aiohttp mock."""
    def __init__(self, session):
        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = MagicMock(return_value=session)
        self.mock_aiohttp = mock_aiohttp
        self.patches = []

    def __enter__(self):
        self.patches = [
            patch.object(search_mod, "AIOHTTP_AVAILABLE", True),
            patch.object(search_mod, "aiohttp", self.mock_aiohttp, create=True),
        ]
        for p in self.patches:
            p.__enter__()
        return self.mock_aiohttp

    def __exit__(self, *args):
        for p in reversed(self.patches):
            p.__exit__(*args)


# ============================================================================
# TestSearchConfig
# ============================================================================

class TestSearchConfig:
    """Test configuration functions."""

    def test_configure_search_sets_global(self):
        configure_search("serpapi", "test-key")
        assert search_mod._config is not None
        assert search_mod._config.provider == "serpapi"
        assert search_mod._config.api_key == "test-key"

    async def test_missing_config_error(self):
        with patch.object(search_mod, "AIOHTTP_AVAILABLE", True):
            result = await web_search(query="test")
            assert result.success is False
            assert "not configured" in result.error

    async def test_missing_aiohttp_error(self):
        configure_search("serpapi", "key")
        with patch.object(search_mod, "AIOHTTP_AVAILABLE", False):
            result = await web_search(query="test")
            assert result.success is False
            assert "aiohttp" in result.error


# ============================================================================
# TestNumResultsClamping
# ============================================================================

class TestNumResultsClamping:
    """Test num_results is clamped to 1..20."""

    async def test_zero_clamped_to_1(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test", num_results=0)
            assert result.success is True

    async def test_negative_clamped_to_1(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test", num_results=-5)
            assert result.success is True

    async def test_within_range(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": [
            {"title": f"Result {i}", "link": f"https://example.com/{i}", "snippet": f"Snippet {i}"}
            for i in range(5)
        ]})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test", num_results=5)
            assert result.success is True
            assert result.data["count"] == 5

    async def test_over_max_clamped_to_20(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test", num_results=100)
            assert result.success is True


# ============================================================================
# TestSerpApiProvider
# ============================================================================

class TestSerpApiProvider:
    """Test SerpAPI search provider."""

    async def test_success(self):
        configure_search("serpapi", "test-key")
        resp = _mock_response(200, {
            "organic_results": [
                {"title": "Test", "link": "https://example.com", "snippet": "A test result"},
                {"title": "Test 2", "link": "https://example.com/2", "snippet": "Another"},
            ]
        })
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test query")
            assert result.success is True
            assert result.data["count"] == 2
            assert result.data["results"][0]["title"] == "Test"
            assert result.data["results"][0]["url"] == "https://example.com"

    async def test_error_status(self):
        configure_search("serpapi", "bad-key")
        resp = _mock_response(401)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is False
            assert "error" in result.error.lower()

    async def test_empty_results(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="obscure query")
            assert result.success is True
            assert result.data["count"] == 0


# ============================================================================
# TestGoogleProvider
# ============================================================================

class TestGoogleProvider:
    """Test Google Custom Search provider."""

    async def test_success(self):
        configure_search("google", "google-key", engine_id="cx123")
        resp = _mock_response(200, {
            "items": [
                {"title": "Google Result", "link": "https://google.com/1", "snippet": "Found it"},
            ]
        })
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is True
            assert result.data["count"] == 1
            assert result.data["results"][0]["title"] == "Google Result"

    async def test_missing_engine_id(self):
        configure_search("google", "key")  # No engine_id
        resp = _mock_response(200, {"items": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is False
            assert "engine_id" in result.error.lower()

    async def test_error_status(self):
        configure_search("google", "key", engine_id="cx123")
        resp = _mock_response(403)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is False


# ============================================================================
# TestBingProvider
# ============================================================================

class TestBingProvider:
    """Test Bing Search provider."""

    async def test_success(self):
        configure_search("bing", "bing-key")
        resp = _mock_response(200, {
            "webPages": {
                "value": [
                    {"name": "Bing Result", "url": "https://bing.com/1", "snippet": "Found via Bing"},
                ]
            }
        })
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is True
            assert result.data["count"] == 1
            assert result.data["results"][0]["title"] == "Bing Result"

    async def test_error_status(self):
        configure_search("bing", "bad-key")
        resp = _mock_response(401)
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is False


# ============================================================================
# TestUnknownProvider
# ============================================================================

class TestUnknownProvider:
    """Test unknown provider returns error."""

    async def test_unknown_provider(self):
        configure_search("yandex", "key")
        with patch.object(search_mod, "AIOHTTP_AVAILABLE", True):
            result = await web_search(query="test")
            assert result.success is False
            assert "Unknown provider" in result.error


# ============================================================================
# TestWebSearchTool
# ============================================================================

class TestWebSearchTool:
    """Test end-to-end web_search tool."""

    async def test_connection_error(self):
        configure_search("serpapi", "key")
        session = MagicMock()
        session.get = MagicMock(side_effect=ConnectionError("refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with _patch_for_http(session):
            result = await web_search(query="test")
            assert result.success is False
            assert "error" in result.error.lower()

    async def test_query_forwarded(self):
        configure_search("serpapi", "key")
        resp = _mock_response(200, {"organic_results": []})
        session = _mock_session(resp)

        with _patch_for_http(session):
            result = await web_search(query="my specific query")
            assert result.success is True
            assert result.data["query"] == "my specific query"
