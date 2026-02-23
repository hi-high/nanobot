# Nanobot Reimplementation — Product Requirements Document

## 1. Product Vision

Reimplement the nanobot personal AI assistant framework from Python into a new language, preserving all core functionality while enabling additional features. The system connects an LLM-powered agentic loop to multiple chat platforms, giving users a single AI assistant reachable from Telegram, Discord, Slack, WhatsApp, Email, and more.

This PRD breaks the work into **6 phases**, ordered by dependency chain. Each phase produces a **working, testable artifact** — you can ship and validate each phase before starting the next.

---

## 2. Phased Implementation Plan

### Phase Overview

| Phase | Name | What You Get | Est. Complexity |
|-------|------|-------------|-----------------|
| 1 | Core Foundation | Config, types, session storage, single LLM call | Low |
| 2 | Agent Loop + Tools | Iterative LLM ↔ tool execution, CLI mode | Medium |
| 3 | Memory + Skills | Persistent memory, skill loading, context assembly | Medium |
| 4 | Message Bus + Channels | Gateway mode, first chat channel (Telegram) | Medium-High |
| 5 | All Channels + Services | Remaining channels, cron, heartbeat, subagents | High |
| 6 | Advanced Providers + Polish | Multi-provider, MCP, OAuth providers, onboarding | Medium |

```
Phase 1          Phase 2          Phase 3          Phase 4          Phase 5          Phase 6
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Config   │───►│  Agent   │───►│  Memory  │───►│  Bus +   │───►│ Channels │───►│ Advanced │
│  Types    │    │  Loop    │    │  Skills  │    │  Gateway │    │ Cron/HB  │    │ Providers│
│  Session  │    │  Tools   │    │  Context │    │  1st Ch. │    │ Subagent │    │ MCP/OAuth│
│  1 LLM   │    │  CLI     │    │          │    │          │    │          │    │ Onboard  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
    MVP              Usable          Smart           Connected       Complete        Polished
  (library)        (CLI app)       (has memory)     (1 channel)    (all channels)   (production)
```

---

## 3. Phase 1 — Core Foundation

**Goal**: Establish the type system, configuration, session persistence, and a single working LLM call. Everything in later phases depends on these primitives.

### 3.1 Configuration System

#### Requirements

- [ ] Define root `Config` struct/type matching the schema in ARCHITECTURE.md §3
- [ ] Support loading from `~/.nanobot/config.json`
- [ ] Support saving config back to disk (for onboarding)
- [ ] JSON keys are **camelCase** in the file, **snake_case** in code (bidirectional mapping)
- [ ] Support environment variable overrides with prefix `NANOBOT_` and `__` as nested delimiter
- [ ] Provide sensible defaults for all fields (see ARCHITECTURE.md §3 for values)
- [ ] Implement config migration: `tools.exec.restrictToWorkspace` → `tools.restrictToWorkspace`

#### Key Types

```
Config
├── AgentsConfig → AgentDefaults (workspace, model, max_tokens, temperature, max_tool_iterations, memory_window)
├── ProvidersConfig → ProviderConfig (api_key, api_base, extra_headers) × 16 providers
├── ChannelsConfig → per-channel config types (9 channels)
├── ToolsConfig → WebToolsConfig, ExecToolConfig, MCPServerConfig
└── GatewayConfig (host, port)
```

#### Acceptance Criteria
- Load a sample `config.json`, access all nested fields with correct types
- Modify a field, save, reload — round-trips correctly
- Env var `NANOBOT_AGENTS__DEFAULTS__MODEL=x` overrides `agents.defaults.model`

### 3.2 Provider Abstraction (Single Provider)

#### Requirements

- [ ] Define `LLMProvider` interface: `chat(messages, tools, model, max_tokens, temperature) → LLMResponse`
- [ ] Define `LLMResponse` type: `content`, `tool_calls[]`, `finish_reason`, `usage`, `reasoning_content`
- [ ] Define `ToolCallRequest` type: `id`, `name`, `arguments` (dict/map)
- [ ] Implement one concrete provider — the **OpenAI-compatible direct client** (`CustomProvider`)
  - Uses standard OpenAI chat completions API
  - Handles tool call parsing (JSON, with malformed JSON repair)
  - Returns errors as content strings, not exceptions
