"""
Webhook trigger endpoint.

This handles incoming webhook requests that trigger flows.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from bloxserver.api.models.database import get_db_context
from bloxserver.api.models.tables import (
    ExecutionRecord,
    ExecutionStatus,
    FlowRecord,
    TriggerRecord,
    TriggerType,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{webhook_token}")
async def handle_webhook(
    webhook_token: str,
    request: Request,
) -> dict:
    """
    Handle incoming webhook request.

    This endpoint is public (no auth) - the token IS the authentication.
    """
    async with get_db_context() as db:
        # Look up trigger by token
        query = select(TriggerRecord).where(
            TriggerRecord.webhook_token == webhook_token,
            TriggerRecord.type == TriggerType.WEBHOOK,
        )
        result = await db.execute(query)
        trigger = result.scalar_one_or_none()

        if not trigger:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

        # Get the flow
        flow_query = select(FlowRecord).where(FlowRecord.id == trigger.flow_id)
        flow = (await db.execute(flow_query)).scalar_one_or_none()

        if not flow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Flow not found",
            )

        if flow.status != "running":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Flow is not running (status: {flow.status})",
            )

        # Get request body
        try:
            body = await request.body()
            input_payload = body.decode("utf-8") if body else None
        except Exception:
            input_payload = None

        # Create execution record
        execution = ExecutionRecord(
            flow_id=flow.id,
            trigger_id=trigger.id,
            trigger_type=TriggerType.WEBHOOK,
            status=ExecutionStatus.RUNNING,
            input_payload=input_payload,
        )
        db.add(execution)
        await db.commit()

        # TODO: Actually dispatch to the running container
        # This would send the payload to the flow's container

        return {
            "status": "accepted",
            "executionId": str(execution.id),
            "message": "Webhook received and execution started",
        }


@router.get("/{webhook_token}/test")
async def test_webhook(webhook_token: str) -> dict:
    """
    Test that a webhook token is valid.

    Returns info about the trigger without actually executing.
    """
    async with get_db_context() as db:
        query = select(TriggerRecord).where(
            TriggerRecord.webhook_token == webhook_token,
            TriggerRecord.type == TriggerType.WEBHOOK,
        )
        result = await db.execute(query)
        trigger = result.scalar_one_or_none()

        if not trigger:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

        # Get the flow
        flow_query = select(FlowRecord).where(FlowRecord.id == trigger.flow_id)
        flow = (await db.execute(flow_query)).scalar_one_or_none()

        return {
            "valid": True,
            "triggerName": trigger.name,
            "flowName": flow.name if flow else None,
            "flowStatus": flow.status.value if flow else None,
        }
