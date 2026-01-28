"""
BudgetWarning — System alerts for token budget thresholds.

When a thread approaches its token budget limit, the system injects
BudgetWarning messages to give agents a chance to wrap up gracefully.

Thresholds:
- 75%: Early warning - consider wrapping up
- 90%: Critical warning - finish current task
- 95%: Final warning - immediate action required

Agents can design contingencies:
- Summarize progress and respond early
- Escalate to a supervisor agent
- Save state for continuation
- Request budget increase (if supported)

Example handler pattern:
    async def my_agent(payload, metadata):
        if isinstance(payload, BudgetWarning):
            if payload.severity == "critical":
                # Wrap up immediately
                return HandlerResponse.respond(
                    payload=Summary(progress="Reached 90% budget, stopping here...")
                )
            # Otherwise note it and continue
            ...
"""

# Note: Do NOT use `from __future__ import annotations` here
# as it breaks the xmlify decorator which needs concrete types

from dataclasses import dataclass
from third_party.xmlable import xmlify


@xmlify
@dataclass
class BudgetWarning:
    """
    System warning about token budget consumption.

    Sent to agents when their thread crosses budget thresholds.

    Attributes:
        percent_used: Current percentage of budget consumed (0-100)
        tokens_used: Total tokens consumed so far
        tokens_remaining: Tokens remaining before exhaustion
        max_tokens: Total budget for this thread
        severity: Warning level (warning, critical, final)
        message: Human-readable description
    """
    percent_used: float
    tokens_used: int
    tokens_remaining: int
    max_tokens: int
    severity: str  # "warning" (75%), "critical" (90%), "final" (95%)
    message: str


# Default thresholds (can be configured)
DEFAULT_THRESHOLDS = {
    75: "warning",    # 75% - early warning
    90: "critical",   # 90% - wrap up soon
    95: "final",      # 95% - last chance
}