- [ ] Implement `_sanitize_empty_content()` — replace empty content with `"(empty)"` for user/tool messages, `None` for assistant messages with tool_calls

#### Provider Matching (minimal)

- [ ] Implement `Config.get_provider()` — for Phase 1, just return the first provider with an API key
- [ ] Full matching algorithm (prefix, keyword, fallback) can wait until Phase 6

#### Acceptance Criteria
- Send a simple message to an OpenAI-compatible API, get a text response back
- Send a message with tool definitions, get tool calls back with parsed arguments
- Malformed JSON in tool arguments is repaired and parsed

### 3.3 Session Management

#### Requirements

- [ ] Define `Session` struct: `key`, `messages[]`, `created_at`, `updated_at`, `metadata`, `last_consolidated`
- [ ] Implement JSONL persistence: first line = metadata, remaining lines = messages
- [ ] Implement `SessionManager`:
  - `get_or_create(key)` — check memory cache → load from disk → create new
  - `save(session)` — overwrite entire file, update cache
  - `invalidate(key)` — remove from memory cache
  - `list_sessions()` — scan directory, read metadata lines only
- [ ] File naming: replace `:` with `_`, strip unsafe chars
- [ ] Legacy migration: if file exists at `~/.nanobot/sessions/`, move to `workspace/sessions/`

#### Acceptance Criteria
- Create session, add messages, save, reload — messages match
- JSONL file format matches spec (metadata line + message lines)
- Cache prevents redundant disk reads

### 3.4 Utility Functions

- [ ] `ensure_dir(path)` — create directory if not exists
- [ ] `safe_filename(name)` — strip `<>:"/\\|?*` characters
- [ ] `get_workspace_path(optional_override)` — resolve and ensure workspace directory
- [ ] `truncate_string(s, max_len)` — truncate with `...` suffix
- [ ] `parse_session_key(key)` → `(channel, chat_id)`

### 3.5 Deliverable

A library that can:
1. Load config from disk
2. Create/manage sessions
3. Make a single LLM API call and parse the response

---

## 4. Phase 2 — Agent Loop + Tools + CLI

**Goal**: Build the iterative agent loop and tool system. End result: a working CLI chatbot that can read files, run commands, and search the web.

### 4.1 Tool System

#### Base Tool Interface

- [ ] Define `Tool` interface/trait: `name`, `description`, `parameters` (JSON Schema), `execute(**params) → string`
- [ ] Implement `validate_params(params)` — walk JSON Schema, check types, required fields, enums, ranges, string lengths
- [ ] Implement `to_schema()` — convert to OpenAI function-calling format

#### Tool Registry

- [ ] `ToolRegistry` with `register()`, `unregister()`, `get()`, `get_definitions()`, `execute()`
- [ ] On execution failure, append: `"\n\n[Analyze the error above and try a different approach.]"`

#### Built-in Tools (implement in order)

1. **`read_file`**
   - [ ] Resolve relative paths against workspace
   - [ ] If `restrict_to_workspace`: validate path is within allowed directory
   - [ ] Return file content as UTF-8 string

2. **`write_file`**
   - [ ] Create parent directories if needed
   - [ ] Write UTF-8 content
   - [ ] Workspace restriction check

3. **`edit_file`**
   - [ ] Search-and-replace: find `old_text`, replace with `new_text`
   - [ ] If not found: fuzzy match with `SequenceMatcher`-equivalent, return unified diff
   - [ ] If multiple matches: warn and ask for more context
   - [ ] Replace only first occurrence

4. **`list_dir`**
   - [ ] Return sorted entries with type indicators (folder vs file)

5. **`exec`**
   - [ ] Safety guard: regex deny patterns (`rm -rf`, `mkfs`, `dd if=`, `shutdown`, fork bombs, etc.)
   - [ ] Workspace restriction: block `../` traversal and absolute paths outside workspace
   - [ ] Async subprocess with timeout (default 60s)
   - [ ] Capture stdout + stderr, truncate at 10,000 chars
   - [ ] Return exit code on non-zero

