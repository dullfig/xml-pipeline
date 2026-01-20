"""
Search tool - web search integration.

Requires configuration of a search provider API.
Supported providers: SerpAPI, Google Custom Search, Bing Search.
"""

from __future__ import annotations

from typing import Optional, List
from dataclasses import dataclass

from .base import tool, ToolResult


# Try to import aiohttp for HTTP requests
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class SearchConfig:
    """Configuration for search provider."""
    provider: str  # "serpapi", "google", "bing"
    api_key: str
    engine_id: Optional[str] = None  # For Google Custom Search


# Global config - set via configure_search()
_config: Optional[SearchConfig] = None


def configure_search(
    provider: str,
    api_key: str,
    engine_id: Optional[str] = None,
) -> None:
    """
    Configure the search provider.

    Args:
        provider: "serpapi", "google", or "bing"
        api_key: API key for the provider
        engine_id: Required for Google Custom Search

    Example:
        configure_search("serpapi", os.environ["SERPAPI_KEY"])
    """
    global _config
    _config = SearchConfig(
        provider=provider,
        api_key=api_key,
        engine_id=engine_id,
    )


async def _search_serpapi(query: str, num_results: int) -> List[dict]:
    """Search using SerpAPI."""
    async with aiohttp.ClientSession() as session:
        params = {
            "q": query,
            "api_key": _config.api_key,
            "num": num_results,
            "engine": "google",
        }
        async with session.get(
            "https://serpapi.com/search",
            params=params,
        ) as resp:
            if resp.status != 200:
                raise Exception(f"SerpAPI error: {resp.status}")
            data = await resp.json()
            results = []
            for item in data.get("organic_results", [])[:num_results]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results


async def _search_google(query: str, num_results: int) -> List[dict]:
    """Search using Google Custom Search API."""
    if not _config.engine_id:
        raise Exception("Google Custom Search requires engine_id")
    
    async with aiohttp.ClientSession() as session:
        params = {
            "q": query,
            "key": _config.api_key,
            "cx": _config.engine_id,
            "num": min(num_results, 10),  # API max is 10
        }
        async with session.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Google API error: {resp.status}")
            data = await resp.json()
            results = []
            for item in data.get("items", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results


async def _search_bing(query: str, num_results: int) -> List[dict]:
    """Search using Bing Search API."""
    async with aiohttp.ClientSession() as session:
        headers = {"Ocp-Apim-Subscription-Key": _config.api_key}
        params = {
            "q": query,
            "count": num_results,
        }
        async with session.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Bing API error: {resp.status}")
            data = await resp.json()
            results = []
            for item in data.get("webPages", {}).get("value", []):
                results.append({
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results


@tool
async def web_search(
    query: str,
    num_results: int = 5,
) -> ToolResult:
    """
    Search the web.

    Args:
        query: Search query
        num_results: Number of results (default: 5, max: 20)

    Returns:
        results: Array of {title, url, snippet}

    Configuration:
        Call configure_search() before use:
        
        from xml_pipeline.tools.search import configure_search
        configure_search("serpapi", "your-api-key")
    """
    if not AIOHTTP_AVAILABLE:
        return ToolResult(
            success=False,
            error="aiohttp not installed. Install with: pip install xml-pipeline[server]"
        )
    
    if not _config:
        return ToolResult(
            success=False,
            error="Search not configured. Call configure_search() first."
        )
    
    # Clamp num_results
    num_results = min(max(1, num_results), 20)
    
    try:
        if _config.provider == "serpapi":
            results = await _search_serpapi(query, num_results)
        elif _config.provider == "google":
            results = await _search_google(query, num_results)
        elif _config.provider == "bing":
            results = await _search_bing(query, num_results)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown provider: {_config.provider}"
            )
        
        return ToolResult(success=True, data={
            "query": query,
            "results": results,
            "count": len(results),
        })
    except Exception as e:
        return ToolResult(success=False, error=f"Search error: {e}")
