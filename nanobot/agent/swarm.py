"""SwarmManager — multi-agent routing layer on top of AgentLoop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.delegate import DelegateTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import AgentConfig, Config
    from nanobot.cron.service import CronService
    from nanobot.providers.base import LLMProvider


class _AgentEntry:
    """Internal bookkeeping for a single agent in the swarm."""

    __slots__ = ("name", "config", "bus", "loop", "session_manager", "workspace")

    def __init__(
        self,
        name: str,
        config: "AgentConfig",
        bus: MessageBus,
        loop: AgentLoop,
        session_manager: SessionManager,
        workspace: Path,
    ):
        self.name = name
        self.config = config
        self.bus = bus
        self.loop = loop
        self.session_manager = session_manager
        self.workspace = workspace


class SwarmManager:
    """
    Multi-agent routing layer.

    Wraps multiple vanilla ``AgentLoop`` instances — each with its own
    ``MessageBus``, workspace, and session store — and routes inbound
    messages from the shared bus to the correct agent based on channel
    bindings.

    **Design contract**: This class does NOT modify ``AgentLoop``,
    ``ContextBuilder``, ``MessageBus``, ``ChannelManager``, or any
    other upstream module.  It uses only their public APIs.
    """

    def __init__(
        self,
        config: "Config",
        shared_bus: MessageBus,
        provider: "LLMProvider",
        *,
        cron_service: "CronService | None" = None,
        mcp_servers: dict | None = None,
    ):
        self._config = config
        self._shared_bus = shared_bus
        self._provider = provider
        self._cron_service = cron_service
        self._mcp_servers = mcp_servers or {}

        self._agents: dict[str, _AgentEntry] = {}
        self._route_exact: dict[str, str] = {}   # "channel:chat_id" → agent name
        self._route_channel: dict[str, str] = {}  # "channel" → agent name
        self._default_name: str | None = None
        self._running = False

        self._init_agents()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_agents(self) -> None:
        defaults = self._config.agents.defaults
        base_agents_dir = Path(defaults.workspace).expanduser().parent / "agents"

        for idx, ac in enumerate(self._config.agents.instances):
            name = ac.name or f"agent-{idx}"

            # Per-agent workspace
            if ac.workspace:
                ws = Path(ac.workspace).expanduser()
            else:
                ws = base_agents_dir / name
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "memory").mkdir(exist_ok=True)
            (ws / "skills").mkdir(exist_ok=True)

            # Resolve settings (per-agent overrides fall back to defaults)
            model = ac.model or defaults.model
            max_tokens = ac.max_tokens if ac.max_tokens > 0 else defaults.max_tokens
            temperature = ac.temperature if ac.temperature >= 0 else defaults.temperature
            max_iters = ac.max_tool_iterations if ac.max_tool_iterations > 0 else defaults.max_tool_iterations
            mem_window = ac.memory_window if ac.memory_window > 0 else defaults.memory_window

            # Each agent gets its own bus and session store
            agent_bus = MessageBus()
            session_mgr = SessionManager(ws)

            exec_cfg = self._config.tools.exec

            agent_loop = AgentLoop(
                bus=agent_bus,
                provider=self._provider,
                workspace=ws,
                model=model,
                max_iterations=max_iters,
                temperature=temperature,
                max_tokens=max_tokens,
                memory_window=mem_window,
                brave_api_key=self._config.tools.web.search.api_key or None,
                exec_config=exec_cfg,
                cron_service=self._cron_service if idx == 0 else None,
                restrict_to_workspace=self._config.tools.restrict_to_workspace,
                session_manager=session_mgr,
                mcp_servers=self._mcp_servers if idx == 0 else {},
                channels_config=self._config.channels,
            )

            entry = _AgentEntry(
                name=name,
                config=ac,
                bus=agent_bus,
                loop=agent_loop,
                session_manager=session_mgr,
                workspace=ws,
            )
            self._agents[name] = entry

            # Routing table
            for pattern in ac.channels:
                if ":" in pattern:
                    self._route_exact[pattern] = name
                else:
                    self._route_channel[pattern] = name

            # First agent (or one named "default") is the fallback
            if self._default_name is None or name == "default":
                self._default_name = name

            logger.info(
                "Swarm agent '{}': workspace={}, model={}, channels={}",
                name, ws, model, ac.channels,
            )

        # Inject DelegateTool and write AGENTS.md for each agent
        for entry in self._agents.values():
            entry.loop.tools.register(DelegateTool(swarm=self, source_agent=entry.name))
            self._write_agents_md(entry)

    def _write_agents_md(self, entry: _AgentEntry) -> None:
        """Write an AGENTS.md into the agent's workspace listing all peers."""
        peers = [
            e for e in self._agents.values() if e.name != entry.name
        ]
        if not peers:
            return

        lines = [
            "# Swarm Peers",
            "",
            f'You are agent **{entry.name}**.',
        ]
        if entry.config.description:
            lines.append(f"Your role: {entry.config.description}")
        lines += [
            "",
            "Use the `delegate` tool to ask a peer agent to handle a task.",
            "",
            "| Agent | Description |",
            "|-------|-------------|",
        ]
        for p in peers:
            desc = p.config.description or "(no description)"
            lines.append(f"| {p.name} | {desc} |")

        agents_md = entry.workspace / "AGENTS.md"
        agents_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, msg: InboundMessage) -> _AgentEntry:
        """Resolve the agent that should handle this message."""
        # Exact match: "channel:chat_id"
        exact_key = f"{msg.channel}:{msg.chat_id}"
        name = self._route_exact.get(exact_key)
        if name and name in self._agents:
            return self._agents[name]

        # Channel match
        name = self._route_channel.get(msg.channel)
        if name and name in self._agents:
            return self._agents[name]

        # Fallback
        if self._default_name and self._default_name in self._agents:
            return self._agents[self._default_name]

        # Should never happen (we always have at least one agent)
        raise RuntimeError("No agent available to handle message")

    # ------------------------------------------------------------------
    # Public accessors (used by DelegateTool and CLI)
    # ------------------------------------------------------------------

    def get_agent(self, name: str) -> AgentLoop | None:
        """Get an agent's loop by name."""
        entry = self._agents.get(name)
        return entry.loop if entry else None

    @property
    def agent_names(self) -> list[str]:
        """List of registered agent names."""
        return list(self._agents.keys())

    @property
    def default_agent(self) -> AgentLoop | None:
        """The fallback/default agent loop."""
        if self._default_name:
            entry = self._agents.get(self._default_name)
            return entry.loop if entry else None
        return None

    def get_entries(self) -> list[_AgentEntry]:
        """All agent entries (for CLI status display)."""
        return list(self._agents.values())

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main event loop.

        1. Starts each agent's ``AgentLoop.run()`` (consuming from its private bus).
        2. Routes inbound messages from the shared bus to the correct agent's bus.
        3. Collects outbound messages from all agent buses and republishes
           to the shared bus for ``ChannelManager`` to dispatch.
        """
        self._running = True
        logger.info("Swarm started with {} agents: {}", len(self._agents), self.agent_names)

        # Launch per-agent loops and outbound collectors
        tasks: list[asyncio.Task] = []
        for entry in self._agents.values():
            tasks.append(asyncio.create_task(entry.loop.run()))
            tasks.append(asyncio.create_task(self._collect_outbound(entry)))

        # Inbound router
        tasks.append(asyncio.create_task(self._route_inbound()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def _route_inbound(self) -> None:
        """Consume from the shared bus and publish to the correct agent's bus."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._shared_bus.consume_inbound(),
                    timeout=1.0,
                )
                entry = self.route(msg)
                logger.debug("Routing {}:{} → agent '{}'", msg.channel, msg.chat_id, entry.name)
                await entry.bus.publish_inbound(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _collect_outbound(self, entry: _AgentEntry) -> None:
        """Collect outbound messages from an agent's bus and republish to the shared bus."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    entry.bus.consume_outbound(),
                    timeout=1.0,
                )
                await self._shared_bus.publish_outbound(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Stop the swarm and all agent loops."""
        self._running = False
        for entry in self._agents.values():
            entry.loop.stop()
        logger.info("Swarm stopping")

    async def close_mcp(self) -> None:
        """Close MCP connections for all agents."""
        for entry in self._agents.values():
            await entry.loop.close_mcp()