6. **`web_search`**
   - [ ] HTTP GET to Brave Search API
   - [ ] API key in `X-Subscription-Token` header
   - [ ] Format results: title, URL, description

7. **`web_fetch`**
   - [ ] URL validation (http/https, valid domain)
   - [ ] Follow up to 5 redirects
   - [ ] HTML: extract article content (readability algorithm), convert to markdown
   - [ ] JSON: pretty-print
   - [ ] Truncate at 50,000 chars default
   - [ ] Return JSON envelope: `{url, finalUrl, status, extractor, truncated, length, text}`

### 4.2 Agent Loop

#### Core Loop

- [ ] Implement `_run_agent_loop(messages, tools, on_progress)`:
  ```
  while iteration < max_iterations:
      response = provider.chat(messages, tools)
      if response.has_tool_calls:
          append assistant message (with tool_calls) to messages
          for each tool_call:
              result = tools.execute(name, arguments)
              append tool result to messages
              call on_progress with tool hint
          if response has text content:
              call on_progress with intermediate text
      else:
          return response.content (stripped of <think> blocks)
  return fallback message
  ```

- [ ] Implement think-block stripping: `<think>…</think>` regex removal
- [ ] Implement `process_direct(content, session_key, ...)` — the non-bus entry point

#### Turn Saving

- [ ] After each turn, append new messages to session
- [ ] Add `timestamp` field to each message
- [ ] Truncate tool result content to 500 chars in saved messages
- [ ] Strip `reasoning_content` from saved messages

#### Slash Commands

- [ ] `/help` — return command list string
- [ ] `/new` — clear session (full implementation with memory archiving comes in Phase 3)

### 4.3 CLI Interface

- [ ] Implement one-shot mode: `nanobot agent -m "message"` → process and print response
- [ ] Implement interactive mode: REPL with input prompt, history file at `~/.nanobot/history/cli_history`
- [ ] Session key: `"cli:direct"`
- [ ] Display agent response with markdown formatting
- [ ] Show spinner/progress indicator while waiting
- [ ] Exit on: `exit`, `quit`, `/exit`, `/quit`, `:q`, Ctrl+D, Ctrl+C

### 4.4 Deliverable

A working CLI chatbot:
```
$ nanobot agent
You: What files are in this directory?
nanobot: Let me check...
[uses list_dir tool]
Here are the files: ...
```

---

## 5. Phase 3 — Memory + Skills + Context Assembly

**Goal**: Add persistent memory across sessions and the skill system. The agent now remembers facts about the user and can leverage skill modules.

### 5.1 Context Builder

- [ ] Implement `ContextBuilder.build_system_prompt()`:
  1. **Identity section**: Current time, timezone, OS/runtime info, workspace path, tool guidelines, memory file locations
  2. **Bootstrap files**: Load `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `IDENTITY.md` from workspace root (skip missing)
  3. **Memory**: Include `MEMORY.md` content under `# Memory`
  4. **Always-loaded skills**: Full content of skills with `always=true`
  5. **Skills summary**: XML listing of all available skills
  6. Join sections with `\n\n---\n\n`

- [ ] Implement `build_messages(history, current_message, media, channel, chat_id)`:
  - System prompt + `## Current Session` with channel/chat_id
  - History messages
  - Current user message (with optional base64-encoded images)

- [ ] Implement media handling: detect image MIME types, base64 encode, format as OpenAI multimodal content blocks

### 5.2 Memory System

#### MemoryStore

- [ ] Two files: `workspace/memory/MEMORY.md` (facts) and `workspace/memory/HISTORY.md` (log)
- [ ] `get_memory_context()` — read and return MEMORY.md content
- [ ] `consolidate(session, provider, archive_all)` — the consolidation algorithm:

#### Consolidation Algorithm

- [ ] Trigger condition: `len(messages) - last_consolidated >= memory_window`
- [ ] Message selection:
  - Normal: from `last_consolidated` to `len(messages) - memory_window/2`
  - Archive all (`/new`): all messages
