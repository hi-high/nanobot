# Nanobot Architecture — Reimplementation Guide

This document provides a comprehensive technical description of the nanobot codebase (~3,900 lines of Python) intended for someone reimplementing the system in another language. It covers every subsystem, data structure, algorithm, and integration point.

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Directory Structure](#2-directory-structure)
3. [Configuration System](#3-configuration-system)
4. [Message Bus](#4-message-bus)
5. [Agent Loop (Core Engine)](#5-agent-loop-core-engine)
6. [Context Builder (Prompt Assembly)](#6-context-builder-prompt-assembly)
7. [Tool System](#7-tool-system)
8. [LLM Provider Abstraction](#8-llm-provider-abstraction)
9. [Session Management](#9-session-management)
10. [Memory System](#10-memory-system)
11. [Skills System](#11-skills-system)
12. [Channel System](#12-channel-system)
13. [Subagent System](#13-subagent-system)
14. [Cron (Scheduled Tasks)](#14-cron-scheduled-tasks)
15. [Heartbeat Service](#15-heartbeat-service)
16. [CLI Interface](#16-cli-interface)
17. [WhatsApp Bridge (Node.js)](#17-whatsapp-bridge-nodejs)
18. [Data Flow Diagrams](#18-data-flow-diagrams)
19. [Key Design Decisions](#19-key-design-decisions)

---

## 1. High-Level Overview

Nanobot is a **personal AI assistant framework** that connects an LLM-powered agent to multiple chat platforms (Telegram, Discord, WhatsApp, Slack, Feishu, DingTalk, Email, QQ, Mochat). The architecture follows a **hub-and-spoke** pattern:

```
Chat Channels ──► Message Bus ──► Agent Loop ──► LLM Provider
  (spokes)          (hub)         (core)         (external)
                                    │
                                    ▼
                              Tool Registry
                        (filesystem, shell, web,
                         cron, message, spawn, MCP)
```

**Core flow:**
1. A chat channel receives a user message and publishes it to the **inbound** message bus queue
2. The **agent loop** consumes the message, builds context (system prompt + history + memory + skills), and calls the LLM
3. If the LLM returns **tool calls**, the agent executes them and loops back to the LLM with results
4. When the LLM returns a **text response** (no tool calls), the agent publishes it to the **outbound** queue
5. The **channel manager** dispatches outbound messages to the correct channel for delivery

There are two operating modes:
- **Gateway mode** (`nanobot gateway`): Runs the agent loop + all enabled channels + cron + heartbeat as a long-running server
- **CLI mode** (`nanobot agent`): Runs the agent loop directly without the bus, for interactive or one-shot usage

---

## 2. Directory Structure

```
nanobot/                          # Python package root
├── __init__.py                   # Version (__version__) and logo (__logo__)
├── __main__.py                   # Entry: python -m nanobot
├── cli/
│   └── commands.py               # Typer CLI: onboard, agent, gateway, cron, status, provider login
├── config/
│   ├── loader.py                 # Load/save ~/.nanobot/config.json, migration
│   └── schema.py                 # Pydantic models for all config (camelCase ↔ snake_case)
├── bus/
│   ├── events.py                 # InboundMessage, OutboundMessage dataclasses
│   └── queue.py                  # MessageBus: two async queues (inbound + outbound)
├── agent/
│   ├── loop.py                   # AgentLoop: the core LLM ↔ tool execution engine
│   ├── context.py                # ContextBuilder: prompt assembly (bootstrap, memory, skills)
│   ├── memory.py                 # MemoryStore: two-layer persistent memory (MEMORY.md + HISTORY.md)
│   ├── skills.py                 # SkillsLoader: progressive skill loading from markdown files
│   ├── subagent.py               # SubagentManager: background task execution
│   └── tools/
│       ├── base.py               # Tool ABC: name, description, parameters, execute(), validate_params()
│       ├── registry.py           # ToolRegistry: register/execute tools, get OpenAI-format definitions
│       ├── filesystem.py         # ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
│       ├── shell.py              # ExecTool: shell command execution with safety guards
│       ├── web.py                # WebSearchTool (Brave API), WebFetchTool (Readability extraction)
│       ├── message.py            # MessageTool: send messages to chat channels
│       ├── spawn.py              # SpawnTool: delegate tasks to background subagents
│       ├── cron.py               # CronTool: schedule/list/remove recurring tasks
│       └── mcp.py                # MCPToolWrapper + connect_mcp_servers: MCP protocol client
├── providers/
│   ├── base.py                   # LLMProvider ABC, LLMResponse, ToolCallRequest dataclasses
│   ├── registry.py               # ProviderSpec dataclass + PROVIDERS tuple (all provider metadata)
│   ├── litellm_provider.py       # LiteLLMProvider: multi-provider via litellm library
│   ├── custom_provider.py        # CustomProvider: direct OpenAI-compatible API (bypasses litellm)
│   ├── openai_codex_provider.py  # OpenAICodexProvider: Codex Responses API via OAuth + SSE
│   └── transcription.py          # GroqTranscriptionProvider: Whisper voice-to-text via Groq
├── channels/
│   ├── base.py                   # BaseChannel ABC: start(), stop(), send(), is_allowed()
│   ├── manager.py                # ChannelManager: init channels, start/stop, outbound dispatch
│   ├── telegram.py               # Telegram (python-telegram-bot, long-polling)
│   ├── discord.py                # Discord (raw WebSocket gateway, no discord.py)
│   ├── whatsapp.py               # WhatsApp (WebSocket to Node.js bridge)
│   ├── feishu.py                 # Feishu/Lark (lark-oapi SDK, WebSocket)
│   ├── dingtalk.py               # DingTalk (dingtalk-stream SDK)
│   ├── slack.py                  # Slack (slack-sdk, Socket Mode)
│   ├── email.py                  # Email (IMAP polling + SMTP sending)
│   ├── mochat.py                 # Mochat/Claw IM (Socket.IO)
│   └── qq.py                     # QQ (botpy SDK, WebSocket)
├── session/
│   └── manager.py                # SessionManager + Session dataclass (JSONL persistence)
├── cron/
│   ├── types.py                  # CronJob, CronSchedule, CronPayload, CronJobState, CronStore
│   └── service.py                # CronService: job scheduling, timer-based execution
├── heartbeat/
│   └── service.py                # HeartbeatService: periodic HEARTBEAT.md check
├── skills/                       # Bundled skill markdown files (github, weather, tmux, etc.)
├── templates/                    # Workspace template files (SOUL.md, TOOLS.md, HEARTBEAT.md, etc.)
└── utils/
    └── helpers.py                # ensure_dir, safe_filename, get_workspace_path, etc.

bridge/                           # Node.js WhatsApp bridge (TypeScript)
├── src/
│   ├── index.ts                  # Entry point
│   ├── server.ts                 # WebSocket server for nanobot ↔ WhatsApp
│   ├── whatsapp.ts               # whatsapp-web.js client wrapper
│   └── types.d.ts                # Type definitions
├── package.json
└── tsconfig.json
```

---

## 3. Configuration System

### File Location
- **Config**: `~/.nanobot/config.json`
- **Workspace**: `~/.nanobot/workspace/` (configurable via `agents.defaults.workspace`)
- **Cron store**: `~/.nanobot/cron/jobs.json`
- **CLI history**: `~/.nanobot/history/cli_history`

### Schema (Pydantic with camelCase aliasing)

All config models extend a `Base` class that uses Pydantic's `to_camel` alias generator. This means the JSON file uses **camelCase** keys (`apiKey`, `allowFrom`) while Python uses **snake_case** (`api_key`, `allow_from`). Both forms are accepted during deserialization (`populate_by_name=True`).

```
Config (root)
├── agents: AgentsConfig
│   └── defaults: AgentDefaults
│       ├── workspace: str = "~/.nanobot/workspace"
│       ├── model: str = "anthropic/claude-opus-4-5"
│       ├── max_tokens: int = 8192
│       ├── temperature: float = 0.1
│       ├── max_tool_iterations: int = 40
│       └── memory_window: int = 100
├── providers: ProvidersConfig
│   ├── custom: ProviderConfig { api_key, api_base, extra_headers }
│   ├── openrouter: ProviderConfig
│   ├── anthropic: ProviderConfig
│   ├── openai: ProviderConfig
│   ├── deepseek: ProviderConfig
│   ├── groq: ProviderConfig
│   ├── gemini: ProviderConfig
│   ├── zhipu: ProviderConfig
│   ├── dashscope: ProviderConfig
│   ├── moonshot: ProviderConfig
│   ├── minimax: ProviderConfig
│   ├── aihubmix: ProviderConfig
│   ├── siliconflow: ProviderConfig
│   ├── volcengine: ProviderConfig
│   ├── vllm: ProviderConfig
│   ├── openai_codex: ProviderConfig
│   └── github_copilot: ProviderConfig
├── channels: ChannelsConfig
│   ├── send_progress: bool = true
│   ├── send_tool_hints: bool = false
│   ├── telegram: TelegramConfig { enabled, token, allow_from, proxy, reply_to_message }
│   ├── discord: DiscordConfig { enabled, token, allow_from, gateway_url, intents }
│   ├── whatsapp: WhatsAppConfig { enabled, bridge_url, bridge_token, allow_from }
│   ├── feishu: FeishuConfig { enabled, app_id, app_secret, encrypt_key, allow_from }
│   ├── dingtalk: DingTalkConfig { enabled, client_id, client_secret, allow_from }
│   ├── slack: SlackConfig { enabled, bot_token, app_token, group_policy, dm, ... }
│   ├── email: EmailConfig { enabled, consent_granted, imap_*, smtp_*, allow_from, ... }
│   ├── mochat: MochatConfig { enabled, base_url, claw_token, sessions, panels, ... }
│   └── qq: QQConfig { enabled, app_id, secret, allow_from }
├── tools: ToolsConfig
│   ├── web: WebToolsConfig
│   │   └── search: WebSearchConfig { api_key (Brave), max_results }
│   ├── exec: ExecToolConfig { timeout: 60 }
│   ├── restrict_to_workspace: bool = false
│   └── mcp_servers: dict[str, MCPServerConfig]
│       └── MCPServerConfig { command, args, env, url, headers, tool_timeout }
└── gateway: GatewayConfig { host, port }
```

### Provider Matching Algorithm

When the user sets a `model` string (e.g., `"anthropic/claude-opus-4-5"`), the system determines which provider to use:

1. **Explicit prefix match**: If the model contains a `/`, the prefix is checked against each `ProviderSpec.name` (normalized: `github-copilot` → `github_copilot`). First match with a configured API key wins.
2. **Keyword match**: Each `ProviderSpec` has `keywords`. If any keyword appears in the model name (case-insensitive), and that provider has an API key, it wins.
3. **Fallback**: First provider in registry order that has an API key (skipping OAuth providers).

### Config Migration

The `_migrate_config()` function handles one migration:
- `tools.exec.restrictToWorkspace` → `tools.restrictToWorkspace`

### Environment Variables

Config also supports env vars via `pydantic-settings` with prefix `NANOBOT_` and `__` as nested delimiter (e.g., `NANOBOT_AGENTS__DEFAULTS__MODEL`).

---

## 4. Message Bus

**File**: `bus/queue.py`, `bus/events.py`

The message bus is a simple **dual async queue** that decouples channels from the agent:

```python
class MessageBus:
    inbound:  asyncio.Queue[InboundMessage]   # channels → agent
    outbound: asyncio.Queue[OutboundMessage]   # agent → channels
```

### InboundMessage
```python
@dataclass
class InboundMessage:
    channel: str              # "telegram", "discord", "cli", "system"
    sender_id: str            # User identifier
    chat_id: str              # Chat/group identifier
    content: str              # Message text
    timestamp: datetime       # Auto-set to now
    media: list[str]          # File paths (images downloaded by channel)
    metadata: dict[str, Any]  # Channel-specific (e.g., message_id for replies)
    session_key_override: str | None  # Optional thread-scoped session key

    @property
    session_key -> str:       # Returns override or "{channel}:{chat_id}"
```

### OutboundMessage
```python
@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None      # Message ID to reply to
    media: list[str]          # File paths to send as attachments
    metadata: dict[str, Any]  # Includes _progress and _tool_hint flags
```

### Progress Messages

During tool execution, the agent sends **progress messages** (intermediate text) and **tool hints** (e.g., `web_search("query")`) via the outbound bus with special metadata flags:
- `metadata["_progress"] = True` — this is a progress update, not a final response
- `metadata["_tool_hint"] = True` — this is a tool name hint (configurable whether to show)

The channel manager and CLI both check these flags and respect the `send_progress` / `send_tool_hints` config.

---

## 5. Agent Loop (Core Engine)

**File**: `agent/loop.py`

The `AgentLoop` is the brain of the system. It manages the iterative LLM ↔ tool execution cycle.

### Initialization

```python
AgentLoop(
    bus: MessageBus,
    provider: LLMProvider,
    workspace: Path,
    model: str,
    max_iterations: int = 40,      # Max tool call rounds before forced stop
    temperature: float = 0.1,
    max_tokens: int = 4096,
    memory_window: int = 100,       # Max messages from session history
    brave_api_key: str | None,
    exec_config: ExecToolConfig,
    cron_service: CronService | None,
    restrict_to_workspace: bool,
    session_manager: SessionManager,
    mcp_servers: dict | None,
    channels_config: ChannelsConfig,
)
```

On init, it:
1. Creates a `ContextBuilder`, `SessionManager`, `ToolRegistry`, and `SubagentManager`
2. Registers **default tools**: read_file, write_file, edit_file, list_dir, exec, web_search, web_fetch, message, spawn, (optionally) cron
3. MCP tools are connected **lazily** on first message (`_connect_mcp()`)

### Main Loop (`run()`)

```
while running:
    msg = await bus.consume_inbound()  (with 1s timeout to check _running)
    response = await _process_message(msg)
    if response:
        await bus.publish_outbound(response)
```

### Message Processing (`_process_message()`)

1. **System messages** (from subagents): Parse `channel:chat_id` from `msg.chat_id`, route through agent, respond to original channel
2. **Slash commands**: `/new` archives memory and clears session; `/help` returns command list
3. **Memory consolidation trigger**: If unconsolidated messages ≥ `memory_window`, fire background consolidation task (non-blocking)
4. **Set tool context**: Update MessageTool, SpawnTool, CronTool with current channel/chat_id
5. **Build messages**: Load session history, assemble system prompt via ContextBuilder
6. **Run agent loop**: The iterative LLM ↔ tool cycle
7. **Save turn**: Append new messages to session (with truncated tool results, max 500 chars)
8. **Check MessageTool**: If the agent already sent a message via the `message` tool during this turn, return `None` (suppress duplicate final response)

### Agent Iteration Loop (`_run_agent_loop()`)

```python
while iteration < max_iterations:
    response = await provider.chat(messages, tools, model, temperature, max_tokens)

    if response.has_tool_calls:
        # Send progress (intermediate text + tool hints) via on_progress callback
        # Append assistant message with tool_calls to messages
        for tool_call in response.tool_calls:
            result = await tools.execute(tool_call.name, tool_call.arguments)
            # Append tool result to messages
    else:
        # Final response — strip <think>…</think> blocks, return content
        break
```

If `max_iterations` is reached without a non-tool-call response, a fallback message is returned.

### Think Block Stripping

Some models (DeepSeek-R1, etc.) embed `<think>…</think>` blocks in content. These are stripped with regex: `re.sub(r"<think>[\s\S]*?</think>", "", text)`

### Direct Processing (`process_direct()`)

For CLI and cron usage, bypasses the bus:
```python
async def process_direct(content, session_key, channel, chat_id, on_progress) -> str
```

---

## 6. Context Builder (Prompt Assembly)

**File**: `agent/context.py`

The `ContextBuilder` assembles the complete system prompt from multiple sources.

### System Prompt Structure

The system prompt is built by joining these sections with `\n\n---\n\n`:

1. **Identity**: Current time, timezone, runtime info (OS, Python version), workspace path, tool guidelines, memory file locations
2. **Bootstrap files**: Loaded from workspace root — `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `IDENTITY.md` (only if they exist)
3. **Long-term memory**: Contents of `workspace/memory/MEMORY.md` (if non-empty)
4. **Always-loaded skills**: Skills with `always=true` in frontmatter metadata — full content included
5. **Skills summary**: XML listing of all available skills with name, description, path, availability status — the agent loads full content via `read_file` when needed (progressive loading)

### Message Assembly (`build_messages()`)

```python
[
    {"role": "system", "content": system_prompt + "\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"},
    ...history,  # Previous messages from session
    {"role": "user", "content": user_content},  # Current message (may include base64 images)
]
```

### Media Handling

If the inbound message has `media` (file paths from channel downloads), images are base64-encoded and sent as OpenAI-format multimodal content:
```python
[
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,…"}},
    {"type": "text", "text": "user message text"},
]
```

### Tool Result Messages

Added as:
```python
{"role": "tool", "tool_call_id": "call_xxx", "name": "tool_name", "content": "result string"}
```

### Assistant Messages with Tool Calls

```python
{
    "role": "assistant",
    "content": "optional text" | None,
    "tool_calls": [
        {"id": "call_xxx", "type": "function", "function": {"name": "tool_name", "arguments": "{\"key\": \"value\"}"}}
    ],
    "reasoning_content": "optional thinking text"  # For thinking models
}
```

---

## 7. Tool System

### Base Tool Interface

**File**: `agent/tools/base.py`

```python
class Tool(ABC):
    @property name -> str                          # e.g., "read_file"
    @property description -> str                   # Human-readable description
    @property parameters -> dict[str, Any]         # JSON Schema for parameters
    async execute(**kwargs) -> str                  # Execute and return string result
    validate_params(params) -> list[str]            # Validate against JSON Schema
    to_schema() -> dict                            # Convert to OpenAI function-calling format
```

The `validate_params` method walks the JSON Schema and checks types, required fields, enums, min/max values, and string lengths. Returns a list of error strings (empty = valid).

### Tool Registry

**File**: `agent/tools/registry.py`

```python
class ToolRegistry:
    _tools: dict[str, Tool]

    register(tool: Tool)
    unregister(name: str)
    get(name: str) -> Tool | None
    get_definitions() -> list[dict]        # All tools in OpenAI format
    execute(name: str, params: dict) -> str # Validate + execute, with error hints
```

When execution fails (tool not found, validation error, runtime error), the registry appends:
`"\n\n[Analyze the error above and try a different approach.]"` — this guides the LLM to recover.

### Built-in Tools

#### `read_file` — Read file contents
- **Params**: `path: str` (required)
- **Behavior**: Resolves relative paths against workspace. If `restrict_to_workspace` is on, enforces path is within allowed directory. Returns file content as string.

#### `write_file` — Write content to file
- **Params**: `path: str`, `content: str` (both required)
- **Behavior**: Creates parent directories if needed. Writes UTF-8 content.

#### `edit_file` — Search-and-replace in file
- **Params**: `path: str`, `old_text: str`, `new_text: str` (all required)
- **Behavior**: If `old_text` not found, runs a fuzzy match using `difflib.SequenceMatcher` to find the best match and returns a unified diff showing what was expected vs. what was found. If `old_text` appears more than once, returns a warning asking for more context. Replaces only the first occurrence.

#### `list_dir` — List directory contents
- **Params**: `path: str` (required)
- **Behavior**: Returns sorted entries with folder/file emoji prefixes.

#### `exec` — Execute shell commands
- **Params**: `command: str` (required), `working_dir: str` (optional)
- **Behavior**:
  - **Safety guard** (`_guard_command`): Checks command against deny patterns (regex):
    - `rm -rf`, `del /f`, `rmdir /s`, `format`, `mkfs`, `diskpart`, `dd if=`, `> /dev/sd`, `shutdown/reboot/poweroff`, fork bombs
  - If `restrict_to_workspace`: blocks `../` path traversal and absolute paths outside workspace
  - Runs via `asyncio.create_subprocess_shell` with configurable timeout (default 60s)
  - Captures stdout + stderr, truncates output at 10,000 chars
  - Returns exit code on non-zero

#### `web_search` — Web search via Brave Search API
- **Params**: `query: str` (required), `count: int` (optional, 1-10, default 5)
- **Behavior**: Calls `https://api.search.brave.com/res/v1/web/search` with API key in `X-Subscription-Token` header. Returns formatted results with title, URL, description.

#### `web_fetch` — Fetch and extract web page content
- **Params**: `url: str` (required), `extractMode: str` (optional, "markdown"|"text"), `maxChars: int` (optional)
- **Behavior**:
  - Validates URL (must be http/https with valid domain)
  - Follows up to 5 redirects
  - For HTML: uses `readability-lxml` to extract article content, then converts to markdown (links, headings, lists) or plain text
  - For JSON: pretty-prints
  - Returns JSON envelope: `{url, finalUrl, status, extractor, truncated, length, text}`
  - Default max: 50,000 chars

#### `message` — Send message to chat channel
- **Params**: `content: str` (required), `channel: str`, `chat_id: str`, `media: list[str]` (all optional)
- **Behavior**: Sends via the bus's `publish_outbound`. Tracks `_sent_in_turn` flag — if the agent sends a message via this tool, the agent loop suppresses the final response to avoid duplicates.
- **Context**: `set_context(channel, chat_id, message_id)` is called each turn to set defaults.

#### `spawn` — Background subagent
- **Params**: `task: str` (required), `label: str` (optional)
- **Behavior**: Delegates to `SubagentManager.spawn()`. Returns immediately with a status message. See [Subagent System](#13-subagent-system).

#### `cron` — Schedule tasks
- **Params**: `action: str` ("add"|"list"|"remove"), plus schedule params
- **Behavior**: Wraps `CronService`. See [Cron](#14-cron-scheduled-tasks).

#### MCP Tools — External tool servers
- **Behavior**: Each MCP server tool is wrapped as `MCPToolWrapper` with name `mcp_{server}_{tool}`. Connected lazily on first message via `connect_mcp_servers()`. Supports both stdio (local process) and HTTP (remote endpoint) transports. Per-tool timeout (default 30s).

---

## 8. LLM Provider Abstraction

### Base Interface

**File**: `providers/base.py`

```python
class LLMProvider(ABC):
    async chat(messages, tools, model, max_tokens, temperature) -> LLMResponse
    get_default_model() -> str

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]     # id, name, arguments (dict)
    finish_reason: str                    # "stop", "length", "error"
    usage: dict[str, int]                 # prompt_tokens, completion_tokens, total_tokens
    reasoning_content: str | None         # For thinking models (DeepSeek-R1, Kimi)

    @property has_tool_calls -> bool

@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]
```

All providers implement `_sanitize_empty_content()` which replaces empty string content with `"(empty)"` or `None` (for assistant messages with tool_calls) to avoid 400 errors from providers.

### Provider Registry

**File**: `providers/registry.py`

The registry is a **tuple of `ProviderSpec` dataclasses** — the single source of truth. Each spec describes:

```python
@dataclass(frozen=True)
class ProviderSpec:
    name: str                    # Config field name ("openrouter", "deepseek")
    keywords: tuple[str, ...]    # Model-name matching keywords
    env_key: str                 # LiteLLM env var name
    display_name: str            # Shown in `nanobot status`
    litellm_prefix: str          # Auto-prefix for model names
    skip_prefixes: tuple[str, ...] # Don't double-prefix
    env_extras: tuple[tuple[str, str], ...]  # Additional env vars with {api_key}/{api_base} placeholders
    is_gateway: bool             # Routes any model (OpenRouter, AiHubMix)
    is_local: bool               # Local deployment (vLLM)
    detect_by_key_prefix: str    # API key prefix detection ("sk-or-")
    detect_by_base_keyword: str  # API base URL keyword detection
    default_api_base: str        # Fallback URL
    strip_model_prefix: bool     # Strip "provider/" before re-prefixing
    model_overrides: tuple[...]  # Per-model param overrides
    is_oauth: bool               # Uses OAuth instead of API key
    is_direct: bool              # Bypasses LiteLLM
    supports_prompt_caching: bool # Supports cache_control on content blocks
```

**Registry order** (priority):
1. Custom (direct, bypasses LiteLLM)
2. Gateways: OpenRouter, AiHubMix, SiliconFlow, VolcEngine
3. Standard: Anthropic, OpenAI, OpenAI Codex, GitHub Copilot, DeepSeek, Gemini, Zhipu, DashScope, Moonshot, MiniMax
4. Local: vLLM
5. Auxiliary: Groq

**Lookup functions**:
- `find_by_model(model)` → ProviderSpec: Match by keywords (skips gateways/local)
- `find_gateway(provider_name, api_key, api_base)` → ProviderSpec: Detect gateway by config key, key prefix, or base URL keyword
- `find_by_name(name)` → ProviderSpec: Direct name lookup

### LiteLLM Provider

**File**: `providers/litellm_provider.py`

The main provider that routes to 15+ backends via the `litellm` library.

**Model resolution** (`_resolve_model`):
- If a **gateway** is detected: apply gateway's `litellm_prefix`, optionally `strip_model_prefix`
- Otherwise: find spec by model keywords, apply `litellm_prefix` (e.g., `deepseek-chat` → `deepseek/deepseek-chat`)

**Prompt caching** (`_apply_cache_control`):
For Anthropic and OpenRouter, injects `cache_control: {"type": "ephemeral"}` on system message content blocks and the last tool definition.

**Model overrides** (`_apply_model_overrides`):
Per-model parameter overrides, e.g., Kimi K2.5 requires `temperature >= 1.0`.

**Message sanitization** (`_sanitize_messages`):
Strips non-standard keys (like `reasoning_content`, `timestamp`) to only `_ALLOWED_MSG_KEYS = {role, content, tool_calls, tool_call_id, name}`. Ensures assistant messages always have a `content` key.

**Error handling**: LLM errors are returned as `LLMResponse(content="Error: ...", finish_reason="error")` rather than raising — the agent loop will pass this to the LLM as context on the next iteration.

**Tool argument parsing**: Uses `json_repair` library to handle malformed JSON from LLMs.

### Custom Provider

**File**: `providers/custom_provider.py`

Direct OpenAI-compatible client using `openai.AsyncOpenAI`. Bypasses LiteLLM entirely. Used for `custom` provider config key.

### OpenAI Codex Provider

**File**: `providers/openai_codex_provider.py`

Calls Codex's Responses API (`https://chatgpt.com/backend-api/codex/responses`) via OAuth tokens from `oauth-cli-kit`. Uses SSE streaming. Converts between OpenAI chat format and Codex's input/output item format.

Key conversion:
- Chat messages → Codex `input` items (message, function_call, function_call_output)
- Codex SSE events → LLMResponse with tool_calls
- Tool call IDs use `call_id|item_id` format for roundtripping

### Groq Transcription

**File**: `providers/transcription.py`

Simple HTTP POST to `https://api.groq.com/openai/v1/audio/transcriptions` with `whisper-large-v3` model. Used by Telegram channel for voice messages.

---

## 9. Session Management

**File**: `session/manager.py`

### Session Dataclass

```python
@dataclass
class Session:
    key: str                              # "channel:chat_id" (e.g., "telegram:12345")
    messages: list[dict[str, Any]]        # Full message history (append-only)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
    last_consolidated: int = 0            # Index marking already-consolidated messages
```

**Messages are append-only** for LLM cache efficiency. The memory consolidation system writes summaries to external files but does NOT trim the messages list.

### Persistence Format (JSONL)

Sessions are stored as `.jsonl` files in `workspace/sessions/`:

```
{"_type": "metadata", "key": "telegram:12345", "created_at": "...", "updated_at": "...", "metadata": {}, "last_consolidated": 50}
{"role": "user", "content": "Hello", "timestamp": "2026-02-23T10:00:00"}
{"role": "assistant", "content": "Hi there!", "timestamp": "2026-02-23T10:00:01"}
{"role": "assistant", "content": null, "tool_calls": [...], "timestamp": "..."}
{"role": "tool", "tool_call_id": "call_xxx", "name": "web_search", "content": "Results..."}
```

First line is always metadata. Remaining lines are messages.

### SessionManager

- **In-memory cache**: `_cache: dict[str, Session]` — avoids re-reading from disk
- **Legacy migration**: Checks `~/.nanobot/sessions/` (old path) and moves to `workspace/sessions/`
- **Filename**: session key with `:` replaced by `_`, unsafe chars stripped
- `get_or_create(key)`: Load from cache → disk → create new
- `save(session)`: Overwrite entire JSONL file, update cache
- `invalidate(key)`: Remove from cache (used after `/new` command)
- `list_sessions()`: Read metadata lines from all `.jsonl` files, sorted by `updated_at` descending

### Turn Saving

When a turn completes, new messages are appended to the session with:
- `timestamp` field added to each message
- Tool results truncated to 500 chars (with `... (truncated)` suffix)
- `reasoning_content` stripped from saved messages

---

## 10. Memory System

**File**: `agent/memory.py`

A **two-layer memory system** stored as markdown files:

1. **MEMORY.md** (long-term facts): Structured markdown with key facts about the user, preferences, ongoing projects. Updated by merging new information with existing content.
2. **HISTORY.md** (grep-searchable log): Chronological entries prefixed with `[YYYY-MM-DD HH:MM]`. Each entry is a 2-5 sentence summary of a conversation segment. Append-only.

### Memory Consolidation Algorithm

Triggered when `unconsolidated_messages >= memory_window` (default 100):

1. **Select messages to consolidate**:
   - Normal: Messages from `last_consolidated` to `len(messages) - memory_window/2` (keep recent half)
   - Archive all (`/new` command): All messages
2. **Build consolidation prompt**: Format selected messages as `[timestamp] ROLE: content` lines, prepend current MEMORY.md content
3. **Call LLM** with a special `save_memory` tool:
   ```json
   {
     "name": "save_memory",
     "parameters": {
       "history_entry": "2-5 sentence summary with [YYYY-MM-DD HH:MM] prefix",
       "memory_update": "Full updated MEMORY.md content (existing + new facts)"
     }
   }
   ```
4. **Persist**: Append `history_entry` to HISTORY.md, overwrite MEMORY.md with `memory_update`
5. **Update pointer**: Set `session.last_consolidated` to mark consolidated messages

Consolidation runs as a **background asyncio task** with per-session locking to prevent concurrent consolidation of the same session.

### Memory in Context

On every LLM call, the system prompt includes the current MEMORY.md content under `# Memory > ## Long-term Memory`.

---

## 11. Skills System

**File**: `agent/skills.py`

Skills are **markdown files** (`SKILL.md`) that extend the agent's capabilities by providing instructions, prompts, or tool usage guides.

### Skill Structure

```
workspace/skills/
├── my-skill/
│   └── SKILL.md          # Skill content with optional frontmatter

nanobot/skills/             # Built-in skills (shipped with package)
├── github/
│   └── SKILL.md
├── weather/
│   └── SKILL.md
└── ...
```

### Frontmatter Format

```yaml
---
name: my-skill
description: Does something useful
metadata: {"nanobot": {"always": true, "requires": {"bins": ["git"], "env": ["GITHUB_TOKEN"]}}}
---
# Skill content here...
```

### Progressive Loading Strategy

To avoid bloating the system prompt with all skills:

1. **Always-loaded skills** (`always: true`): Full content included in system prompt
2. **Available skills**: Only an XML summary is included:
   ```xml
   <skills>
     <skill available="true">
       <name>github</name>
       <description>GitHub operations</description>
       <location>/path/to/SKILL.md</location>
     </skill>
     <skill available="false">
       <name>tmux</name>
       <description>Terminal multiplexer</description>
       <location>/path/to/SKILL.md</location>
       <requires>CLI: tmux</requires>
     </skill>
   </skills>
   ```
3. The agent uses `read_file` to load a skill's full content when it needs it

### Requirement Checking

Skills can declare dependencies:
- `bins`: CLI binaries checked via `shutil.which()`
- `env`: Environment variables checked via `os.environ.get()`

Unavailable skills are still listed but marked `available="false"` with missing requirements shown.

### Priority

Workspace skills override built-in skills with the same name.

---

## 12. Channel System

### Base Channel

**File**: `channels/base.py`

```python
class BaseChannel(ABC):
    name: str                                  # Channel identifier
    config: Any                                # Channel-specific config
    bus: MessageBus                            # For publishing inbound messages

    async start()                              # Connect and listen (long-running)
    async stop()                               # Disconnect
    async send(msg: OutboundMessage)           # Deliver a message
    is_allowed(sender_id: str) -> bool         # Check allowFrom list
    async _handle_message(sender_id, chat_id, content, media, metadata, session_key)
```

The `is_allowed()` method:
- If `allow_from` list is empty: allow everyone
- If non-empty: check if `sender_id` is in the list (also handles `|`-separated composite IDs)

The `_handle_message()` method:
1. Check `is_allowed(sender_id)` — log warning and drop if denied
2. Create `InboundMessage` and publish to bus

### Channel Manager

**File**: `channels/manager.py`

The `ChannelManager`:
1. **Initialization**: Checks each channel config's `enabled` flag, lazily imports and creates channel instances
2. **Startup**: Creates an outbound dispatch task + starts all channels concurrently
3. **Outbound dispatch** (`_dispatch_outbound`): Consumes from `bus.outbound`, routes to correct channel's `send()` method. Respects `send_progress` and `send_tool_hints` config.

### Channel Implementations

Each channel follows the same pattern:
1. **`start()`**: Connect to platform (WebSocket, long-polling, SDK)
2. **Listen loop**: Receive messages, extract sender_id/chat_id/content, call `_handle_message()`
3. **`send()`**: Format and deliver outbound message (handle long messages, markdown conversion, file attachments)

**Key platform details**:

| Channel | Connection Method | Message Splitting | File Support | Special Features |
|---------|------------------|-------------------|--------------|------------------|
| Telegram | python-telegram-bot, long-polling | 4096 char limit | Images, voice, documents | Voice transcription via Groq, proxy support, reply quoting |
| Discord | Raw WebSocket gateway (no discord.py) | 2000 char split | — | Heartbeat/reconnect, intent bitfield |
| WhatsApp | WebSocket to Node.js bridge | — | — | QR code login, bridge token auth |
| Feishu | lark-oapi SDK, WebSocket | — | Images (upload to Feishu) | Multi-format message content (text, image, file, audio) |
| DingTalk | dingtalk-stream SDK, Stream Mode | — | — | Bot callback handler |
| Slack | slack-sdk, Socket Mode | slackify-markdown | Files (upload to Slack) | Thread replies, emoji reactions, group policies (mention/open/allowlist) |
| Email | IMAP polling + SMTP | — | — | Subject threading, consent gate, configurable polling interval |
| Mochat | Socket.IO (python-socketio + msgpack) | — | — | Panel/session filtering, mention handling, reply delay |
| QQ | qq-botpy SDK, WebSocket | — | — | Private messages only, sandbox support |

### Session Key Patterns

Each channel generates session keys for the SessionManager:
- Most channels: `"{channel_name}:{chat_id}"`
- Slack threads: `"slack:{channel_id}:{thread_ts}"` (thread-scoped via `session_key_override`)
- CLI: `"cli:direct"`
- Cron: `"cron:{job_id}"`
- Heartbeat: `"heartbeat"`

---

## 13. Subagent System

**File**: `agent/subagent.py`

Subagents are **lightweight background agents** that handle delegated tasks asynchronously.

### Architecture

```
Main Agent ──(spawn tool)──► SubagentManager.spawn()
                                │
                                ▼
                         asyncio.create_task(_run_subagent)
                                │
                                ▼
                         Independent agent loop (max 15 iterations)
                         with its own ToolRegistry (no message/spawn/cron tools)
                                │
                                ▼
                         _announce_result() → InboundMessage(channel="system")
                                │
                                ▼
                         Main agent processes result as system message
                                │
                                ▼
                         Main agent sends summary to user
```

### Subagent Capabilities

Subagents get a **reduced tool set**:
- read_file, write_file, edit_file, list_dir
- exec (shell)
- web_search, web_fetch
- **NOT**: message, spawn, cron (prevents recursive spawning and direct user messaging)

### Lifecycle

1. `SpawnTool.execute()` → `SubagentManager.spawn(task, label, origin_channel, origin_chat_id)`
2. Creates `asyncio.Task` tracked in `_running_tasks[task_id]`
3. Builds focused system prompt with time, workspace path, rules
4. Runs independent LLM loop (max 15 iterations, same provider/model)
5. On completion/error: publishes `InboundMessage(channel="system", chat_id="{origin_channel}:{origin_chat_id}")` to bus
6. Main agent processes this system message and sends a user-friendly summary

### Task ID

8-character UUID prefix (e.g., `"a1b2c3d4"`)

---

## 14. Cron (Scheduled Tasks)

**Files**: `cron/types.py`, `cron/service.py`, `agent/tools/cron.py`

### Data Types

```python
CronSchedule:
    kind: "at" | "every" | "cron"
    at_ms: int | None          # Unix timestamp in ms (for one-shot)
    every_ms: int | None       # Interval in ms (for recurring)
    expr: str | None           # Cron expression (for cron)
    tz: str | None             # IANA timezone (for cron)

CronPayload:
    kind: "system_event" | "agent_turn"
    message: str               # What to tell the agent
    deliver: bool              # Whether to deliver response to channel
    channel: str | None        # Target channel
    to: str | None             # Target chat_id

CronJob:
    id: str                    # 8-char UUID
    name: str
    enabled: bool
    schedule: CronSchedule
    payload: CronPayload
    state: CronJobState        # next_run_at_ms, last_run_at_ms, last_status, last_error
    delete_after_run: bool     # Auto-delete after one-shot execution
```

### Persistence

Jobs stored in `~/.nanobot/cron/jobs.json` as JSON with camelCase keys.

### Timer-Based Execution

The `CronService`:
1. On `start()`: loads store, recomputes all `next_run_at_ms`, arms timer
2. **Timer**: `asyncio.create_task` that sleeps until the earliest `next_run_at_ms`
3. On tick: finds all due jobs (where `now >= next_run_at_ms`), executes each via `on_job` callback
4. After execution: recomputes next run, re-arms timer, saves store

The `on_job` callback (set by gateway command) calls `agent.process_direct()` with the job's message, then optionally delivers the response to the configured channel.

### Next Run Computation

- **"at"**: Fixed timestamp — runs once, then disabled or deleted
- **"every"**: `now_ms + every_ms`
- **"cron"**: Uses `croniter` library with timezone support via `zoneinfo.ZoneInfo`

---

## 15. Heartbeat Service

**File**: `heartbeat/service.py`

A periodic wake-up service that checks `workspace/HEARTBEAT.md` for tasks.

### Algorithm

Every 30 minutes (configurable):
1. Read `HEARTBEAT.md`
2. Check if it has **actionable content** (non-empty, non-header, non-comment lines)
3. If empty: skip silently
4. If has tasks: Send prompt to agent via `process_direct()`:
   > "Read HEARTBEAT.md in your workspace and follow any instructions listed there. If nothing needs attention, reply with exactly: HEARTBEAT_OK"
5. If agent responds with `HEARTBEAT_OK`: silent (nothing to report)
6. If agent responds with anything else: deliver to user's most recently active chat channel via `on_notify` callback

### Target Channel Selection

The gateway picks the target channel for heartbeat notifications by scanning sessions sorted by `updated_at` — the first non-CLI, non-system session on an enabled channel is chosen.

---

## 16. CLI Interface

**File**: `cli/commands.py`

Built with **Typer** (click-based CLI framework) and **Rich** (terminal formatting).

### Commands

| Command | Description |
|---------|-------------|
| `nanobot onboard` | Create config + workspace + template files |
| `nanobot agent -m "..."` | One-shot: process single message via `process_direct()` |
| `nanobot agent` | Interactive: run bus-based loop with prompt_toolkit input |
| `nanobot gateway` | Start full server: agent loop + channels + cron + heartbeat |
| `nanobot status` | Show config/workspace/provider status |
| `nanobot channels status` | Show enabled channels table |
| `nanobot channels login` | Build + run WhatsApp bridge for QR login |
| `nanobot cron list/add/remove/enable/run` | Manage scheduled jobs |
| `nanobot provider login <name>` | OAuth flow for Codex/Copilot |

### Interactive Mode Details

1. Initializes `prompt_toolkit.PromptSession` with file-based history
2. Creates agent loop + bus tasks
3. Saves/restores terminal state (termios) for clean exit
4. Flushes pending TTY input after each response to prevent ghost characters
5. Shows Rich spinner while waiting for response (unless `--logs` is set)
6. Exit commands: `exit`, `quit`, `/exit`, `/quit`, `:q`, `Ctrl+D`, `Ctrl+C`

### Provider Creation (`_make_provider`)

1. Get model from config
2. Get provider name via `config.get_provider_name(model)`
3. Route to:
   - `OpenAICodexProvider` if provider is `openai_codex` or model starts with `openai-codex/`
   - `CustomProvider` if provider is `custom`
   - `LiteLLMProvider` for everything else (with API key, base, headers, provider name)

---

## 17. WhatsApp Bridge (Node.js)

**Directory**: `bridge/`

A TypeScript WebSocket server that wraps `whatsapp-web.js`:

1. **WhatsApp client** (`whatsapp.ts`): Manages WA Web connection, QR code display, message events
2. **WebSocket server** (`server.ts`): Listens on port 3001 (default), relays messages between WA and nanobot
3. **Protocol**: JSON messages over WebSocket:
   - Inbound: `{type: "message", from: "phone", body: "text"}`
   - Outbound: `{type: "send", to: "phone", body: "text"}`

The Python `WhatsAppChannel` connects to this bridge via `websocket-client`.

---

## 18. Data Flow Diagrams

### Gateway Mode — Full Message Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Telegram    │     │   Discord    │     │    Slack     │    ... (other channels)
│   Channel     │     │   Channel    │     │   Channel    │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       │   _handle_message()│                    │
       ▼                    ▼                    ▼
  ┌─────────────────────────────────────────────────┐
  │              MessageBus.inbound                  │   (asyncio.Queue)
  └──────────────────────┬──────────────────────────┘
                         │ bus.consume_inbound()
                         ▼
  ┌─────────────────────────────────────────────────┐
  │                  AgentLoop.run()                  │
  │                                                   │
  │  1. Get/create Session (JSONL)                   │
  │  2. ContextBuilder.build_messages()              │
  │     ├── System prompt (identity, bootstrap,      │
  │     │   memory, skills)                          │
  │     ├── Session history                          │
  │     └── Current user message (+ media)           │
  │  3. _run_agent_loop():                           │
  │     ├── provider.chat(messages, tools)           │
  │     ├── If tool_calls → execute → loop           │
  │     └── If text only → final response            │
  │  4. Save turn to Session                         │
  │  5. Maybe trigger memory consolidation (bg)      │
  └──────────────────────┬──────────────────────────┘
                         │ bus.publish_outbound()
                         ▼
  ┌─────────────────────────────────────────────────┐
  │              MessageBus.outbound                  │   (asyncio.Queue)
  └──────────────────────┬──────────────────────────┘
                         │ ChannelManager._dispatch_outbound()
                         ▼
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │   Telegram    │     │   Discord    │     │    Slack     │
  │   .send()    │     │   .send()    │     │   .send()    │
  └──────────────┘     └──────────────┘     └──────────────┘
```

### CLI Mode — Direct Processing

```
  User input ──► AgentLoop.process_direct()
                      │
                      ├── ContextBuilder.build_messages()
                      ├── _run_agent_loop() (LLM ↔ tools)
                      └── Return response string
                              │
                              ▼
                      Print to terminal (Rich markdown)
```

### Subagent Flow

```
  Main Agent ──(spawn tool)──► SubagentManager
                                    │
                                    ▼
                              Background Task
                              (15 iterations max)
                              (reduced tool set)
                                    │
                                    ▼
                              InboundMessage(channel="system")
                                    │
                                    ▼
                              bus.inbound → AgentLoop
                                    │
                                    ▼
                              Response to user
```

### Memory Consolidation Flow

```
  Session reaches memory_window messages
       │
       ▼
  Background asyncio task (with per-session lock)
       │
       ▼
  Format old messages as text
       │
       ▼
  LLM call with save_memory tool
       │
       ├──► HISTORY.md (append entry)
       └──► MEMORY.md (overwrite with updated facts)
       │
       ▼
  Update session.last_consolidated pointer
```

---

## 19. Key Design Decisions

### Why These Matter for Reimplementation

1. **Message bus decoupling**: Channels and agent are fully decoupled via async queues. This means channels can be added/removed without modifying the agent. Your reimplementation needs a similar pub/sub or queue mechanism.

2. **Append-only sessions**: Messages are never deleted from session files for LLM cache efficiency. Memory consolidation writes to separate files (MEMORY.md, HISTORY.md) and only advances a pointer. This is critical for prompt caching with providers like Anthropic.

3. **Progressive skill loading**: Skills are NOT all loaded into the system prompt. Only `always=true` skills are included fully; others are listed as XML summaries. The agent reads full skill content on demand via `read_file`. This keeps the system prompt small.

4. **Single-threaded agent loop**: Only one message is processed at a time per agent loop. Concurrency comes from subagents (separate tool registries, separate LLM calls) and background consolidation tasks.

5. **Tool result truncation**: Tool results saved to session history are truncated at 500 chars. Full results are seen by the LLM in the current turn but not persisted at full length.

6. **Error as content, not exceptions**: LLM errors and tool errors are returned as string content, not raised as exceptions. This lets the LLM see errors and adapt. The tool registry appends "[Analyze the error above and try a different approach.]" to guide recovery.

7. **Provider registry pattern**: Adding a new LLM provider requires only 2 changes: add a `ProviderSpec` to the registry tuple, and add a field to `ProvidersConfig`. No if-elif chains anywhere. Your reimplementation should use a similar registry/plugin pattern.

8. **camelCase ↔ snake_case**: Config JSON uses camelCase (JS conventions), Python code uses snake_case. Pydantic handles the mapping. Your reimplementation needs similar bidirectional serialization.

9. **MCP lazy connection**: MCP servers are connected on the first message, not on startup. This avoids blocking startup if MCP servers are slow or unavailable.

10. **Message tool suppression**: If the agent uses the `message` tool to send a response during a turn, the final LLM response is suppressed (return `None`). This prevents double-sending. Track `_sent_in_turn` per tool turn.

11. **Consolidation locking**: Per-session asyncio locks prevent concurrent consolidation of the same session. The `_consolidating` set tracks which sessions have consolidation in progress to avoid triggering it twice.

12. **Safety guards are regex-based**: The shell exec tool uses regex deny patterns, not a sandbox. This is a best-effort guard. For a more secure reimplementation, consider using OS-level sandboxing.

13. **WhatsApp uses a separate Node.js bridge**: Because `whatsapp-web.js` is a Node library. Your reimplementation could use a native WhatsApp library for your target language, or keep the bridge pattern.

### Concurrency Model

- **asyncio** throughout — all I/O is async
- Single event loop, no threading (except `asyncio.to_thread` for OAuth token fetch)
- Channel starts are concurrent (`asyncio.gather`)
- Subagents run as `asyncio.Task`s
- Memory consolidation runs as `asyncio.Task` with per-session locks

### File System Layout at Runtime

```
~/.nanobot/
├── config.json                    # User configuration
├── bridge/                        # WhatsApp bridge (built on first use)
├── history/
│   └── cli_history                # prompt_toolkit input history
├── cron/
│   └── jobs.json                  # Scheduled jobs
└── workspace/                     # Agent workspace (configurable)
    ├── SOUL.md                    # Agent personality
    ├── TOOLS.md                   # Tool usage notes
    ├── HEARTBEAT.md               # Periodic tasks
    ├── USER.md                    # User info (if created)
    ├── AGENTS.md                  # Agent behavior (if created)
    ├── IDENTITY.md                # Custom identity (if created)
    ├── memory/
    │   ├── MEMORY.md              # Long-term facts
    │   └── HISTORY.md             # Chronological summary log
    ├── sessions/
    │   ├── telegram_12345.jsonl   # Per-chat session history
    │   ├── discord_67890.jsonl
    │   └── cli_direct.jsonl
    └── skills/
        └── my-custom-skill/
            └── SKILL.md
```
