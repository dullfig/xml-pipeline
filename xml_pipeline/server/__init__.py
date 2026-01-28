"""
server — FastAPI-based AgentServer API for monitoring and controlling organisms.

Provides:
- REST API for querying organism state (agents, threads, messages)
- WebSocket for real-time events
- Message injection endpoint

Usage:
    from xml_pipeline.server import create_app, run_server

    # With existing pump
    app = create_app(pump)
    uvicorn.run(app, host="0.0.0.0", port=8080)

    # Or use CLI
    xml-pipeline serve config/organism.yaml --port 8080
"""

from xml_pipeline.server.app import create_app, run_server, run_server_sync

__all__ = [
    "create_app",
    "run_server",
    "run_server_sync",
]