- [ ] Build consolidation prompt: format messages as `[timestamp] ROLE: content`
- [ ] Call LLM with a `save_memory` tool:
  ```json
  {
    "name": "save_memory",
    "parameters": {
      "history_entry": "string (2-5 sentence summary with [YYYY-MM-DD HH:MM] prefix)",
      "memory_update": "string (full updated MEMORY.md content)"
    }
  }
  ```
- [ ] On tool call: append `history_entry` to HISTORY.md, overwrite MEMORY.md with `memory_update`
- [ ] Update `session.last_consolidated`
- [ ] Run as background task with **per-session lock** (prevent concurrent consolidation of same session)
- [ ] Track `_consolidating` set to avoid double-triggering

#### Integration with Agent Loop

- [ ] Before each turn, check if consolidation is needed (non-blocking trigger)
- [ ] `/new` command: trigger `archive_all` consolidation, then clear session and invalidate cache

### 5.3 Skills System

#### SkillsLoader

- [ ] Scan two directories: `workspace/skills/` (user) and built-in `skills/` (shipped)
- [ ] Workspace skills override built-in skills with same name
- [ ] Each skill: `{name}/SKILL.md` with optional YAML frontmatter

#### Frontmatter Parsing

- [ ] Detect `---\n...\n---` block at file start
- [ ] Parse simple key-value YAML (name, description, metadata)
- [ ] Parse `metadata` field as JSON: `{"nanobot": {"always": true, "requires": {"bins": [...], "env": [...]}}}`

#### Requirement Checking

- [ ] `bins`: check if CLI binary exists on PATH (equivalent of `which`)
- [ ] `env`: check if environment variable is set
- [ ] Mark skills as available/unavailable

#### Progressive Loading

- [ ] `get_always_skills()` → list of skill names with `always=true` that meet requirements
- [ ] `load_skills_for_context(names)` → concatenated skill content (frontmatter stripped)
- [ ] `build_skills_summary()` → XML string:
  ```xml
  <skills>
    <skill available="true">
      <name>github</name>
      <description>GitHub operations</description>
      <location>/path/to/SKILL.md</location>
    </skill>
  </skills>
  ```

### 5.4 Template Files

- [ ] Ship template files: `SOUL.md`, `TOOLS.md`, `HEARTBEAT.md`
- [ ] Copy to workspace on first run / onboarding

### 5.5 Deliverable

The CLI agent now:
- Remembers facts between sessions (MEMORY.md)
- Has a searchable conversation log (HISTORY.md)
- Can leverage skills for specialized tasks
- `/new` archives everything and starts fresh

---

## 6. Phase 4 — Message Bus + Gateway + First Channel

**Goal**: Introduce the message bus for channel decoupling and get the first real chat channel working (Telegram recommended — simplest API, best docs).

### 6.1 Message Bus

- [ ] Define `InboundMessage`: channel, sender_id, chat_id, content, timestamp, media, metadata, session_key_override
- [ ] Define `OutboundMessage`: channel, chat_id, content, reply_to, media, metadata
- [ ] `InboundMessage.session_key` property: return override or `"{channel}:{chat_id}"`
- [ ] Implement `MessageBus` with two async queues:
  - `publish_inbound(msg)` / `consume_inbound(timeout)` — with 1s timeout for graceful shutdown
  - `publish_outbound(msg)` / `consume_outbound()`

### 6.2 Message Tool

- [ ] Implement `MessageTool`:
  - Params: `content` (required), `channel`, `chat_id`, `media` (optional)
  - Publishes `OutboundMessage` via bus
  - Has `set_context(channel, chat_id, message_id)` — called each turn
  - Tracks `_sent_in_turn` flag (reset each turn)
- [ ] In agent loop: if `message_tool._sent_in_turn` after a turn, suppress the final response (return `None`)

### 6.3 Progress Messages

- [ ] During tool execution, publish intermediate text as `OutboundMessage` with `metadata._progress = True`
- [ ] Publish tool name hints as `OutboundMessage` with `metadata._tool_hint = True`
- [ ] Channel manager / CLI respects `channels.send_progress` and `channels.send_tool_hints` config

### 6.4 Agent Loop — Bus Mode

