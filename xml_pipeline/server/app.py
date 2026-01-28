"""
app.py — FastAPI application factory for AgentServer.

Creates the FastAPI app that combines REST API and WebSocket endpoints.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xml_pipeline.server.api import create_router
from xml_pipeline.server.state import ServerState
from xml_pipeline.server.websocket import create_websocket_router

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump


def create_app(
    pump: "StreamPump",
    *,
    title: str = "AgentServer API",
    version: str = "1.0.0",
    cors_origins: Optional[list[str]] = None,
) -> FastAPI:
    """
    Create FastAPI application with REST and WebSocket endpoints.

    Args:
        pump: The StreamPump instance to wrap
        title: API title for OpenAPI docs
        version: API version
        cors_origins: List of allowed CORS origins (default: all)

    Returns:
        Configured FastAPI application
    """
    # Create state manager
    state = ServerState(pump)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage app lifecycle - startup and shutdown."""
        # Startup
        state.set_running()
        yield
        # Shutdown
        state.set_stopping()

    app = FastAPI(
        title=title,
        version=version,
        description="REST and WebSocket API for monitoring and controlling xml-pipeline organisms.",
        lifespan=lifespan,
    )

    # CORS middleware
    if cors_origins is None:
        cors_origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(create_router(state))
    app.include_router(create_websocket_router(state))

    # Store state on app for access if needed
    app.state.server_state = state
    app.state.pump = pump

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        info = state.get_organism_info()
        return {
            "status": "healthy",
            "organism": info.name,
            "uptime_seconds": info.uptime_seconds,
        }

    return app


async def run_server(
    pump: "StreamPump",
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    cors_origins: Optional[list[str]] = None,
) -> None:
    """
    Run the AgentServer with uvicorn.

    Args:
        pump: The StreamPump instance to wrap
        host: Host to bind to
        port: Port to listen on
        cors_origins: List of allowed CORS origins
    """
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(
            "uvicorn is required for the server. Install with: pip install xml-pipeline[server]"
        ) from e

    app = create_app(pump, cors_origins=cors_origins)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


def run_server_sync(
    pump: "StreamPump",
    *,
    host: str = "0.0.0.0",
    port: int = 8080,
    cors_origins: Optional[list[str]] = None,
) -> None:
    """
    Run the AgentServer synchronously (blocking).

    This is a convenience wrapper for CLI usage.

    Args:
        pump: The StreamPump instance to wrap
        host: Host to bind to
        port: Port to listen on
        cors_origins: List of allowed CORS origins
    """
    asyncio.run(run_server(pump, host=host, port=port, cors_origins=cors_origins))
