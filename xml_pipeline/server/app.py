"""
aiohttp-based HTTP/WebSocket server.

Provides:
- REST API for authentication
- WebSocket for console/GUI message sending
- Integration with SystemPipeline for message injection
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional, Callable

try:
    from aiohttp import web, WSMsgType
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None
    WSMsgType = None

from ..auth.users import get_user_store, UserStore
from ..auth.sessions import get_session_manager, SessionManager, Session

if TYPE_CHECKING:
    from ..message_bus.stream_pump import StreamPump
    from ..message_bus.system_pipeline import SystemPipeline

logger = logging.getLogger(__name__)


def auth_middleware():
    @web.middleware
    async def middleware(request, handler):
        if request.path in ("/auth/login", "/health"):
            return await handler(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "Missing Authorization"}, status=401)

        token = auth_header[7:]
        session = request.app["session_manager"].validate(token)

        if not session:
            return web.json_response({"error": "Invalid token"}, status=401)

        request["session"] = session
        return await handler(request)

    return middleware


async def handle_login(request):
    try:
        data = await request.json()
    except:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    username = data.get("username", "")
    password = data.get("password", "")

    if not username or not password:
        return web.json_response({"error": "Credentials required"}, status=400)

    user = request.app["user_store"].authenticate(username, password)
    if not user:
        return web.json_response({"error": "Invalid credentials"}, status=401)

    session = request.app["session_manager"].create(user.username, user.role)
    return web.json_response(session.to_dict())


async def handle_logout(request):
    session = request["session"]
    request.app["session_manager"].revoke(session.token)
    return web.json_response({"message": "Logged out"})


async def handle_me(request):
    session = request["session"]
    return web.json_response({
        "username": session.username,
        "role": session.role,
        "expires_at": session.expires_at.isoformat(),
    })


async def handle_health(request):
    return web.json_response({"status": "ok"})


async def handle_websocket(request):
    session = request["session"]
    pump = request.app.get("pump")
    system_pipeline = request.app.get("system_pipeline")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Track this WebSocket for response delivery
    ws_id = id(ws)
    request.app["websockets"][ws_id] = {
        "ws": ws,
        "user": session.username,
        "threads": set(),  # Thread IDs this client is subscribed to
    }

    await ws.send_json({"type": "connected", "username": session.username})

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    resp = await handle_ws_msg(
                        data, session, pump, system_pipeline,
                        request.app["websockets"][ws_id]
                    )
                    await ws.send_json(resp)
                except Exception as e:
                    logger.exception(f"WebSocket error: {e}")
                    await ws.send_json({"type": "error", "error": str(e)})
    finally:
        # Cleanup on disconnect
        del request.app["websockets"][ws_id]

    return ws


async def handle_ws_msg(data, session, pump, system_pipeline, ws_state):
    """
    Handle WebSocket message.

    Message types:
        ping        - Keepalive
        status      - Get server status
        listeners   - List available listeners
        targets     - Alias for listeners
        send        - Send message to pipeline (@target or explicit)
    """
    t = data.get("type", "")

    if t == "ping":
        return {"type": "pong"}

    elif t == "status":
        from ..memory import get_context_buffer
        stats = get_context_buffer().get_stats()
        return {"type": "status", "threads": stats["thread_count"]}

    elif t == "listeners" or t == "targets":
        if not pump:
            return {"type": "listeners", "listeners": []}
        return {"type": "listeners", "listeners": list(pump.listeners.keys())}

    elif t == "send":
        # Send message to pipeline
        if not system_pipeline:
            return {"type": "error", "error": "Pipeline not available"}

        # Support two formats:
        # 1. {"type": "send", "raw": "@greeter Dan"}
        # 2. {"type": "send", "target": "greeter", "content": "Dan"}
        raw = data.get("raw")
        if raw:
            # Parse @target message format
            try:
                thread_id = await system_pipeline.inject_console(
                    raw=raw,
                    user=session.username,
                )
            except ValueError as e:
                return {"type": "error", "error": str(e)}
        else:
            target = data.get("target")
            content = data.get("content", data.get("text", data.get("message", "")))

            if not target:
                return {"type": "error", "error": "Missing target"}
            if not content:
                return {"type": "error", "error": "Missing content"}

            try:
                thread_id = await system_pipeline.inject_raw(
                    target=target,
                    content=content,
                    source="websocket",
                    user=session.username,
                )
            except ValueError as e:
                return {"type": "error", "error": str(e)}

        # Track thread for response delivery
        ws_state["threads"].add(thread_id)

        return {
            "type": "sent",
            "thread_id": thread_id,
            "target": data.get("target") or raw.split()[0].lstrip("@") if raw else None,
        }

    return {"type": "error", "error": f"Unknown message type: {t}"}


def create_app(pump=None, system_pipeline=None):
    """
    Create the aiohttp application.

    Args:
        pump: StreamPump instance (optional)
        system_pipeline: SystemPipeline instance (optional, created from pump if not provided)
    """
    if not AIOHTTP_AVAILABLE:
        raise RuntimeError("aiohttp not installed")

    app = web.Application(middlewares=[auth_middleware()])
    app["user_store"] = get_user_store()
    app["session_manager"] = get_session_manager()
    app["pump"] = pump
    app["websockets"] = {}  # Track connected WebSocket clients

    # Create SystemPipeline if pump provided but system_pipeline not
    if pump and not system_pipeline:
        from ..message_bus.system_pipeline import SystemPipeline
        system_pipeline = SystemPipeline(pump)

    app["system_pipeline"] = system_pipeline

    app.router.add_post("/auth/login", handle_login)
    app.router.add_post("/auth/logout", handle_logout)
    app.router.add_get("/auth/me", handle_me)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ws", handle_websocket)

    return app


async def run_server(pump=None, host="127.0.0.1", port=8765):
    """
    Run the server.

    Args:
        pump: StreamPump instance for message handling
        host: Bind address
        port: Port number
    """
    app = create_app(pump)
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"Server on http://{host}:{port}")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