- [ ] Implement `AgentLoop.run()` — the bus-consuming main loop:
  ```
  while running:
      msg = await bus.consume_inbound(timeout=1s)
      if msg is None: continue  # timeout, check _running
      response = await _process_message(msg)
      if response:
          await bus.publish_outbound(response)
  ```
- [ ] `_process_message()` handles: system messages (from subagents), slash commands, regular messages
- [ ] System messages: parse `channel:chat_id` from `msg.chat_id`, process, respond to original channel

### 6.5 Base Channel

- [ ] Define `BaseChannel` interface: `name`, `config`, `bus`, `start()`, `stop()`, `send(OutboundMessage)`, `is_allowed(sender_id)`
- [ ] `is_allowed()`: empty `allow_from` = allow all; otherwise check membership (handle `|`-separated composite IDs)
- [ ] `_handle_message(sender_id, chat_id, content, media, metadata)` → check allowed, publish to bus

### 6.6 Channel Manager

- [ ] `ChannelManager.__init__(config, bus)` — scan channel configs, instantiate enabled channels
- [ ] `start()` — start outbound dispatch task + all channels concurrently
- [ ] `stop()` — stop all channels + dispatch task
- [ ] `_dispatch_outbound()` — consume from outbound queue, route to correct channel's `send()`
  - Skip `_progress` messages if `send_progress` is false
  - Skip `_tool_hint` messages if `send_tool_hints` is false

### 6.7 First Channel: Telegram

- [ ] Connect via long-polling (no webhook needed)
- [ ] Handle text messages: extract sender_id, chat_id, content → `_handle_message()`
- [ ] Handle voice messages: download audio file, transcribe via Groq Whisper API, process as text
- [ ] Handle images: download to temp file, pass path in `media` field
- [ ] `send()`: split messages at 4096 char limit, send as markdown
- [ ] Support `reply_to_message` config (quote original message)
- [ ] Support proxy config
- [ ] Implement `GroqTranscriptionProvider` for voice

### 6.8 Gateway Command

- [ ] `nanobot gateway` — start agent loop + channel manager + future services (cron, heartbeat)
- [ ] Graceful shutdown on SIGINT/SIGTERM
- [ ] Log startup info (enabled channels, model, workspace)

### 6.9 Deliverable

Working Telegram bot:
- Responds to messages from allowed users
- Shows progress indicators during tool execution
- Handles voice messages and images
- Runs as a persistent gateway process

---

## 7. Phase 5 — All Channels + Cron + Heartbeat + Subagents

**Goal**: Complete the channel roster, add all background services, and enable subagent delegation.

### 7.1 Remaining Channels

Implement each following the `BaseChannel` pattern. Priority order (most common first):

#### Discord
- [ ] Raw WebSocket to `wss://gateway.discord.gg/?v=10&encoding=json`
- [ ] Handle HELLO (heartbeat interval), IDENTIFY (token + intents), DISPATCH (MESSAGE_CREATE)
- [ ] Reconnect on disconnect with resume capability
- [ ] Split messages at 2000 chars
- [ ] Intent bitfield: `37377` = GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

#### Slack
- [ ] Socket Mode via SDK (app_token + bot_token)
- [ ] Thread-scoped sessions: `session_key_override = "slack:{channel_id}:{thread_ts}"`
- [ ] Reply in thread (configurable)
- [ ] Emoji reaction on message receipt (configurable, default "eyes")
- [ ] Group policies: "mention" (must @bot), "open" (respond to all), "allowlist" (specific channels)
- [ ] DM policy: "open" or "allowlist"
- [ ] Markdown conversion for Slack format
- [ ] File upload support

#### WhatsApp
- [ ] WebSocket client connecting to Node.js bridge at `bridge_url`
- [ ] Bridge token authentication (optional)
- [ ] JSON protocol: inbound `{type: "message", from, body}`, outbound `{type: "send", to, body}`
- [ ] (Optional) Reimplement bridge in target language or keep Node.js bridge

#### Feishu / Lark
- [ ] WebSocket long connection via SDK
- [ ] Handle text, image, file, audio message types
- [ ] Upload images to Feishu before sending
- [ ] App ID + App Secret authentication

#### DingTalk
- [ ] Stream mode via SDK
- [ ] Client ID + Client Secret authentication
- [ ] Bot callback handler

