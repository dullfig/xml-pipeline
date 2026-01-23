"""
Health check and status endpoints.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import text

from bloxserver.api.models.database import async_session_maker

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """
    Basic health check.

    Returns 200 if the service is running.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "bloxserver-api",
    }


@router.get("/health/ready")
async def readiness_check() -> dict:
    """
    Readiness check - verifies database connectivity.

    Used by Kubernetes/load balancers to determine if the service
    is ready to receive traffic.
    """
    errors = []

    # Check database
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        errors.append(f"database: {e}")

    # TODO: Check Redis
    # TODO: Check other dependencies

    if errors:
        return {
            "status": "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "errors": errors,
        }

    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {
            "database": "ok",
        },
    }


@router.get("/health/live")
async def liveness_check() -> dict:
    """
    Liveness check - just confirms the process is running.

    If this fails, Kubernetes should restart the pod.
    """
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat(),
    }
