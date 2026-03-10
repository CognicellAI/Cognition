# Gap Analysis: Cognition vs. OpenDev Research Paper

**Reference**: "Building AI Coding Agents for the Terminal: Scaffolding, Harness, Context Engineering, and Lessons Learned" (arXiv:2603.05344v1, Nghi D. Q. Bui, OpenDev, March 2026)

**Date**: 2026-03-09

**Purpose**: Evaluate Cognition's architecture and implementation against the engineering decisions documented in OpenDev's technical report. Identify strengths, gaps, and a prioritized feature plan that stays true to Cognition's "batteries-included AI backend" mission.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Cognition Is Doing Right](#2-what-cognition-is-doing-right)
3. [Implementation & Performance Improvements](#3-implementation--performance-improvements)
4. [Priority Feature List](#4-priority-feature-list)
5. [Add-Ons: Complementary Projects & Middleware](#5-add-ons-complementary-projects--middleware)
6. [Scoping as a Library Primitive](#6-scoping-as-a-library-primitive)
7. [Appendix: Detailed Feature Mapping](#appendix-detailed-feature-mapping)

---

## 1. Executive Summary

OpenDev is a **terminal-native, single-user CLI agent** optimized for interactive coding sessions. Cognition is a **server-first, multi-user API backend** designed so that an agent definition alone is sufficient to generate an API, streaming, persistence, sandboxing, observability, and evaluation.

These are fundamentally different products with overlapping concerns. The paper surfaces engineering decisions that are universally applicable to long-running LLM agents regardless of deployment model. The most transferable lessons are:

- **Context engineering is not optional** — it is the primary determinant of agent reliability in sessions exceeding 15 tool calls.
- **Instruction fade-out is a reproducible failure mode** — not a theory. System reminders are a proven, low-cost mitigation.
- **Multi-model routing pays for itself** — using a cheap model for compaction and an expensive model for reasoning is an obvious win that Cognition does not yet exploit.
- **Tool richness matters less than tool correctness** — OpenDev's 9-pass fuzzy edit matching eliminated their single largest class of tool errors. The 35-tool catalog is secondary to the quality of the file-editing tool.

Cognition's advantages — multi-user scoping, REST/SSE API, provider fallback with circuit breakers, 7-layer architecture, hot-reloadable agent definitions — are genuine differentiators that OpenDev lacks entirely. These should not be compromised.

---

## 2. What Cognition Is Doing Right

### 2.1 Backend-First Architecture (Advantage: Strong)

Cognition's REST/SSE API with reconnection, replay buffers, and heartbeats (`server/app/api/sse.py`) means multiple frontends — CLI, web, IDE, mobile — can attach to the same agent backend. OpenDev is a monolithic CLI tool. This is Cognition's defining architectural advantage.

**Evidence**:
- `EventBuffer` (circular deque) for SSE replay on reconnection using `Last-Event-ID`
- `EventBuilder` with 11 typed event constructors
- Heartbeat timer via `asyncio.wait_for()` with configurable interval
- Clean separation: API layer (`server/app/api/`) never touches agent internals directly

### 2.2 General-Purpose Session Scoping (Advantage: Unique)

OpenDev is explicitly single-user. Cognition has scope-aware session isolation via opaque scope headers (`X-Cognition-Scope-User`, `X-Cognition-Scope-Project`) with fail-closed enforcement and per-scope session filtering. No terminal-native agent in the paper's survey offers this.

Critically, this scoping mechanism is **general-purpose, not user-specific**. See [Section 6: Scoping as a Library Primitive](#6-scoping-as-a-library-primitive) for an extended discussion of how this positions Cognition as an embeddable dependency where scoping serves multiple isolation dimensions beyond user identity.

### 2.3 Provider Resilience (Advantage: Strong)

`ProviderFallbackChain` (`server/app/llm/provider_fallback.py:54`) with per-provider circuit breakers is more robust than OpenDev's lazy-init model slots. OpenDev's fallback is provider → action model. Cognition's is provider → provider → provider, with exponential backoff (1s, 2s, 4s), configurable retry counts, and `CircuitBreaker` state machines (CLOSED → OPEN → HALF_OPEN → CLOSED).

### 2.4 Strict Layer Discipline (Advantage: Structural)

Cognition's 7-layer architecture with top-down-only dependency direction is more rigorous than OpenDev's 4-layer model. Every file in `server/app/` respects this:
```
Layer 7: Observability
Layer 6: API & Streaming
Layer 5: LLM Provider
Layer 4: Agent Runtime
Layer 3: Execution
Layer 2: Persistence
Layer 1: Foundation
```

OpenDev's layers (Entry & UI, Agent, Tool & Context, Persistence) are coarser and the paper does not enforce dependency direction as a hard constraint.

### 2.5 Pluggable Agent Definitions (Advantage: Strong)

`AgentDefinitionRegistry` (`server/app/agent/agent_definition_registry.py:35`) loads agent definitions from:
- Built-in defaults (`"default"`, `"readonly"`)
- User YAML/MD files in `.cognition/agents/`
- Configuration with `reload()` for hot-swap

The registry pattern with Markdown frontmatter parsing (`load_agent_definition_from_markdown()` at `definition.py:474`) is cleaner than OpenDev's `SubAgentSpec` TypedDict approach. It aligns directly with Cognition's north star:

```python
agent = AgentDefinition(tools=[...], skills=[...], system_prompt="...")
app = Cognition(agent)
app.run()
```

### 2.6 AST Security Scanning for User Tools (Advantage: Unique)

`AgentRegistry` (`server/app/agent_registry.py`) performs static analysis on user-uploaded tools:
- `BANNED_IMPORTS`: `subprocess`, `socket`, `ctypes`, `sys`, `eval`, `exec`
- `BANNED_OS_CALLS`: Dangerous `os.*` calls
- AST walk before tool loading

OpenDev's lifecycle hooks can block tool calls at runtime, but they don't inspect tool *code*. Cognition catches malicious tools before they execute.

### 2.7 Observability Stack (Advantage: Production-Grade)

- Prometheus histograms (`LLM_CALL_DURATION`) and counters (`TOOL_CALL_COUNT`) in `CognitionObservabilityMiddleware` (`middleware.py:77`)
- OpenTelemetry integration via `opentelemetry-*` dependencies
- LangSmith OTel bridge via `langsmith[otel]`
- MLflow experiment tracking setup
- Structured logging via `structlog`

OpenDev has no observability infrastructure. For a "batteries-included backend," this is table stakes and Cognition delivers it.

### 2.8 Persistence with Checkpointing (Advantage: Production-Grade)

Three storage backends sharing a `StorageBackend` protocol:
- SQLite (`aiosqlite` + `AsyncSqliteSaver`)
- PostgreSQL (`asyncpg` pool + `AsyncPostgresSaver`)
- In-memory (tests)

LangGraph checkpointing per `thread_id` enables conversation resumption. OpenDev persists session JSON files; Cognition has a proper database layer.

---

## 3. Implementation & Performance Improvements

These are improvements that do not require new features — they address gaps in existing code.

### 3.1 Critical: `agent_recursion_limit` is Too High

| Setting | Cognition | OpenDev |
|---------|-----------|---------|
| Main agent iteration limit | 1000 (default) | Not documented (but bounded by safety cap) |
| Subagent iteration limit | 1000 (same) | **15** |

**Problem**: A subagent with `recursion_limit=1000` can burn through the entire context window and token budget before anyone notices. OpenDev caps subagents at 15 iterations, which is sufficient for thorough investigation while preventing runaway execution.

**Recommendation**: Add `subagent_recursion_limit` to Settings (default: 25). Pass it to subagent construction in `AgentDefinition.to_subagent()`. Keep the main agent limit configurable but set a saner default (e.g., 100).

**Layer**: 4 (Agent Runtime)
**Effort**: 0.5 days
**Files**: `server/app/settings.py`, `server/app/agent/definition.py`, `server/app/agent/cognition_agent.py`

### 3.2 Critical: No Tool Output Truncation

OpenDev enforces:
- Per-tool output: 300 tokens max (offloaded to scratch file with 500-char preview)
- File reads: 30,000 chars with head-tail truncation (first 10k + last 10k)
- Per-line truncation: 2,000 chars
- Search results: 50 matches, 30,000 chars total

Cognition's built-in tools (`BrowserTool`, `SearchTool`, `InspectPackageTool`) have no output size limits. The `deepagents` tools handle truncation internally, but Cognition has no control over it and no consistency guarantee.

**Recommendation**: Add a `ToolOutputTruncationMiddleware` that enforces configurable limits on tool result content before it enters the conversation. This is a middleware concern, not a per-tool concern.

**Layer**: 4 (Agent Runtime) — middleware
**Effort**: 1 day
**Files**: New middleware in `server/app/agent/middleware.py`

### 3.3 High: No Prompt Caching

OpenDev's `compose_two_part()` splits system prompts into stable (19/21 sections) and dynamic (2/21 sections). The stable part gets Anthropic's `cache_control` header, yielding ~88% cost reduction on the cached portion.

Cognition sends the full system prompt every turn. For Anthropic (Bedrock or direct), this is a significant cost multiplier.

**Recommendation**: When `llm_provider` is `bedrock` with an Anthropic model, or a direct Anthropic provider, split the system prompt at the model factory level. The `ChatBedrock` and `ChatOpenAI` constructors accept message metadata — inject `cache_control: {"type": "ephemeral"}` on the static portion.

**Layer**: 5 (LLM Provider)
**Effort**: 1 day
**Files**: `server/app/llm/registry.py`

### 3.4 High: Ollama Provider Not Wired

Settings exist (`COGNITION_OLLAMA_MODEL`, `COGNITION_OLLAMA_BASE_URL` at `settings.py:74-78`) but no factory is registered in `server/app/llm/registry.py`. Users who configure Ollama get a silent failure.

**Recommendation**: Add `create_ollama_model()` factory using `ChatOllama` from `langchain_ollama`.

**Layer**: 5 (LLM Provider)
**Effort**: 0.5 days
**Files**: `server/app/llm/registry.py`, `pyproject.toml` (add `langchain-ollama` dependency)

### 3.5 High: Model Discovery Uses Hardcoded Lists

`DiscoveryEngine._probe_openai()` returns a hardcoded list of 3 models. `_probe_bedrock()` returns 2. These are stale and will stay stale.

OpenDev caches model capabilities locally with 24-hour TTL and background refresh (stale-while-revalidate).

**Recommendation**: Replace hardcoded lists with actual API calls to `/models` endpoints (OpenAI, Bedrock `list-foundation-models`). Cache results with TTL. Fall back to hardcoded list on failure.

**Layer**: 5 (LLM Provider)
**Effort**: 1 day
**Files**: `server/app/llm/discovery.py`

### 3.6 Medium: ROADMAP.md is Stale

Multiple items marked "Pending" or "In Progress" have working implementations:
- Rate limiting (implemented in `rate_limiter.py`)
- Health check endpoint (`/health` exists and works)
- Metrics and telemetry (OTel + Prometheus fully wired)
- Multi-user session isolation (scoping framework works)
- Graceful abort (`DeepAgentRuntime.abort()` implemented)

**Recommendation**: Audit and update ROADMAP.md status fields to reflect actual implementation state.

**Effort**: 0.5 days

### 3.7 Medium: Missing `client/tui/` Directory

`pyproject.toml` line 105 defines `cognition-client = "client.tui.app:main"` but `client/tui/` does not exist. This is a broken entry point.

**Recommendation**: Either implement the TUI app or remove the entry point.

**Effort**: Remove = 5 minutes. Implement = 3-5 days.

---

## 4. Priority Feature List

Features are grouped into tiers based on impact, alignment with Cognition's mission, and implementation effort. Each feature references the specific OpenDev mechanism it draws from.

### Tier 1: Critical (Address Reproducible Failure Modes)

These fix failure modes that **will** occur in any long-running agent session. They are not optional enhancements.

#### F1. Adaptive Context Compaction (ACC)

**OpenDev Reference**: Section 2.3.6, Algorithm 1, Figure 13, Table 9

**What**: A 5-stage pipeline that monitors context pressure at the start of every ReAct iteration and applies progressively aggressive reduction:

| Stage | Threshold | Action |
|-------|-----------|--------|
| 1 - Warning | 70% | Log utilization, no data reduction |
| 2 - Observation Masking | 80% | Replace older tool results with compact references |
| 2.5 - Fast Pruning | 85% | Delete old results beyond recency window |
| 3 - Aggressive Masking | 90% | Shrink preservation window to most recent only |
| 4 - Full Compaction | 99% | LLM-based summarization of conversation middle |

**Why**: Without this, Cognition silently degrades after ~30 tool calls as the context window fills. The paper reports ACC reduces peak context consumption by ~54% and often eliminates the need for emergency compaction entirely.

**Cognition Implementation Strategy**: Implement as `AgentMiddleware` that wraps `awrap_model_call()`. Use API-reported `prompt_tokens` from previous turn to calibrate utilization (not local token counting — this corrects for provider-side injections invisible to the client). The "compact model" role (see F3) should handle Stage 4 summarization cheaply.

**Layer**: 4 (Agent Runtime)
**Effort**: 5 days
**Dependencies**: F3 (per-workload model routing) for cheap compaction model
**ROADMAP Category**: Feature (P1)

---

#### F2. System Reminders (Event-Driven Instruction Reinforcement)

**OpenDev Reference**: Section 2.3.4, Figure 11, Figure 12, Section F (full catalog of 24 reminders)

**What**: Short, single-purpose messages injected as `role: user` at maximum recency, right before the LLM call where the agent would otherwise fail. 8 event detectors covering:

1. Tool failure without retry (6 error-specific recovery templates)
2. Exploration spirals (5+ consecutive reads)
3. Denied tool re-attempts
4. Premature completion with incomplete todos
5. Continued work after all todos are done
6. Plan approval without follow-through
7. Unprocessed subagent results
8. Empty completion messages

**Key Design Decisions**:
- `role: user` not `role: system` — experiments confirmed higher compliance rates because the model treats it as something requiring a response
- Guardrail counters: `MAX_TODO_NUDGES = 2`, `MAX_NUDGE_ATTEMPTS = 3`, one-shot flags for plan/completion signals
- Templates stored outside code (Markdown files) for auditability

**Why**: The paper demonstrates this is a **reproducible, predictable failure mode** observable after ~15 tool calls. Not hypothetical. The system prompt's influence fades with distance as the conversation grows.

**Cognition Implementation Strategy**: Implement as `AgentMiddleware` that wraps `awrap_model_call()`. Before each model call, run detector functions against the conversation state. Inject reminders as messages. Store templates in `.cognition/reminders/` or a bundled resource directory. Expose reminder configuration in `AgentDefinition` so users can customize per-agent.

**Layer**: 4 (Agent Runtime)
**Effort**: 3 days
**Dependencies**: None
**ROADMAP Category**: Feature (P1)

---

#### F3. Per-Workload Multi-Model Routing

**OpenDev Reference**: Section 2.2.5

**What**: Five model roles bound independently to user-configured LLMs:

| Role | Purpose | Fallback |
|------|---------|----------|
| Action | Primary tool-using execution | (required) |
| Thinking | Pre-action reasoning without tools | Action |
| Critique | Reflexion-style self-evaluation | Thinking → Action |
| Vision | Screenshot/image analysis | Action (if vision-capable) |
| Compact | Summarization during context compaction | Action |

**Why**: Using a $15/M-token model to summarize conversation history when a $0.15/M-token model would suffice is a 100x waste. Separating the thinking model from the action model prevents premature tool use (when tools are available, models tend to act rather than think).

**Cognition Implementation Strategy**: Extend `Settings` with `llm_thinking_model`, `llm_compact_model`, `llm_vision_model`, `llm_critique_model`. Extend `ProviderFallbackChain` to accept a `role` parameter. In `DeepAgentStreamingService.stream_response()`, create role-specific model instances. Pass the model map to the agent runtime.

**Layer**: 5 (LLM Provider)
**Effort**: 3 days
**Dependencies**: None
**ROADMAP Category**: Feature (P1)

---

### Tier 2: High (Differentiated Capability)

These features provide meaningful capability improvements and align with Cognition's backend-first mission.

#### F4. Modular Prompt Composition System

**OpenDev Reference**: Section 2.3.1, Section C, Tables 3-5, Section K

**What**: Replace the single static system prompt with a modular composition pipeline:
1. Register sections with priority, condition predicate, and cacheability flag
2. At render time: filter by condition → sort by priority → load from Markdown → join
3. Split into stable/dynamic parts for prompt caching

OpenDev uses 21 sections for the main agent, 4 for thinking mode. Conditions include `always`, `in_git_repo`, `has_subagents`, `todo_enabled`, `openai`/`anthropic`/`fireworks` (provider-specific guidance).

**Why**: Cognition's system prompt is a single string from `PromptConfig`. This means:
- No conditional inclusion (git-specific guidance loads even for non-git projects)
- No provider-specific tool-calling guidance
- No prompt caching (88% cost reduction lost)
- No separation between thinking prompts and action prompts

**Cognition Implementation Strategy**: Create a `PromptComposer` class in `server/app/agent/prompt_composer.py`. Register sections as data files in `server/app/agent/prompts/`. Wire into `create_cognition_agent()` where `system_prompt` is currently resolved. Expose the section registry in `AgentDefinition` so users can add/override sections.

**Layer**: 4 (Agent Runtime)
**Effort**: 3 days
**Dependencies**: None
**ROADMAP Category**: Feature (P1)

---

#### F5. Schema-Level Plan Mode (Subagent-Based)

**OpenDev Reference**: Section 2.2, Figure 5

**What**: Replace the current `readonly` agent definition (which uses `interrupt_on` runtime flags) with a subagent-based planner where **write tool schemas are structurally absent** from the LLM's view. The Planner cannot write because write tools don't exist in its schema, not because a runtime check blocks the attempt.

**Why**: The paper documents that a state-machine approach (enter/exit plan mode) was brittle — the agent sometimes failed to exit plan mode, leaving the system stuck in read-only state. Schema-level exclusion eliminates this failure mode entirely.

**Current Cognition State**: The `"readonly"` built-in agent uses `interrupt_on={"write_file": True, "edit_file": True, "execute": True}`. This is runtime interruption, not schema exclusion. The LLM still sees the tool definitions and may attempt to call them.

**Cognition Implementation Strategy**: Add a `Planner` agent definition with `allowed_tools` containing only read-only tools. The existing `AgentDefinition.to_subagent()` already supports `tools` filtering. This is largely a definition change, not a code change.

**Layer**: 4 (Agent Runtime)
**Effort**: 1 day
**Dependencies**: None
**ROADMAP Category**: Feature (P1)

---

#### F6. Task Management Tools

**OpenDev Reference**: Table 1 (Task Mgmt category)

**What**: Expose `write_todos`, `update_todo`, `complete_todo`, `list_todos` as first-class agent tools with enforcement:
- `update_todo` enforces a single "doing" constraint (only one task can be `in_progress`)
- `complete_todo` accepts an optional completion log
- Todo state is persisted per-session

**Why**: Without structured task tracking, agents lose track of multi-step plans. The system reminder for premature completion (F2) depends on having a machine-readable todo state to check against.

**Cognition Implementation Strategy**: Implement as deepagents tools bundled with Cognition's default agent. Store todo state in the session's persistence layer (not the LLM context). Expose via API so the CLI/web can render todo progress.

**Layer**: 4 (Agent Runtime) + Layer 6 (API)
**Effort**: 2 days
**Dependencies**: F2 (system reminders check todo state)
**ROADMAP Category**: Feature (P1)

---

#### F7. Doom-Loop Detection

**OpenDev Reference**: Algorithm 1 lines 22-28, Table 9

**What**: MD5-fingerprint each tool call `(name, args)`. Maintain a sliding window of the last 20 fingerprints. If any fingerprint appears >= 3 times, trigger an approval pause and inject a warning.

**Why**: Agents occasionally enter repetitive loops (re-reading the same file, re-running the same failing command). Without detection, this silently burns tokens and produces no progress. Simple, high-value safety guard.

**Cognition Implementation Strategy**: Implement as `AgentMiddleware` wrapping `awrap_tool_call()`. Zero dependencies on other features.

**Layer**: 4 (Agent Runtime)
**Effort**: 0.5 days
**Dependencies**: None
**ROADMAP Category**: Feature (P1)

---

### Tier 3: Medium (Strategic Enhancements)

These improve the platform but are not blocking any immediate failure modes.

#### F8. Adaptive Memory / Cross-Session Playbook (ACE)

**OpenDev Reference**: Section 2.3.6 (Memory), Figure 14

**What**: A 4-stage pipeline that accumulates project-specific knowledge across sessions:
1. **BulletSelector**: Scores playbook bullets by effectiveness (0.5), recency (0.3), semantic similarity (0.2)
2. **Reflector**: Every 5 messages, analyzes accumulated experience → reasoning trace + effectiveness tags
3. **Curator**: Plans mutations (add/update/tag/remove bullets) as `DeltaBatch`
4. **Persist**: Writes updated playbook to session-scoped JSON

**Cognition Implementation Strategy**: This is a natural fit for Cognition's persistence layer. Store the playbook in the database alongside sessions. The Reflector and Curator can be subagents using the compact model (F3). Inject selected bullets into the system prompt via the prompt composer (F4).

**Layer**: 2 (Persistence) + 4 (Agent Runtime)
**Effort**: 5 days
**Dependencies**: F3 (compact model for reflector/curator), F4 (prompt composer for injection)
**ROADMAP Category**: Feature (P2)

---

#### F9. Dual-Memory Thinking Context

**OpenDev Reference**: Section 2.3.3

**What**: When calling the thinking model, provide:
- **Episodic memory**: 500-char LLM-generated summary of the full conversation (regenerated every 5 messages, not incrementally — prevents summary drift)
- **Working memory**: Last 6 message pairs verbatim (exact file contents, error messages, line numbers)

This bounds the thinking token budget regardless of conversation length.

**Cognition Implementation Strategy**: If Cognition adopts a thinking model (F3), this is the mechanism that keeps it efficient. Implement as a context preparation step in the middleware that wraps the thinking model call.

**Layer**: 4 (Agent Runtime)
**Effort**: 2 days
**Dependencies**: F3 (thinking model)
**ROADMAP Category**: Feature (P2)

---

#### F10. Error-Classification Recovery Templates

**OpenDev Reference**: Section 2.3.5

**What**: Classify tool errors into 6 categories and inject specific recovery guidance:

| Error Type | Recovery Template |
|------------|-------------------|
| Permission denied | Request appropriate access or suggest alternative approach |
| File not found | Check path, suggest search, list directory |
| Edit mismatch | Re-read file, retry edit with current content |
| Syntax error | Show specific line, suggest fix |
| Rate limit | Wait and retry with backoff |
| Timeout | Retry with smaller scope or simpler approach |

**Why**: "The file has changed since you last read it; re-read and retry" is substantially more actionable than "try again."

**Cognition Implementation Strategy**: Part of F2 (system reminders). Store templates in Markdown files. Pattern-match error messages to categories.

**Layer**: 4 (Agent Runtime)
**Effort**: 1 day (bundled with F2)
**Dependencies**: F2
**ROADMAP Category**: Feature (P2)

---

#### F11. Stale-Read Detection for File Edits

**OpenDev Reference**: Section 2.4.2 (read_file handler)

**What**: Track `mtime` of each file read per session. Before any edit, verify `os.path.getmtime(file_path) <= read_time + 50ms`. Reject stale edits with a message instructing the agent to re-read.

**Why**: Prevents silent overwrites when a file changes between the agent's read and edit — e.g., if the user edits a file while the agent is mid-session.

**Cognition Implementation Strategy**: If Cognition controls its own file tools (not delegated to deepagents), implement in the edit tool handler. If delegated, implement as middleware that wraps file edit tool calls.

**Layer**: 3 (Execution) or 4 (Agent Runtime)
**Effort**: 1 day
**Dependencies**: None
**ROADMAP Category**: Feature (P2)

---

#### F12. Lifecycle Hooks (External Script Integration)

**OpenDev Reference**: Section 2.4.1 (Lifecycle hooks)

**What**: 10 lifecycle events that external scripts can observe or intercept:
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `PreCompact`, `SessionEnd`, `Stop`

- Blocking events (`PreToolUse`, `UserPromptSubmit`) can return exit code 2 to hard-block
- Scripts can mutate tool arguments via JSON stdout
- Hook matchers use compiled regex patterns against tool names

**Why**: Enables organization-wide policies (e.g., "run eslint after file edits," "block writes to protected paths") without modifying agent code.

**Cognition Implementation Strategy**: Cognition already has middleware. This extends it with an external script interface. Configure hooks in `.cognition/config.yaml`. Execute via `asyncio.create_subprocess_exec` (no `shell=True` per AGENTS.md rules). Pass event context as JSON on stdin.

**Layer**: 4 (Agent Runtime)
**Effort**: 3 days
**Dependencies**: None
**ROADMAP Category**: Feature (P2)

---

### Tier 4: Lower Priority (Future Enhancements)

#### F13. LSP Integration for Semantic Code Analysis

**OpenDev Reference**: Section 2.4.5, Figure 18

**What**: 6 tools backed by Language Server Protocol: `find_symbol`, `find_referencing_symbols`, `rename_symbol`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`. Supports 30+ languages via standard language servers (Pyright, tsserver, rust-analyzer, etc.).

**Why**: Text search can find strings but misses semantic structure. Finding all usages of a method requires distinguishing method calls from variable names, handling overloading, and tracking cross-file references.

**Cognition Implementation Strategy**: This is a large feature. Consider starting with Python-only support (Pyright) as a deepagents tool or MCP server. The MCP approach keeps it decoupled from the core.

**Layer**: 3 (Execution)
**Effort**: 10+ days for multi-language; 3 days for Python-only
**ROADMAP Category**: Feature (P3)

---

#### F14. Vision Tooling (Screenshot + VLM Analysis)

**OpenDev Reference**: Table 1 (Web category)

**What**: `capture_screenshot` (desktop), `capture_web_screenshot` (Playwright), `analyze_image` (VLM). Requires a vision model role (F3).

**Layer**: 3 (Execution)
**Effort**: 3 days
**Dependencies**: F3 (vision model)
**ROADMAP Category**: Feature (P3)

---

#### F15. Auto-Generated Project Context

**OpenDev Reference**: Project-Init subagent, Table 7

**What**: A `Project-Init` subagent that analyzes the codebase and auto-generates an `AGENTS.md`-equivalent file. Runs on first session in a new project.

**Why**: Reduces onboarding friction. Currently Cognition requires manual `AGENTS.md` authoring.

**Layer**: 4 (Agent Runtime)
**Effort**: 2 days
**ROADMAP Category**: Feature (P3)

---

## 5. Add-Ons: Complementary Projects & Middleware

These are not core features but complementary systems that can be developed as separate packages, MCP servers, or middleware plugins.

### 5.1 MCP Servers (External Tools via Model Context Protocol)

Cognition already supports MCP via `McpSseClient` (`server/app/agent/mcp_client.py`). The following can be built as standalone MCP servers and registered in `.cognition/config.yaml`:

| MCP Server | Purpose | Complexity |
|------------|---------|------------|
| **cognition-lsp-mcp** | LSP integration (F13) as an MCP server. Wraps language servers, exposes `find_symbol`, `rename_symbol`, etc. Keeps LSP complexity out of core. | Medium |
| **cognition-git-mcp** | Safe git operations: commit, branch, diff, log, PR creation. Schema-level safety (no force-push tool). | Low |
| **cognition-docker-mcp** | Container management: build, run, logs, stop. Useful for agents managing deployments. | Low |
| **cognition-browser-mcp** | Playwright-based browser automation: screenshot, navigate, click, fill forms. Richer than the current `BrowserTool`. | Medium |
| **cognition-notebook-mcp** | Jupyter notebook operations: create cells, execute, inspect outputs. Mirrors OpenDev's `notebook_edit`. | Low |

### 5.2 Agent Middleware Packages

Cognition's middleware system (`AgentMiddleware` from `langchain.agents.middleware.types`) supports pluggable middleware. The following can be published as separate packages:

| Package | Purpose | Maps to OpenDev Feature |
|---------|---------|------------------------|
| **cognition-context-compactor** | Adaptive Context Compaction (F1). Can be developed and tested independently, then registered as middleware. | ACC (Section 2.3.6) |
| **cognition-system-reminders** | Event-driven instruction reinforcement (F2). Detectors + templates + guardrail counters as a middleware package. | Reminders (Section 2.3.4) |
| **cognition-doom-loop-guard** | Doom-loop detection (F7). Minimal, single-purpose middleware. | Algorithm 1 lines 22-28 |
| **cognition-output-truncator** | Tool output truncation (3.2). Enforces per-tool and global output size limits. | Section 2.4.2 |
| **cognition-cost-tracker** | Per-session token usage and cost tracking. Cumulative totals persisted in session metadata. | Section 2.2.8 (CostTracker) |
| **cognition-stale-read-guard** | Stale-read detection (F11). Tracks file mtimes, rejects stale edits. | Section 2.4.2 |

### 5.3 Complementary Projects

| Project | Purpose | Relationship to Cognition |
|---------|---------|---------------------------|
| **cognition-eval** | Evaluation framework. Runs benchmark suites (SWE-bench, Terminal-Bench, custom scenarios) against Cognition sessions. Measures ACC hit rates, reminder compliance, tool error rates. | Consumes Cognition's API. P3 roadmap item. |
| **cognition-memory** | Adaptive memory service (F8). Standalone service with BulletSelector, Reflector, Curator stages. Stores playbooks. Cognition queries it for relevant bullets per session. | Accessed via internal API or middleware. |
| **cognition-prompt-lab** | Prompt composition development tool. Preview how sections compose for different contexts (git/no-git, provider, thinking mode). A/B test prompt variations against eval harness. | Development tooling. |
| **cognition-tui** | The missing `client/tui/` implementation. Full Textual-based TUI with rich rendering, modal approvals, real-time todo display, cost tracking. Connects to Cognition's SSE API. | Client-side. Separate package. |
| **cognition-web** | Web frontend for Cognition. React/Next.js app consuming the REST/SSE API. Chat interface, session management, model switching, real-time streaming. | Client-side. Separate repo. |

### 5.4 Deep Agents Upstream Contributions

Some features are better implemented in `deepagents` (the upstream library Cognition wraps) rather than in Cognition itself:

| Feature | Why Upstream |
|---------|--------------|
| Fuzzy edit matching (9-pass chain) | File editing is a deepagents concern. All deepagents users would benefit. |
| Background process management (PTY-based) | Shell execution is a deepagents sandbox concern. |
| Parallel tool execution (up to 5 concurrent) | Tool dispatch is a deepagents runtime concern. |
| `ask_user` as a structured tool | Human-in-the-loop interaction is a deepagents middleware concern. |

These should be filed as feature requests or PRs against the deepagents repository rather than reimplemented in Cognition's layer.

---

## 6. Scoping as a Library Primitive

### 6.1 Cognition's Intended Deployment Model

Cognition is designed as a **library/dependency** that application developers embed, not a hosted multi-tenant platform. The embedding application owns identity, authorization, and billing. Cognition provides session isolation as a building block — a general-purpose scoping mechanism that the consumer wires to their own domain model.

This distinction fundamentally changes how "multi-user" should be evaluated. Cognition does not need authentication, user management, or billing. It needs **correct, opaque session scoping** that the embedding application can use for whatever isolation boundary it requires.

### 6.2 Scoping Beyond User Identity

The current scope headers (`X-Cognition-Scope-User`, `X-Cognition-Scope-Project`) suggest user-centric isolation, but scope is a more general concept. Application developers embedding Cognition may scope sessions along any dimension their domain requires:

| Scope Dimension | Example | Header Usage |
|-----------------|---------|--------------|
| **User** | Developer A's sessions are isolated from Developer B's | `X-Cognition-Scope-User: user-a` |
| **Project/Repository** | Sessions for `repo-frontend` are separate from `repo-backend` | `X-Cognition-Scope-Project: repo-frontend` |
| **Tenant** | SaaS application isolating customer A from customer B | `X-Cognition-Scope-User: tenant-123` (overloaded) |
| **Environment** | Production agent sessions separate from staging | Requires a new scope dimension |
| **Team/Organization** | Shared sessions within a team, isolated across teams | Requires a new scope dimension |
| **Feature Branch** | Sessions scoped to a specific branch or PR | Requires a new scope dimension |
| **Agent Role** | Sessions for a "code review" agent isolated from a "deployment" agent | Could use `X-Cognition-Scope-Project` or new dimension |

The current two-dimensional scope (`user` + `project`) covers the most common cases. But a library consumed by diverse applications will eventually encounter use cases that need additional scope dimensions or arbitrary scope composition.

### 6.3 Design Implications

#### What the current implementation gets right

- **Opaque scope values**: Cognition doesn't interpret scope values — they're arbitrary strings. The embedding app assigns meaning. This is correct for a library.
- **Fail-closed enforcement**: When `scoping_enabled=True`, missing scope headers reject the request. This prevents accidental cross-scope data leakage.
- **Storage-layer filtering**: Scope filtering happens at the query level in `list_sessions()` and `get_session()`. This is defense-in-depth — bypassing the API middleware doesn't bypass the storage filter.

#### What should be hardened for library consumers

1. **Scope as a first-class contract, not an implementation detail.** The scoping behavior should be documented as a public API guarantee with semantic versioning implications. If a consumer depends on scope isolation for data privacy, breaking that contract is a breaking change regardless of whether it's a "feature" or a "bug fix."

2. **Extensible scope dimensions.** The current two-header approach (`User` + `Project`) is sufficient for now, but the storage layer should not assume exactly two dimensions. Consider accepting arbitrary scope key-value pairs (e.g., `X-Cognition-Scope: user=alice, project=frontend, env=staging`) or a structured scope object at session creation. This allows consumers to add isolation dimensions without Cognition needing to define new headers for each.

3. **Scope propagation to all data.** Scope must filter not just sessions and messages, but every data type Cognition persists or exposes:
   - Sessions and messages (currently scoped)
   - Adaptive memory / playbooks (F8) — if implemented, must follow scope
   - Todo state (F6) — per-session, so inherits session scope automatically
   - Evaluation results — if implemented, must follow scope
   - Observability data (metrics, traces) — should carry scope labels for consumer-side filtering

4. **Scope immutability.** Once a session is created with a scope, that scope should never change. This prevents a class of bugs where scope reassignment causes data leakage. The current implementation should enforce this (reject PATCH requests that attempt to modify scope fields).

#### What stays out of scope for Cognition

| Concern | Owner | Rationale |
|---------|-------|-----------|
| Authentication (who is this user?) | Embedding application | Cognition is a library, not an identity provider |
| Authorization (can this user use this agent?) | Embedding application | Policy is domain-specific |
| Billing / cost attribution | Embedding application | Cognition exposes usage data per session; aggregation is the consumer's job |
| Rate limiting per user | Embedding application via Cognition config | Cognition provides rate limiting machinery; the consumer configures limits per scope |
| Audit logging of scope access | Embedding application | Cognition can emit structured logs with scope labels; the consumer routes them |

### 6.4 Impact on Gap Analysis Features

The library/scoping model simplifies the multi-user dimension of every feature in this report:

| Feature | Multi-Tenant Platform Concern | Library/Scoping Concern |
|---------|-------------------------------|------------------------|
| F1 (Context Compaction) | Resource economics across N users | Session-level quality; consumer manages capacity |
| F7 (Doom-Loop Detection) | Unattended runaway resource protection | Safety guard; consumer sets session timeouts |
| F8 (Adaptive Memory) | Per-user vs. per-team playbook privacy | Follows session scope automatically |
| Cost Tracking | Billing per user | Expose `UsageEvent` data; consumer aggregates |
| Session Resource Limits | Server-wide fairness enforcement | Configurable per session at creation time |

In every case, the library model pushes multi-user complexity to the consumer and keeps Cognition focused on **single-session correctness**. Features should be designed and tested for one session working correctly. Scope isolation ensures that N correct sessions remain correctly isolated.

### 6.5 Recommendations

1. **Near-term**: Document scoping as a public API contract. Add tests verifying scope immutability and scope propagation to all storage queries. No code changes needed beyond test coverage.

2. **Medium-term**: Generalize scope headers to support arbitrary key-value dimensions. Refactor storage queries to filter on a scope map rather than two specific fields. This is a Layer 2 (Persistence) change.

3. **Long-term**: As new data types are added (playbooks, evaluation results, agent-generated artifacts), ensure each type carries scope metadata and respects scope filtering. This should be a checklist item in the Definition of Done for any feature that introduces a new persisted data type.

---

## Appendix: Detailed Feature Mapping

### A.1 Full Comparison Matrix

| Dimension | Cognition | OpenDev | Gap Severity |
|-----------|-----------|---------|--------------|
| **Context Engineering** | | | |
| Adaptive Context Compaction | Not implemented | 5-stage ACC (70/80/85/90/99%) | Critical |
| Context pressure monitoring | Not implemented | API-reported `prompt_tokens` calibration | Critical |
| Observation masking / pruning | Not implemented | Multi-stage with recency windows | Critical |
| Artifact index in compaction | Not implemented | Files touched + operations tracked | High |
| **Instruction Management** | | | |
| System reminders | Not implemented | 8 detectors, 24 templates, guardrail counters | Critical |
| Modular prompt composition | Single static string | 21 sections, conditional, priority-sorted | High |
| Prompt caching | Not implemented | ~88% cost reduction (Anthropic) | High |
| Provider-specific prompt sections | Not implemented | OpenAI/Anthropic/Fireworks-specific guidance | Medium |
| **Model Architecture** | | | |
| Multi-model routing | Single model + fallback | 5 roles (action/thinking/critique/vision/compact) | High |
| Thinking phase (tool-free reasoning) | Not implemented | Configurable depth (OFF/LOW/MEDIUM/HIGH) | High |
| Self-critique (Reflexion-style) | Not implemented | At HIGH thinking level | Medium |
| Dual-memory for thinking context | Not implemented | Episodic (500 char) + working (last 6) | Medium |
| **Tool System** | | | |
| Built-in tools | 3 (webfetch, websearch, inspect_package) | 35 across 12 categories | High |
| Fuzzy edit matching | Delegated to deepagents | 9-pass chain-of-responsibility | High |
| Task management tools | Not implemented | 4 tools with single-doing constraint | High |
| LSP semantic code analysis | Not implemented | 6 tools, 30+ languages | Medium |
| Background process management | Not implemented | PTY-based, server auto-detection | Medium |
| Vision tools | Not implemented | screenshot + VLM analysis | Low |
| **Safety** | | | |
| Tool output truncation | Not enforced | 300 tokens per tool, head-tail strategy | High |
| Doom-loop detection | Not implemented | MD5 fingerprint, 3-in-20 threshold | Medium |
| Stale-read detection | Not implemented | mtime tracking, 50ms tolerance | Medium |
| Lifecycle hooks (external scripts) | Not implemented | 10 events, blocking + non-blocking | Medium |
| **Planning** | | | |
| Plan mode | Runtime interrupt (`interrupt_on`) | Schema-level exclusion (subagent-based) | Medium |
| **Memory** | | | |
| Cross-session adaptive memory | Static files (`AGENTS.md`) | 4-stage ACE playbook pipeline | Medium |
| **Evaluation** | | | |
| Benchmark/eval pipeline | Not implemented | ACC hit rates, compaction metrics | Low (P3) |
| **Multi-User / API** | | | |
| REST/SSE API | Full implementation | Not applicable (CLI only) | Cognition leads |
| General-purpose session scoping | Implemented (opaque, extensible) | Not applicable (single-user) | Cognition leads |
| SSE reconnection/replay | `EventBuffer` with `Last-Event-ID` | Not applicable | Cognition leads |
| **Persistence** | | | |
| Database-backed storage | SQLite + Postgres + Memory | JSON files | Cognition leads |
| LangGraph checkpointing | Per thread_id | Session JSON only | Cognition leads |
| **Resilience** | | | |
| Provider fallback chain | Circuit breaker + retry + multi-provider | Lazy init with single fallback | Cognition leads |
| **Observability** | | | |
| Prometheus + OTel + MLflow | Implemented | Not implemented | Cognition leads |
| **Security** | | | |
| AST scanning of user tools | Implemented | Not implemented | Cognition leads |

### A.2 Implementation Constants Reference (from OpenDev Table 9)

These are calibrated values from OpenDev's production use. Adopt as starting defaults:

| Constant | OpenDev Value | Recommended Cognition Default | Notes |
|----------|---------------|-------------------------------|-------|
| Compaction warning threshold | 70% | 70% | Log only |
| Compaction masking threshold | 80% | 80% | Replace old tool outputs |
| Compaction pruning threshold | 85% | 85% | Delete beyond recency |
| Compaction aggressive threshold | 90% | 90% | Shrink recency window |
| Compaction full threshold | 99% | 95% | LLM summarization (lower threshold is safer) |
| Max concurrent tools | 5 | 5 | Parallelism cap |
| Subagent iteration limit | 15 | 25 | Slightly higher for server context |
| Main agent safety cap | Not documented | 100 | Currently 1000 — dangerously high |
| Max tool result length | 300 tokens | 500 tokens | Slightly higher for API use |
| Doom-loop threshold | 3 repeats | 3 repeats | Match OpenDev |
| Doom-loop window | 20 calls | 20 calls | Match OpenDev |
| Summary regeneration interval | Every 5 messages | Every 5 messages | Match OpenDev |
| Working memory window | Last 6 exchanges | Last 6 exchanges | Match OpenDev |
| Episodic memory max length | 500 characters | 500 characters | Match OpenDev |
| Max nudge attempts | 3 per error | 3 per error | Match OpenDev |
| Max todo nudges | 2 per run | 2 per run | Match OpenDev |
| Provider cache TTL | 24 hours | 24 hours | Match OpenDev |

---

*This report should be updated as features are implemented. Reference specific OpenDev paper sections for implementation details. New features that introduce persisted data types must respect session scoping (see Section 6).*
