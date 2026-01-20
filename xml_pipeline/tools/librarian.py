"""
Librarian tools - exist-db XML database integration.

Provides XQuery-based document storage and retrieval for long-term memory.
Requires exist-db to be running and configured.
"""

from __future__ import annotations

from typing import Optional, Dict
from dataclasses import dataclass

from .base import tool, ToolResult


try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class ExistDBConfig:
    url: str = "http://localhost:8080/exist/rest"
    username: str = "admin"
    password: str = ""
    default_collection: str = "/db/agents"


_config: Optional[ExistDBConfig] = None


def configure_librarian(
    url: str = "http://localhost:8080/exist/rest",
    username: str = "admin",
    password: str = "",
    default_collection: str = "/db/agents",
) -> None:
    global _config
    _config = ExistDBConfig(url=url, username=username, password=password, default_collection=default_collection)


def _check_config() -> Optional[str]:
    if not AIOHTTP_AVAILABLE:
        return "aiohttp not installed. Install with: pip install xml-pipeline[server]"
    if not _config:
        return "Librarian not configured. Call configure_librarian() first."
    return None


def _resolve_path(path: str) -> str:
    if path.startswith("/"):
        return path
    return f"{_config.default_collection}/{path}"


@tool
async def librarian_store(collection: str, document_name: str, content: str) -> ToolResult:
    """Store an XML document in exist-db."""
    if error := _check_config():
        return ToolResult(success=False, error=error)
    collection = _resolve_path(collection)
    url = f"{_config.url}{collection}/{document_name}"
    try:
        auth = aiohttp.BasicAuth(_config.username, _config.password)
        async with aiohttp.ClientSession() as session:
            async with session.put(url, data=content.encode("utf-8"),
                                   headers={"Content-Type": "application/xml"}, auth=auth) as resp:
                if resp.status in (200, 201):
                    return ToolResult(success=True, data={"path": f"{collection}/{document_name}"})
                return ToolResult(success=False, error=f"exist-db error {resp.status}: {await resp.text()}")
    except Exception as e:
        return ToolResult(success=False, error=f"Store error: {e}")


@tool
async def librarian_get(path: str) -> ToolResult:
    """Retrieve a document by path."""
    if error := _check_config():
        return ToolResult(success=False, error=error)
    path = _resolve_path(path)
    url = f"{_config.url}{path}"
    try:
        auth = aiohttp.BasicAuth(_config.username, _config.password)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth) as resp:
                if resp.status == 200:
                    return ToolResult(success=True, data={"content": await resp.text(), "path": path})
                elif resp.status == 404:
                    return ToolResult(success=False, error=f"Not found: {path}")
                return ToolResult(success=False, error=f"exist-db error {resp.status}")
    except Exception as e:
        return ToolResult(success=False, error=f"Get error: {e}")


@tool
async def librarian_query(query: str, collection: Optional[str] = None, variables: Optional[Dict[str, str]] = None) -> ToolResult:
    """Execute an XQuery against exist-db."""
    if error := _check_config():
        return ToolResult(success=False, error=error)
    base_path = _resolve_path(collection) if collection else "/db"
    url = f"{_config.url}{base_path}"
    full_query = query
    if variables:
        var_decls = "\n".join(f'declare variable ${k} external := "{v}";' for k, v in variables.items())
        full_query = f"{var_decls}\n{query}"
    try:
        auth = aiohttp.BasicAuth(_config.username, _config.password)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data={"_query": full_query}, auth=auth) as resp:
                if resp.status == 200:
                    return ToolResult(success=True, data={"results": await resp.text(), "collection": base_path})
                return ToolResult(success=False, error=f"XQuery error {resp.status}: {await resp.text()}")
    except Exception as e:
        return ToolResult(success=False, error=f"Query error: {e}")


@tool
async def librarian_search(query: str, collection: Optional[str] = None, num_results: int = 10) -> ToolResult:
    """Full-text search across documents using Lucene."""
    if error := _check_config():
        return ToolResult(success=False, error=error)
    base_path = _resolve_path(collection) if collection else _config.default_collection
    xquery = f'import module namespace ft="http://exist-db.org/xquery/lucene"; for $hit in collection("{base_path}")//*[ft:query(., "{query}")] let $score := ft:score($hit) order by $score descending return <result><path>{{document-uri(root($hit))}}</path><score>{{$score}}</score></result>'
    url = f"{_config.url}{base_path}"
    try:
        auth = aiohttp.BasicAuth(_config.username, _config.password)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data={"_query": xquery, "_howmany": str(num_results)}, auth=auth) as resp:
                if resp.status == 200:
                    return ToolResult(success=True, data={"results": await resp.text(), "query": query})
                return ToolResult(success=False, error=f"Search error {resp.status}: {await resp.text()}")
    except Exception as e:
        return ToolResult(success=False, error=f"Search error: {e}")