#### Email
- [ ] IMAP polling (configurable interval, default 30s)
- [ ] SMTP sending
- [ ] Consent gate: `consent_granted` must be true
- [ ] Subject threading (Re: prefix)
- [ ] Mark as seen after processing
- [ ] Truncate body at `max_body_chars` (12000)
- [ ] SSL/TLS support for both IMAP and SMTP

#### QQ
- [ ] WebSocket via botpy SDK
- [ ] Private messages only
- [ ] App ID + Secret authentication

#### Mochat
- [ ] Socket.IO with optional msgpack
- [ ] Panel/session filtering
- [ ] Mention handling (configurable: require in groups)
- [ ] Reply delay for non-mention messages
- [ ] Reconnect with exponential backoff

### 7.2 Subagent System

- [ ] Implement `SubagentManager`:
  - `spawn(task, label, origin_channel, origin_chat_id)` → task_id (8-char UUID)
  - Track running tasks in map
  - Background task: run independent agent loop (max 15 iterations)
  - Reduced tool set: read_file, write_file, edit_file, list_dir, exec, web_search, web_fetch (NO message, spawn, cron)
  - On completion: publish `InboundMessage(channel="system", chat_id="{origin_channel}:{origin_chat_id}")`
  - On error: publish error message same way

- [ ] Implement `SpawnTool`:
  - Params: `task` (required), `label` (optional)
  - Delegates to SubagentManager, returns immediately

- [ ] Agent loop: handle system messages — parse `channel:chat_id`, run through agent, respond to original channel

### 7.3 Cron Service

#### Data Types

- [ ] `CronSchedule`: kind ("at"|"every"|"cron"), at_ms, every_ms, expr, tz
- [ ] `CronPayload`: kind, message, deliver, channel, to
- [ ] `CronJob`: id, name, enabled, schedule, payload, state, delete_after_run
- [ ] `CronJobState`: next_run_at_ms, last_run_at_ms, last_status, last_error
- [ ] `CronStore`: version, jobs[]

#### Persistence

- [ ] Load/save `~/.nanobot/cron/jobs.json` (camelCase keys)

#### Service

- [ ] `CronService.start()` — load store, recompute next runs, arm timer
- [ ] Timer: async sleep until earliest `next_run_at_ms`, then fire
- [ ] On tick: find all due jobs (`now >= next_run_at_ms`), execute each
- [ ] `_execute_job(job)`: call `on_job` callback, update state, handle one-shot ("at") vs recurring
- [ ] Next run computation:
  - "at": fixed timestamp, disable/delete after run
  - "every": `now_ms + every_ms`
  - "cron": use cron expression parser with timezone support

#### Cron Tool

- [ ] `CronTool` with actions: "add", "list", "remove"
- [ ] Wire `on_job` callback to `agent.process_direct()` with session key `"cron:{job_id}"`
- [ ] Optional delivery: if `deliver=true`, send response to configured channel

#### CLI Commands

- [ ] `nanobot cron list` — show all jobs with next run time
- [ ] `nanobot cron add` — create job with schedule params
- [ ] `nanobot cron remove <id>` — delete job
- [ ] `nanobot cron enable/disable <id>`
- [ ] `nanobot cron run <id>` — manual trigger

### 7.4 Heartbeat Service

- [ ] `HeartbeatService(workspace, on_heartbeat, on_notify, interval_s=1800)`
- [ ] Loop: sleep `interval_s`, then tick
- [ ] Tick:
  1. Read `workspace/HEARTBEAT.md`
  2. Check if actionable (non-empty, non-header, non-comment lines)
  3. If empty: skip
  4. If has tasks: call `on_heartbeat(prompt)` → get response
  5. If response contains "HEARTBEAT_OK": silent
  6. Else: call `on_notify(response)` to deliver to user
- [ ] Target channel selection: scan sessions sorted by `updated_at`, pick first non-CLI/non-system enabled channel
- [ ] `trigger_now()` — manual trigger

### 7.5 Gateway Integration

