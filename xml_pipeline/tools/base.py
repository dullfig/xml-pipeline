"""
Base classes and registry for tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from functools import wraps
import inspect


@dataclass
class ToolResult:
    """Result from a tool invocation."""
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class Tool:
    """Tool metadata and implementation."""
    name: str
    description: str
    func: Callable
    parameters: Dict[str, Any] = field(default_factory=dict)

    async def invoke(self, **kwargs) -> ToolResult:
        """Invoke the tool with given parameters."""
        try:
            result = await self.func(**kwargs)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def all(self) -> Dict[str, Tool]:
        """Get all tools."""
        return dict(self._tools)


# Global registry
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def tool(func: Callable) -> Callable:
    """Decorator to register a function as a tool."""
    # Extract metadata from function
    name = func.__name__
    description = func.__doc__ or ""

    # Extract parameters from signature
    sig = inspect.signature(func)
    parameters = {}
    for param_name, param in sig.parameters.items():
        param_info = {"name": param_name}
        if param.annotation != inspect.Parameter.empty:
            ann = param.annotation
            # Handle both string annotations (from __future__ import annotations) and type objects
            if isinstance(ann, str):
                param_info["type"] = ann
            elif hasattr(ann, "__name__"):
                param_info["type"] = ann.__name__
            else:
                param_info["type"] = str(ann)
        if param.default != inspect.Parameter.empty:
            param_info["default"] = param.default
        parameters[param_name] = param_info

    # Create tool
    t = Tool(
        name=name,
        description=description.strip(),
        func=func,
        parameters=parameters,
    )

    # Register
    get_tool_registry().register(t)

    @wraps(func)
    async def wrapper(**kwargs):
        return await t.invoke(**kwargs)

    wrapper._tool = t
    return wrapper
