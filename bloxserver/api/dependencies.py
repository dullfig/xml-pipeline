"""
FastAPI dependencies for authentication and database access.

Uses Clerk for JWT validation.
"""

from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bloxserver.api.models.database import get_db
from bloxserver.api.models.tables import UserRecord

# Dev mode - skip auth for local testing
DEV_MODE = os.getenv("ENV", "development") == "development" and not os.getenv("CLERK_ISSUER")

# Clerk configuration
CLERK_ISSUER = os.getenv("CLERK_ISSUER", "")
CLERK_JWKS_URL = f"{CLERK_ISSUER}/.well-known/jwks.json" if CLERK_ISSUER else ""

# Security scheme
security = HTTPBearer(auto_error=False)


# =============================================================================
# JWT Validation (Clerk)
# =============================================================================


async def get_clerk_jwks() -> dict:
    """Fetch Clerk's JWKS for JWT validation."""
    async with httpx.AsyncClient() as client:
        response = await client.get(CLERK_JWKS_URL)
        response.raise_for_status()
        return response.json()


async def validate_clerk_token(token: str) -> dict:
    """
    Validate a Clerk JWT token and return the payload.

    In production, use a proper JWT library with caching.
    This is a simplified version for the scaffold.
    """
    import jwt
    from jwt import PyJWKClient

    try:
        # Get signing key from Clerk's JWKS
        jwks_client = PyJWKClient(CLERK_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and validate
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=os.getenv("CLERK_AUDIENCE"),
            issuer=CLERK_ISSUER,
        )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )


# =============================================================================
# Current User Dependency
# =============================================================================


class CurrentUser:
    """Authenticated user context."""

    def __init__(self, user: UserRecord, clerk_payload: dict):
        self.user = user
        self.clerk_payload = clerk_payload

    @property
    def id(self) -> UUID:
        return self.user.id

    @property
    def clerk_id(self) -> str:
        return self.user.clerk_id

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def tier(self) -> str:
        return self.user.tier.value


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    """
    Dependency that validates the JWT and returns the current user.

    Creates the user record if this is their first request (synced from Clerk).
    In DEV_MODE without Clerk configured, returns a test user.
    """
    # Dev mode - create/return a test user without auth
    if DEV_MODE:
        dev_clerk_id = "dev_user_001"
        result = await db.execute(
            select(UserRecord).where(UserRecord.clerk_id == dev_clerk_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            from bloxserver.api.models.tables import Tier
            user = UserRecord(
                clerk_id=dev_clerk_id,
                email="dev@localhost",
                name="Dev User",
                tier=Tier.PRO,  # Give dev user Pro access
            )
            db.add(user)
            await db.flush()

        return CurrentUser(user=user, clerk_payload={"sub": dev_clerk_id, "dev": True})

    # Production mode - require Clerk auth
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate JWT
    payload = await validate_clerk_token(credentials.credentials)
    clerk_id = payload.get("sub")

    if not clerk_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
        )

    # Look up or create user
    result = await db.execute(
        select(UserRecord).where(UserRecord.clerk_id == clerk_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        # First login - create user record from Clerk data
        user = UserRecord(
            clerk_id=clerk_id,
            email=payload.get("email", f"{clerk_id}@unknown"),
            name=payload.get("name"),
            avatar_url=payload.get("image_url"),
        )
        db.add(user)
        await db.flush()  # Get the ID without committing

    return CurrentUser(user=user, clerk_payload=payload)


# Type alias for cleaner route signatures
AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


# =============================================================================
# Optional Auth (for public endpoints)
# =============================================================================


async def get_optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser | None:
    """
    Like get_current_user, but returns None instead of raising if not authenticated.
    """
    if not credentials:
        return None

    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


OptionalUser = Annotated[CurrentUser | None, Depends(get_optional_user)]


# =============================================================================
# Tier Checks
# =============================================================================


def require_tier(*allowed_tiers: str):
    """
    Dependency factory that requires the user to be on one of the allowed tiers.

    Usage:
        @router.post("/wasm", dependencies=[Depends(require_tier("pro", "enterprise"))])
    """
    async def check_tier(user: AuthenticatedUser) -> None:
        if user.tier not in allowed_tiers:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires one of: {', '.join(allowed_tiers)}",
            )

    return check_tier


RequirePro = Depends(require_tier("pro", "enterprise", "high_frequency"))
RequireEnterprise = Depends(require_tier("enterprise", "high_frequency"))
