"""
BloxServer API - FastAPI Application

Main entry point for the BloxServer backend API.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bloxserver.api.models.database import init_db
from bloxserver.api.routes import executions, flows, health, triggers, webhooks
from bloxserver.api.schemas import ApiError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown events."""
    # Startup
    print("Starting BloxServer API...")

    # Initialize database tables
    if os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true":
        await init_db()
        print("Database tables initialized")

    yield

    # Shutdown
    print("Shutting down BloxServer API...")


# Create FastAPI app
app = FastAPI(
    title="BloxServer API",
    description="Backend API for BloxServer - Visual AI Agent Workflow Builder",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENABLE_DOCS", "true").lower() == "true" else None,
    redoc_url="/redoc" if os.getenv("ENABLE_DOCS", "true").lower() == "true" else None,
)


# =============================================================================
# CORS Middleware
# =============================================================================

# Allowed origins (configure via environment)
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,https://app.openblox.ai",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Exception Handlers
# =============================================================================


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert validation errors to standard API error format."""
    errors = exc.errors()
    details = {
        ".".join(str(loc) for loc in err["loc"]): err["msg"]
        for err in errors
    }

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ApiError(
            code="validation_error",
            message="Request validation failed",
            details=details,
        ).model_dump(by_alias=True),
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all exception handler."""
    # In production, don't expose internal errors
    if os.getenv("ENV", "development") == "production":
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ApiError(
                code="internal_error",
                message="An unexpected error occurred",
            ).model_dump(by_alias=True),
        )

    # In development, include error details
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ApiError(
            code="internal_error",
            message=str(exc),
            details={"type": type(exc).__name__},
        ).model_dump(by_alias=True),
    )


# =============================================================================
# Routes
# =============================================================================

# Health checks (no auth)
app.include_router(health.router)

# Webhook endpoint (token-based auth)
app.include_router(webhooks.router)

# Protected API routes
app.include_router(flows.router, prefix="/api/v1")
app.include_router(triggers.router, prefix="/api/v1")
app.include_router(executions.router, prefix="/api/v1")


# =============================================================================
# Root endpoint
# =============================================================================


@app.get("/")
async def root() -> dict:
    """Root endpoint - API info."""
    return {
        "name": "BloxServer API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


# =============================================================================
# Run with uvicorn
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "bloxserver.api.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "development") == "development",
    )
