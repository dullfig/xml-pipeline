"""
Flow CRUD endpoints.

Flows are the core entity - a user's workflow definition.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from bloxserver.api.dependencies import AuthenticatedUser, DbSession
from bloxserver.api.models.tables import FlowRecord, Tier
from bloxserver.api.schemas import (
    CreateFlowRequest,
    Flow,
    FlowSummary,
    PaginatedResponse,
    UpdateFlowRequest,
)

router = APIRouter(prefix="/flows", tags=["flows"])

# Default organism.yaml template for new flows
DEFAULT_ORGANISM_YAML = """organism:
  name: my-flow

listeners:
  - name: greeter
    payload_class: handlers.hello.Greeting
    handler: handlers.hello.handle_greeting
    description: A friendly greeter agent
    agent: true
    peers: []
"""

# Tier limits
TIER_FLOW_LIMITS = {
    Tier.FREE: 1,
    Tier.PRO: 100,  # Effectively unlimited for most users
    Tier.ENTERPRISE: 1000,
    Tier.HIGH_FREQUENCY: 1000,
}


@router.get("", response_model=PaginatedResponse[FlowSummary])
async def list_flows(
    user: AuthenticatedUser,
    db: DbSession,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse[FlowSummary]:
    """List all flows for the current user."""
    offset = (page - 1) * page_size

    # Get total count
    count_query = select(func.count()).select_from(FlowRecord).where(
        FlowRecord.user_id == user.id
    )
    total = (await db.execute(count_query)).scalar() or 0

    # Get page of flows
    query = (
        select(FlowRecord)
        .where(FlowRecord.user_id == user.id)
        .order_by(FlowRecord.updated_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    flows = result.scalars().all()

    return PaginatedResponse(
        items=[FlowSummary.model_validate(f) for f in flows],
        total=total,
        page=page,
        page_size=page_size,
        has_more=offset + len(flows) < total,
    )


@router.post("", response_model=Flow, status_code=status.HTTP_201_CREATED)
async def create_flow(
    user: AuthenticatedUser,
    db: DbSession,
    request: CreateFlowRequest,
) -> Flow:
    """Create a new flow."""
    # Check tier limits
    count_query = select(func.count()).select_from(FlowRecord).where(
        FlowRecord.user_id == user.id
    )
    current_count = (await db.execute(count_query)).scalar() or 0
    limit = TIER_FLOW_LIMITS.get(user.user.tier, 1)

    if current_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Flow limit reached ({limit}). Upgrade to create more flows.",
        )

    # Create flow
    flow = FlowRecord(
        user_id=user.id,
        name=request.name,
        description=request.description,
        organism_yaml=request.organism_yaml or DEFAULT_ORGANISM_YAML,
    )
    db.add(flow)
    await db.flush()

    return Flow.model_validate(flow)


@router.get("/{flow_id}", response_model=Flow)
async def get_flow(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Flow:
    """Get a single flow by ID."""
    query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    result = await db.execute(query)
    flow = result.scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    return Flow.model_validate(flow)


@router.patch("/{flow_id}", response_model=Flow)
async def update_flow(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
    request: UpdateFlowRequest,
) -> Flow:
    """Update a flow."""
    query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    result = await db.execute(query)
    flow = result.scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Update fields that were provided
    if request.name is not None:
        flow.name = request.name
    if request.description is not None:
        flow.description = request.description
    if request.organism_yaml is not None:
        flow.organism_yaml = request.organism_yaml
    if request.canvas_state is not None:
        flow.canvas_state = request.canvas_state.model_dump()

    await db.flush()
    return Flow.model_validate(flow)


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> None:
    """Delete a flow."""
    query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    result = await db.execute(query)
    flow = result.scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    await db.delete(flow)


# =============================================================================
# Flow Actions (Start/Stop)
# =============================================================================


@router.post("/{flow_id}/start", response_model=Flow)
async def start_flow(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Flow:
    """Start a flow (deploy container)."""
    query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    result = await db.execute(query)
    flow = result.scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    if flow.status not in ("stopped", "error"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start flow in {flow.status} state",
        )

    # TODO: Actually start the container
    # This is where we'd call the container orchestration layer
    # For now, just update the status
    flow.status = "starting"
    flow.error_message = None

    await db.flush()
    return Flow.model_validate(flow)


@router.post("/{flow_id}/stop", response_model=Flow)
async def stop_flow(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Flow:
    """Stop a running flow."""
    query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    result = await db.execute(query)
    flow = result.scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    if flow.status not in ("running", "starting", "error"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot stop flow in {flow.status} state",
        )

    # TODO: Actually stop the container
    flow.status = "stopping"

    await db.flush()
    return Flow.model_validate(flow)
