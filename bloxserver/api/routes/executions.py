"""
Execution history and manual trigger endpoints.

Executions are immutable records of flow runs.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from bloxserver.api.dependencies import AuthenticatedUser, DbSession
from bloxserver.api.models.tables import (
    ExecutionRecord,
    ExecutionStatus,
    FlowRecord,
    TriggerType,
)
from bloxserver.api.schemas import Execution, ExecutionSummary, PaginatedResponse

router = APIRouter(prefix="/flows/{flow_id}/executions", tags=["executions"])


@router.get("", response_model=PaginatedResponse[ExecutionSummary])
async def list_executions(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
    page: int = 1,
    page_size: int = 50,
    status_filter: ExecutionStatus | None = None,
) -> PaginatedResponse[ExecutionSummary]:
    """List execution history for a flow."""
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

    offset = (page - 1) * page_size

    # Build query
    base_query = select(ExecutionRecord).where(ExecutionRecord.flow_id == flow_id)
    if status_filter:
        base_query = base_query.where(ExecutionRecord.status == status_filter)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Get page
    query = base_query.order_by(ExecutionRecord.started_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    executions = result.scalars().all()

    return PaginatedResponse(
        items=[ExecutionSummary.model_validate(e) for e in executions],
        total=total,
        page=page,
        page_size=page_size,
        has_more=offset + len(executions) < total,
    )


@router.get("/{execution_id}", response_model=Execution)
async def get_execution(
    flow_id: UUID,
    execution_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> Execution:
    """Get details of a single execution."""
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

    # Get execution
    query = select(ExecutionRecord).where(
        ExecutionRecord.id == execution_id,
        ExecutionRecord.flow_id == flow_id,
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    return Execution.model_validate(execution)


@router.post("/run", response_model=Execution, status_code=status.HTTP_201_CREATED)
async def run_flow_manually(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
    input_payload: str | None = None,
) -> Execution:
    """
    Manually trigger a flow execution.

    The flow must be in 'running' state.
    """
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

    if flow.status != "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Flow must be running to execute (current: {flow.status})",
        )

    # Create execution record
    execution = ExecutionRecord(
        flow_id=flow_id,
        trigger_type=TriggerType.MANUAL,
        status=ExecutionStatus.RUNNING,
        input_payload=input_payload,
    )
    db.add(execution)
    await db.flush()

    # TODO: Actually dispatch to the running container
    # For now, just return the execution record

    return Execution.model_validate(execution)


# =============================================================================
# Stats endpoint
# =============================================================================


@router.get("/stats", response_model=dict)
async def get_execution_stats(
    flow_id: UUID,
    user: AuthenticatedUser,
    db: DbSession,
) -> dict:
    """Get execution statistics for a flow."""
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

    # Calculate stats
    stats_query = select(
        func.count().label("total"),
        func.count().filter(ExecutionRecord.status == ExecutionStatus.SUCCESS).label("success"),
        func.count().filter(ExecutionRecord.status == ExecutionStatus.ERROR).label("error"),
        func.avg(ExecutionRecord.duration_ms).label("avg_duration_ms"),
        func.max(ExecutionRecord.started_at).label("last_executed_at"),
    ).where(ExecutionRecord.flow_id == flow_id)

    result = await db.execute(stats_query)
    row = result.one()

    return {
        "flowId": str(flow_id),
        "executionsTotal": row.total or 0,
        "executionsSuccess": row.success or 0,
        "executionsError": row.error or 0,
        "avgDurationMs": float(row.avg_duration_ms) if row.avg_duration_ms else 0,
        "lastExecutedAt": row.last_executed_at.isoformat() if row.last_executed_at else None,
    }
