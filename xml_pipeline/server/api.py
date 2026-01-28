"""
api.py — REST API routes for AgentServer.

Provides endpoints for:
- Organism info and config
- Agent listing and details
- Thread listing and management
- Message injection
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, HTTPException, Query

from xml_pipeline.server.models import (
    AgentInfo,
    AgentListResponse,
    AgentUsageInfo,
    CapabilityDetail,
    CapabilityInfo,
    CapabilityListResponse,
    ErrorResponse,
    InjectRequest,
    InjectResponse,
    MessageListResponse,
    ModelUsageInfo,
    OrganismInfo,
    ThreadBudgetInfo,
    ThreadBudgetListResponse,
    ThreadInfo,
    ThreadListResponse,
    ThreadStatus,
    UsageOverview,
    UsageResponse,
    UsageTotals,
)

if TYPE_CHECKING:
    from xml_pipeline.server.state import ServerState


def create_router(state: "ServerState") -> APIRouter:
    """Create API router with state dependency."""
    router = APIRouter(prefix="/api/v1")

    # =========================================================================
    # Organism Endpoints
    # =========================================================================

    @router.get("/organism", response_model=OrganismInfo)
    async def get_organism() -> OrganismInfo:
        """Get organism overview and stats."""
        return state.get_organism_info()

    @router.get("/organism/config")
    async def get_organism_config() -> dict:
        """Get sanitized organism configuration (no secrets)."""
        return state.get_organism_config()

    # =========================================================================
    # Capability Introspection Endpoints (for operators, not agents)
    # =========================================================================

    @router.get("/capabilities", response_model=CapabilityListResponse)
    async def list_capabilities() -> CapabilityListResponse:
        """
        List all registered capabilities in the organism.

        This endpoint is for operator introspection only.
        Agents cannot access this - they only know their declared peers.
        """
        capabilities = state.get_capabilities()
        return CapabilityListResponse(
            capabilities=capabilities,
            count=len(capabilities),
        )

    @router.get("/capabilities/{name}", response_model=CapabilityDetail)
    async def get_capability(name: str) -> CapabilityDetail:
        """
        Get detailed capability info including schema and example.

        This endpoint is for operator introspection only.
        """
        capability = state.get_capability(name)
        if capability is None:
            raise HTTPException(
                status_code=404,
                detail=f"Capability not found: {name}",
            )
        return capability

    # =========================================================================
    # Agent Endpoints
    # =========================================================================

    @router.get("/agents", response_model=AgentListResponse)
    async def list_agents() -> AgentListResponse:
        """List all agents with current state."""
        agents = state.get_agents()
        return AgentListResponse(agents=agents, count=len(agents))

    @router.get("/agents/{name}", response_model=AgentInfo)
    async def get_agent(name: str) -> AgentInfo:
        """Get single agent details."""
        agent = state.get_agent(name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {name}")
        return agent

    @router.get("/agents/{name}/config")
    async def get_agent_config(name: str) -> dict:
        """Get agent's YAML config section."""
        agent = state.get_agent(name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

        # Return relevant config fields
        return {
            "name": agent.name,
            "description": agent.description,
            "isAgent": agent.is_agent,
            "peers": agent.peers,
            "payloadClass": agent.payload_class,
        }

    @router.get("/agents/{name}/schema")
    async def get_agent_schema(name: str) -> dict:
        """Get agent's payload XML schema."""
        schema = state.get_agent_schema(name)
        if schema is None:
            raise HTTPException(
                status_code=404,
                detail=f"Schema not found for agent: {name}",
            )
        return {"schema": schema, "contentType": "application/xml"}

    # =========================================================================
    # Thread Endpoints
    # =========================================================================

    @router.get("/threads", response_model=ThreadListResponse)
    async def list_threads(
        status: Optional[str] = Query(None, description="Filter by status"),
        agent: Optional[str] = Query(None, description="Filter by participant agent"),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> ThreadListResponse:
        """List threads with optional filtering."""
        thread_status = None
        if status:
            try:
                thread_status = ThreadStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}. Valid values: {[s.value for s in ThreadStatus]}",
                )

        threads, total = state.get_threads(
            status=thread_status,
            agent=agent,
            limit=limit,
            offset=offset,
        )
        return ThreadListResponse(
            threads=threads,
            count=len(threads),
            total=total,
            offset=offset,
            limit=limit,
        )

    @router.get("/threads/{thread_id}", response_model=ThreadInfo)
    async def get_thread(thread_id: str) -> ThreadInfo:
        """Get thread details with message history."""
        thread = state.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
        return thread

    @router.get("/threads/{thread_id}/messages", response_model=MessageListResponse)
    async def get_thread_messages(
        thread_id: str,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> MessageListResponse:
        """Get messages in a specific thread."""
        thread = state.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

        messages, total = state.get_messages(
            thread_id=thread_id,
            limit=limit,
            offset=offset,
        )
        return MessageListResponse(
            messages=messages,
            count=len(messages),
            total=total,
            offset=offset,
            limit=limit,
        )

    @router.post("/threads/{thread_id}/kill")
    async def kill_thread(thread_id: str) -> dict:
        """Terminate a thread."""
        thread = state.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")

        await state.complete_thread(thread_id, status=ThreadStatus.KILLED)
        return {"success": True, "threadId": thread_id}

    # =========================================================================
    # Message Endpoints
    # =========================================================================

    @router.get("/messages", response_model=MessageListResponse)
    async def list_messages(
        agent: Optional[str] = Query(None, description="Filter by agent (sender or receiver)"),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> MessageListResponse:
        """Get global message history."""
        messages, total = state.get_messages(
            agent=agent,
            limit=limit,
            offset=offset,
        )
        return MessageListResponse(
            messages=messages,
            count=len(messages),
            total=total,
            offset=offset,
            limit=limit,
        )

    # =========================================================================
    # Usage/Gas Tracking Endpoints
    # =========================================================================

    @router.get("/usage", response_model=UsageResponse)
    async def get_usage() -> UsageResponse:
        """
        Get usage overview (gas gauge).

        Returns aggregate token usage, costs, and per-agent/model breakdowns.
        This is the main endpoint for monitoring LLM consumption.
        """
        from xml_pipeline.llm import get_usage_tracker
        from xml_pipeline.message_bus import get_budget_registry

        tracker = get_usage_tracker()
        budget_registry = get_budget_registry()

        # Get aggregate totals
        totals_dict = tracker.get_totals()
        totals = UsageTotals(
            total_tokens=totals_dict["total_tokens"],
            prompt_tokens=totals_dict["prompt_tokens"],
            completion_tokens=totals_dict["completion_tokens"],
            request_count=totals_dict["request_count"],
            total_cost=totals_dict["total_cost"],
            avg_latency_ms=totals_dict["avg_latency_ms"],
        )

        # Get per-agent breakdown
        agent_totals = tracker.get_all_agent_totals()
        by_agent = [
            AgentUsageInfo(
                agent_id=agent_id,
                total_tokens=data["total_tokens"],
                prompt_tokens=data["prompt_tokens"],
                completion_tokens=data["completion_tokens"],
                request_count=data["request_count"],
                total_cost=data["total_cost"],
            )
            for agent_id, data in agent_totals.items()
        ]

        # Get per-model breakdown
        model_totals = tracker.get_all_model_totals()
        by_model = [
            ModelUsageInfo(
                model=model,
                total_tokens=data["total_tokens"],
                prompt_tokens=data["prompt_tokens"],
                completion_tokens=data["completion_tokens"],
                request_count=data["request_count"],
                total_cost=data["total_cost"],
            )
            for model, data in model_totals.items()
        ]

        # Count active threads with budgets
        all_budgets = budget_registry.get_all_usage()
        active_threads = len(all_budgets)

        overview = UsageOverview(
            totals=totals,
            by_agent=by_agent,
            by_model=by_model,
            active_threads=active_threads,
        )

        return UsageResponse(usage=overview)

    @router.get("/usage/threads", response_model=ThreadBudgetListResponse)
    async def get_thread_budgets() -> ThreadBudgetListResponse:
        """
        Get token budgets for all active threads.

        Shows remaining budget per thread for monitoring runaway agents.
        """
        from xml_pipeline.message_bus import get_budget_registry

        registry = get_budget_registry()
        all_budgets = registry.get_all_usage()

        threads = []
        for thread_id, budget_dict in all_budgets.items():
            max_tokens = budget_dict["max_tokens"]
            total = budget_dict["total_tokens"]
            percent = (total / max_tokens * 100) if max_tokens > 0 else 0

            threads.append(
                ThreadBudgetInfo(
                    thread_id=thread_id,
                    max_tokens=max_tokens,
                    prompt_tokens=budget_dict["prompt_tokens"],
                    completion_tokens=budget_dict["completion_tokens"],
                    total_tokens=total,
                    remaining=budget_dict["remaining"],
                    percent_used=round(percent, 1),
                    is_exhausted=budget_dict["remaining"] <= 0,
                )
            )

        # Sort by percent used (descending) - hottest threads first
        threads.sort(key=lambda t: t.percent_used, reverse=True)

        return ThreadBudgetListResponse(
            threads=threads,
            count=len(threads),
            default_max_tokens=registry._max_tokens_per_thread,
        )

    @router.get("/usage/threads/{thread_id}", response_model=ThreadBudgetInfo)
    async def get_thread_budget(thread_id: str) -> ThreadBudgetInfo:
        """Get token budget for a specific thread."""
        from xml_pipeline.message_bus import get_budget_registry

        registry = get_budget_registry()
        budget_dict = registry.get_usage(thread_id)

        if budget_dict is None:
            raise HTTPException(
                status_code=404,
                detail=f"No budget found for thread: {thread_id}",
            )

        max_tokens = budget_dict["max_tokens"]
        total = budget_dict["total_tokens"]
        percent = (total / max_tokens * 100) if max_tokens > 0 else 0

        return ThreadBudgetInfo(
            thread_id=thread_id,
            max_tokens=max_tokens,
            prompt_tokens=budget_dict["prompt_tokens"],
            completion_tokens=budget_dict["completion_tokens"],
            total_tokens=total,
            remaining=budget_dict["remaining"],
            percent_used=round(percent, 1),
            is_exhausted=budget_dict["remaining"] <= 0,
        )

    @router.get("/usage/agents/{agent_id}")
    async def get_agent_usage(agent_id: str) -> AgentUsageInfo:
        """Get usage totals for a specific agent."""
        from xml_pipeline.llm import get_usage_tracker

        tracker = get_usage_tracker()
        data = tracker.get_agent_totals(agent_id)

        return AgentUsageInfo(
            agent_id=agent_id,
            total_tokens=data["total_tokens"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            request_count=data["request_count"],
            total_cost=data["total_cost"],
        )

    @router.get("/usage/models/{model}")
    async def get_model_usage(model: str) -> ModelUsageInfo:
        """Get usage totals for a specific model."""
        from xml_pipeline.llm import get_usage_tracker

        tracker = get_usage_tracker()
        data = tracker.get_model_totals(model)

        return ModelUsageInfo(
            model=model,
            total_tokens=data["total_tokens"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            request_count=data["request_count"],
            total_cost=data["total_cost"],
        )

    @router.post("/usage/reset")
    async def reset_usage() -> dict:
        """
        Reset all usage tracking (for testing/development).

        WARNING: This clears all usage history. Use with caution.
        """
        from xml_pipeline.llm import reset_usage_tracker

        reset_usage_tracker()
        return {"success": True, "message": "Usage tracking reset"}

    # =========================================================================
    # Control Endpoints
    # =========================================================================

    @router.post("/inject", response_model=InjectResponse)
    async def inject_message(request: InjectRequest) -> InjectResponse:
        """Inject a message to an agent."""
        # Validate target exists
        agent = state.get_agent(request.to)
        if agent is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown target agent: {request.to}",
            )

        # Generate or use provided thread ID
        thread_id = request.thread_id or str(uuid.uuid4())

        # Build payload XML from dict
        # For now, we construct a simple wrapper
        payload_type = next(iter(request.payload.keys()), "Payload")

        # Record the message
        msg_id = await state.record_message(
            thread_id=thread_id,
            from_id="api",
            to_id=request.to,
            payload_type=payload_type,
            payload=request.payload,
        )

        # TODO: Actually inject into pump queue
        # This requires building an envelope and calling pump.inject()

        return InjectResponse(thread_id=thread_id, message_id=msg_id)

    @router.post("/agents/{name}/pause")
    async def pause_agent(name: str) -> dict:
        """Pause an agent (stop processing new messages)."""
        agent = state.get_agent(name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

        from xml_pipeline.server.models import AgentState

        await state.update_agent_state(name, AgentState.PAUSED)
        return {"success": True, "agent": name, "state": "paused"}

    @router.post("/agents/{name}/resume")
    async def resume_agent(name: str) -> dict:
        """Resume a paused agent."""
        agent = state.get_agent(name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

        from xml_pipeline.server.models import AgentState

        await state.update_agent_state(name, AgentState.IDLE)
        return {"success": True, "agent": name, "state": "idle"}

    @router.post("/organism/reload")
    async def reload_config() -> dict:
        """
        Hot-reload organism configuration.

        Re-reads organism.yaml and updates listeners:
        - New listeners are registered
        - Removed listeners are unregistered
        - Changed listeners are updated
        """
        result = await state.reload_config()
        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Reload failed"),
            )
        return result

    @router.post("/organism/stop")
    async def stop_organism() -> dict:
        """Graceful shutdown."""
        state.set_stopping()
        # TODO: Signal pump to stop
        return {"success": True, "status": "stopping"}

    return router