- [ ] Wire cron service into gateway: `on_job` → `agent.process_direct()`, deliver to channel if configured
- [ ] Wire heartbeat into gateway: `on_heartbeat` → `agent.process_direct()`, `on_notify` → deliver to most recent channel
- [ ] Start cron + heartbeat alongside agent loop and channels

### 7.6 Deliverable

Fully functional multi-channel assistant with:
- All 9 chat channels working
- Background subagents for parallel task execution
- Scheduled reminders/tasks via cron
- Periodic heartbeat checking HEARTBEAT.md

---

## 8. Phase 6 — Advanced Providers + MCP + Polish

**Goal**: Complete the provider ecosystem, add MCP support, OAuth flows, and the onboarding experience.

### 8.1 Provider Registry

- [ ] Implement `ProviderSpec` struct with all fields (see ARCHITECTURE.md §8)
- [ ] Define `PROVIDERS` registry (ordered tuple/array):
  1. Custom (direct, is_direct=true)
  2. Gateways: OpenRouter, AiHubMix, SiliconFlow, VolcEngine
  3. Standard: Anthropic, OpenAI, Codex, Copilot, DeepSeek, Gemini, Zhipu, DashScope, Moonshot, MiniMax
  4. Local: vLLM
  5. Auxiliary: Groq
- [ ] Lookup functions: `find_by_model()`, `find_gateway()`, `find_by_name()`

### 8.2 Full Provider Matching

- [ ] Implement three-tier matching in `Config._match_provider()`:
  1. Explicit prefix match (model prefix → provider name)
  2. Keyword match (provider keywords in model name)
  3. Fallback (first keyed provider, skip OAuth)

### 8.3 LiteLLM Provider (or equivalent)

- [ ] Multi-provider routing via LiteLLM-equivalent library (or direct HTTP for each)
- [ ] Model resolution: detect gateway → apply prefix; else find spec → apply litellm_prefix
- [ ] Prompt caching: for Anthropic/OpenRouter, inject `cache_control: {"type": "ephemeral"}` on system message and last tool definition
- [ ] Model overrides: per-model parameter adjustments (e.g., Kimi K2.5 temp >= 1.0)
- [ ] Message sanitization: strip non-standard keys, keep only `{role, content, tool_calls, tool_call_id, name}`
- [ ] Tool argument repair: handle malformed JSON from LLMs

### 8.4 OpenAI Codex Provider

- [ ] OAuth token acquisition via `oauth-cli-kit` equivalent
- [ ] SSE streaming to `https://chatgpt.com/backend-api/codex/responses`
- [ ] Convert chat messages → Codex input items (message, function_call, function_call_output)
- [ ] Parse SSE events → LLMResponse with tool_calls
- [ ] Tool call ID format: `call_id|item_id` for roundtripping
- [ ] Prompt cache key: SHA-256 of messages JSON

### 8.5 GitHub Copilot Provider

- [ ] OAuth token acquisition
- [ ] Route through appropriate endpoint

### 8.6 MCP (Model Context Protocol) Integration

- [ ] Implement MCP client supporting both transports:
  - **Stdio**: spawn local process, communicate via stdin/stdout
  - **HTTP**: connect to remote streamable HTTP endpoint
- [ ] `connect_mcp_servers(config)` → list of `MCPToolWrapper` instances
- [ ] Each MCP tool registered as `mcp_{server_name}_{tool_name}`
- [ ] Per-tool timeout (default 30s from config)
- [ ] **Lazy connection**: connect on first message, not on startup

### 8.7 Onboarding

- [ ] `nanobot onboard` command:
  1. Create `~/.nanobot/` directory structure
  2. Create default `config.json`
  3. Create workspace with template files (SOUL.md, TOOLS.md, HEARTBEAT.md)
  4. Interactive prompts for API key setup
  5. Validate provider connectivity

### 8.8 Status & Diagnostics

- [ ] `nanobot status` — show config path, workspace path, model, provider, API key status (masked)
- [ ] `nanobot channels status` — table of all channels with enabled/disabled status
- [ ] `nanobot channels login` — WhatsApp bridge build + QR login flow
- [ ] `nanobot provider login <name>` — OAuth flow for Codex/Copilot

### 8.9 Deliverable

Production-ready system with:
- 16+ LLM providers supported
- MCP tool server integration
- OAuth login flows
- Guided onboarding
- Diagnostic commands

