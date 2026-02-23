"""Delegate tool for inter-agent task delegation in swarm mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.swarm import SwarmManager


class DelegateTool(Tool):
    """
    Tool to delegate a task to another agent in the swarm.

    The target agent processes the task via its own AgentLoop and
    returns the result synchronously to the calling agent.
    """

    def __init__(self, swarm: SwarmManager, source_agent: str):
        self._swarm = swarm
        self._source = source_agent

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        return (
            "Delegate a task to another agent in the swarm and get the result back. "
            "Use this when a peer agent is better suited for a specific subtask."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the target agent to delegate to",
                },
                "task": {
                    "type": "string",
                    "description": "The task description for the target agent",
                },
            },
            "required": ["agent", "task"],
        }

    async def execute(self, agent: str, task: str, **kwargs: Any) -> str:
        """Delegate a task to the named agent and return the result."""
        target = self._swarm.get_agent(agent)
        if target is None:
            available = ", ".join(self._swarm.agent_names)
            return f"Error: Agent '{agent}' not found. Available agents: {available}"

        if agent == self._source:
            return "Error: Cannot delegate to yourself."

        result = await target.process_direct(
            content=task,
            session_key=f"delegate:{self._source}:{agent}",
            channel="system",
            chat_id=f"delegate:{self._source}",
        )
        return result or "Agent completed the task but returned no response."
