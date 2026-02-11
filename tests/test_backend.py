"""
Tests for LLM backend response validation (#41) and empty API key rejection (#46).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xml_pipeline.llm.backend import (
    MAX_RESPONSE_CONTENT,
    BackendError,
    XAIBackend,
    AnthropicBackend,
    OpenAIBackend,
    OllamaBackend,
    _validate_llm_response,
    create_backend,
)


# ============================================================================
# _validate_llm_response — structural validation
# ============================================================================


class TestValidateLLMResponse:
    """Test the response validation helper."""

    def test_valid_openai_response(self):
        data = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "model": "gpt-4",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        content = _validate_llm_response(data, "openai")
        assert content == "Hello"

    def test_valid_xai_response(self):
        data = {
            "choices": [{"message": {"content": "Grok says hi"}, "finish_reason": "stop"}],
        }
        content = _validate_llm_response(data, "xai")
        assert content == "Grok says hi"

    def test_valid_anthropic_response(self):
        data = {
            "content": [{"type": "text", "text": "Claude says hi"}],
            "model": "claude-3",
        }
        content = _validate_llm_response(data, "anthropic")
        assert content == "Claude says hi"

    def test_valid_ollama_response(self):
        data = {"message": {"content": "Llama says hi"}}
        content = _validate_llm_response(data, "ollama")
        assert content == "Llama says hi"

    def test_missing_choices_raises(self):
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response({"model": "gpt-4"}, "openai")

    def test_empty_choices_raises(self):
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response({"choices": []}, "xai")

    def test_content_not_string_raises(self):
        data = {"choices": [{"message": {"content": 12345}}]}
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response(data, "openai")

    def test_anthropic_content_not_list_raises(self):
        data = {"content": "not a list"}
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response(data, "anthropic")

    def test_ollama_missing_message_raises(self):
        data = {"response": "no message key"}
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response(data, "ollama")

    def test_not_dict_raises(self):
        with pytest.raises(BackendError, match="Malformed"):
            _validate_llm_response("string", "openai")

    def test_huge_content_truncated(self):
        huge = "x" * (MAX_RESPONSE_CONTENT + 1000)
        data = {"choices": [{"message": {"content": huge}}]}
        content = _validate_llm_response(data, "openai")
        assert len(content) == MAX_RESPONSE_CONTENT

    def test_anthropic_multiple_text_blocks(self):
        data = {
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "tool_use", "id": "t1"},
                {"type": "text", "text": "World"},
            ]
        }
        content = _validate_llm_response(data, "anthropic")
        assert content == "Hello World"


# ============================================================================
# create_backend — empty API key rejection (#46)
# ============================================================================


class TestCreateBackendAPIKey:
    """Test API key validation at backend creation time."""

    def test_empty_key_xai_raises(self):
        with pytest.raises(ValueError, match="API key required for xai"):
            create_backend({"provider": "xai", "api_key": ""})

    def test_empty_key_anthropic_raises(self):
        with pytest.raises(ValueError, match="API key required for anthropic"):
            create_backend({"provider": "anthropic"})

    def test_empty_key_openai_raises(self):
        with pytest.raises(ValueError, match="API key required for openai"):
            create_backend({"provider": "openai", "api_key": ""})

    def test_empty_key_ollama_ok(self):
        backend = create_backend({"provider": "ollama"})
        assert backend.provider == "ollama"

    def test_env_var_missing_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                create_backend({"provider": "xai", "api_key_env": "NONEXISTENT_KEY"})

    def test_env_var_present_ok(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "sk-test-123"}):
            backend = create_backend({"provider": "xai", "api_key_env": "TEST_API_KEY"})
            assert backend.api_key == "sk-test-123"


# ============================================================================
# Backend _do_completion — malformed responses
# ============================================================================


class TestBackendMalformedResponse:
    """Test that backends raise BackendError on malformed provider responses."""

    @staticmethod
    def _mock_response(json_data, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        resp.headers = {}
        return resp

    async def test_xai_malformed_raises_backend_error(self):
        backend = XAIBackend(name="xai", api_key="test")
        client = AsyncMock()
        client.post.return_value = self._mock_response({"bad": "data"})
        with pytest.raises(BackendError, match="Malformed"):
            from xml_pipeline.llm.backend import LLMRequest
            await backend._do_completion(client, LLMRequest(
                messages=[{"role": "user", "content": "hi"}], model="grok-1"
            ))

    async def test_anthropic_malformed_raises_backend_error(self):
        backend = AnthropicBackend(name="anthropic", api_key="test")
        client = AsyncMock()
        client.post.return_value = self._mock_response({"content": "not-a-list"})
        with pytest.raises(BackendError, match="Malformed"):
            from xml_pipeline.llm.backend import LLMRequest
            await backend._do_completion(client, LLMRequest(
                messages=[{"role": "user", "content": "hi"}], model="claude-3"
            ))

    async def test_ollama_malformed_raises_backend_error(self):
        backend = OllamaBackend(name="ollama", api_key="")
        client = AsyncMock()
        client.post.return_value = self._mock_response({"no_message": True})
        with pytest.raises(BackendError, match="Malformed"):
            from xml_pipeline.llm.backend import LLMRequest
            await backend._do_completion(client, LLMRequest(
                messages=[{"role": "user", "content": "hi"}], model="llama3"
            ))
