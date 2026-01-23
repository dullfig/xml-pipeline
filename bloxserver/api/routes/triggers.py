"""
Trigger CRUD endpoints.

Triggers define how flows are started: webhook, schedule, or manual.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from bloxserver.api.dependencies import AuthenticatedUser, DbSession
from bloxserver.api.models.tables import FlowRecord, TriggerRecord, TriggerType
from bloxserver.api.schemas import CreateTriggerRequest, Trigger

router = APIRouter(prefix="/flows/{flow_id}/triggers", tags=["triggers"])

# Base URL for webhooks (configured via environment)
import os
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://api.openblox.ai/webhooks")


def generate_webhook_token() -> str:
    """Generate a secure random token for webhook URLs."""
    return secrets.token_urlsafe(32)


@router.get("", response_model=list[Trigger])
async def list_triggers(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> list[Trigger]:
    """List all triggers for a flow."""
    # Verify flow ownership
    flow_query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    flow = (await db.execute(flow_query)).scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Get triggers
    query = select(TriggerRecord).where(TriggerRecord.flow_id == flow_id)
    result = await db.execute(query)
    triggers = result.scalars().all()

    return [Trigger.model_validate(t) for t in triggers]


@router.post("", response_model=Trigger, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
    request: CreateTriggerRequest,
) -> Trigger:
    """Create a new trigger for a flow."""
    # Verify flow ownership
    flow_query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    flow = (await db.execute(flow_query)).scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Create trigger
    trigger = TriggerRecord(
        flow_id=flow_id,
        type=TriggerType(request.type.value),
        name=request.name,
        config=request.config,
    )

    # Generate webhook URL for webhook triggers
    if request.type == TriggerType.WEBHOOK:
        trigger.webhook_token = generate_webhook_token()
        trigger.webhook_url = f"{WEBHOOK_BASE_URL}/{trigger.webhook_token}"

    db.add(trigger)
    await db.flush()

    return Trigger.model_validate(trigger)


@router.get("/{trigger_id}", response_model=Trigger)
async def get_trigger(
    flow_id: UUID,
    trigger_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Trigger:
    """Get a single trigger by ID."""
    # Verify flow ownership
    flow_query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    flow = (await db.execute(flow_query)).scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Get trigger
    query = select(TriggerRecord).where(
        TriggerRecord.id == trigger_id,
        TriggerRecord.flow_id == flow_id,
    )
    result = await db.execute(query)
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trigger not found",
        )

    return Trigger.model_validate(trigger)


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    flow_id: UUID,
    trigger_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> None:
    """Delete a trigger."""
    # Verify flow ownership
    flow_query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    flow = (await db.execute(flow_query)).scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Get and delete trigger
    query = select(TriggerRecord).where(
        TriggerRecord.id == trigger_id,
        TriggerRecord.flow_id == flow_id,
    )
    result = await db.execute(query)
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trigger not found",
        )

    await db.delete(trigger)


@router.post("/{trigger_id}/regenerate-token", response_model=Trigger)
async def regenerate_webhook_token(
    flow_id: UUID,
    trigger_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Trigger:
    """Regenerate the webhook token for a webhook trigger."""
    # Verify flow ownership
    flow_query = select(FlowRecord).where(
        FlowRecord.id == flow_id,
        FlowRecord.user_id == user.id,
    )
    flow = (await db.execute(flow_query)).scalar_one_or_none()

    if not flow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flow not found",
        )

    # Get trigger
    query = select(TriggerRecord).where(
        TriggerRecord.id == trigger_id,
        TriggerRecord.flow_id == flow_id,
    )
    result = await db.execute(query)
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trigger not found",
        )

    if trigger.type != TriggerType.WEBHOOK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only regenerate token for webhook triggers",
        )

    # Regenerate
    trigger.webhook_token = generate_webhook_token()
    trigger.webhook_url = f"{WEBHOOK_BASE_URL}/{trigger.webhook_token}"

    await db.flush()
    return Trigger.model_validate(trigger)