---

## 9. Cross-Cutting Concerns (All Phases)

### 9.1 Error Handling Philosophy

- **LLM errors**: Return as `LLMResponse(content="Error: ...", finish_reason="error")` — never throw/panic
- **Tool errors**: Return error string with recovery hint — never crash the agent loop
- **Channel errors**: Log and continue — one channel failing shouldn't kill others
- **File I/O errors**: Return error string or fallback — never crash

### 9.2 Concurrency Model

- All I/O must be async/non-blocking
- Single event loop, no threading (except where unavoidable, e.g., OAuth token fetch)
- Channel starts are concurrent
- Subagents run as concurrent tasks
- Memory consolidation runs as background task with per-session lock
- Agent loop processes one message at a time (no concurrent turns)

### 9.3 Logging

- Structured logging throughout (original uses `loguru`)
- Log levels: DEBUG for internal state, INFO for operations, WARNING for recoverable issues, ERROR for failures

### 9.4 Testing Strategy

| Phase | What to Test |
|-------|-------------|
| 1 | Config load/save round-trip, session JSONL persistence, LLM response parsing |
| 2 | Tool validation, tool execution (mock filesystem), agent loop iteration count, think-block stripping |
| 3 | Memory consolidation (mock LLM), skill loading, frontmatter parsing, context builder output |
| 4 | Message bus pub/sub, channel manager routing, message tool suppression, progress filtering |
| 5 | Cron next-run computation, heartbeat empty detection, subagent lifecycle |
| 6 | Provider matching algorithm, model resolution, MCP tool wrapping |

### 9.5 Security Considerations

- [ ] Exec tool: regex deny patterns are **best-effort** — consider OS-level sandboxing for your target language
- [ ] Workspace restriction: enforce at every file I/O tool, not just read
- [ ] API keys: never log, mask in status output
- [ ] Channel `allow_from`: enforce before any processing
- [ ] Config file permissions: warn if world-readable

---

## 10. Extension Points for New Features

The following are natural extension points where additional functionality can be added without disrupting the existing architecture:

| Extension | Where to Add | Effort |
|-----------|-------------|--------|
| New chat channel | Implement `BaseChannel`, add config type, register in ChannelManager | Low |
| New LLM provider | Add `ProviderSpec` to registry, add config field | Low |
| New built-in tool | Implement `Tool` interface, register in AgentLoop init | Low |
| New skill | Create `{name}/SKILL.md` in skills directory | Trivial |
| Streaming responses | Add streaming variant to `LLMProvider.chat()`, update channels | Medium |
| Web dashboard | Add HTTP server alongside gateway, read sessions/memory | Medium |
| Multi-user | Add user-scoped workspaces, per-user sessions | Medium |
| Plugin system | Dynamic tool/channel loading from external packages | Medium |
| Database backend | Replace JSONL sessions + JSON cron store with SQLite/Postgres | Medium |
| Rate limiting | Add per-channel, per-user rate limits in BaseChannel | Low |

---

## 11. Glossary

| Term | Definition |
|------|-----------|
| **Agent Loop** | The iterative LLM → tool → LLM cycle that processes a single user message |
| **Bootstrap Files** | Markdown files in workspace root loaded into every system prompt (SOUL.md, TOOLS.md, etc.) |
| **Channel** | A chat platform integration (Telegram, Discord, etc.) |
| **Consolidation** | The process of summarizing old messages into MEMORY.md and HISTORY.md |
| **Gateway Mode** | Long-running server mode with bus, channels, cron, and heartbeat |
| **Heartbeat** | Periodic check of HEARTBEAT.md for agent tasks |
| **Memory Window** | Number of unconsolidated messages before triggering consolidation (default 100) |
| **MCP** | Model Context Protocol — standard for connecting external tool servers |
| **Progressive Loading** | Skills strategy: show summaries in prompt, load full content on demand via read_file |
| **Session Key** | String identifier for a conversation: `"{channel}:{chat_id}"` |
| **Subagent** | Background agent with reduced tools that reports results via system messages |
| **Turn** | One complete cycle: user message → agent processing → response |
