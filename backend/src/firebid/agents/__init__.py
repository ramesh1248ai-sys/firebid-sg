"""Agents: typed functions that call a gateway route and propose, never commit.

Every agent follows the contract in `base.py` and runs through `runtime.run_agent`, which
makes a re-run a no-op and turns uncertainty into a human task rather than a guess.
"""

from firebid.agents.base import (
    Agent,
    AgentInput,
    AgentResult,
    AgentTool,
    Assertion,
    AutonomyLevel,
    AutonomyRefused,
    ToolRegistry,
)
from firebid.agents.runtime import Escalated, complete_task_and_continue, run_agent

__all__ = [
    "Agent",
    "AgentInput",
    "AgentResult",
    "AgentTool",
    "Assertion",
    "AutonomyLevel",
    "AutonomyRefused",
    "Escalated",
    "ToolRegistry",
    "complete_task_and_continue",
    "run_agent",
]
