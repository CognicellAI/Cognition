# Cognition Roadmap

This roadmap tracks the path toward Cognition's "batteries-included AI backend" vision.

All work is categorized by type: Security Fixes, Bug Fixes, Performance Improvements, Dependency Updates, and Features (P0-P3 tiers).

---

## Work Categories

See AGENTS.md for category definitions, DoD requirements, and precedence rules.

---

## Security Fixes

| Date | Description | Severity | Layer | Status |
|------|-------------|----------|-------|--------|
| | | | | |

---

## Bug Fixes

| Date | Description | Issue | Layer | Status |
|------|-------------|-------|-------|--------|
| 2026-03-06 | Fix dead code (duplicate kwargs block) in `llm/registry.py` | - | 5 | Completed |
| 2026-03-06 | Fix `StorageBackend` protocol — `create_message` missing `tool` role and `tool_call_id`; `MemoryStorageBackend` missing `status`/`agent_name` in `update_session` | - | 2 | Completed |
| 2026-03-06 | Fix `agent_definition_registry.py` — `.list()` method shadowing builtin `list` type, causing mypy `valid-type` errors; renamed to `.get_all()` | - | 4 | Completed |
| 2026-03-10 | Remove dead/unenforced settings: `ollama_model`, `ollama_base_url`, `max_sessions`, `session_timeout_seconds`, `llm_temperature`, `docker_timeout`, `protected_paths`, `prompt_source`, `prompt_fallback_to_local`, `prompts_dir`, `test_llm_mode` | - | 1 | Completed |
| 2026-03-10 | Fix missing `await` on `create_cognition_agent` in `runtime.py` and `session_manager.py` — coroutine was stored unresolved, silently breaking MCP and any path through those callers | - | 4 | Completed |
| 2026-03-10 | Harden Bedrock factory: partial key pair (only one of key/secret) now raises clear `ValueError` instead of silently producing a broken `ChatBedrock` instance | - | 5 | Completed |
| 2026-03-10 | Wire `AgentConfig.recursion_limit` into `create_agent_runtime` (per-agent override) and `SessionConfig.recursion_limit` into `DeepAgentService` / storage backends; `SessionConfig.max_tokens` override also wired through to `llm_settings` | - | 2/4 | Completed |
| 2026-03-10 | Fix `DoneEvent` fired twice: remove pass-through `elif isinstance(event, DoneEvent): yield event` in `deep_agent_service.py`; service already emits its own authoritative `DoneEvent` after `UsageEvent` | - | 6 | Completed |
| 2026-03-10 | Fix streaming content doubled: remove redundant `on_chain_stream`/`model` branch and unused `_streamed_via_model_stream` flag from `runtime.py`; `on_chat_model_stream` is the canonical token event in LangGraph v2 | - | 4 | Completed |
| 2026-03-10 | Fix `UsageEvent.model` always reporting `gpt-4o` default: use `_get_model_id(llm_settings)` (already in `provider_fallback.py`) instead of `llm_settings.llm_model` so Bedrock users see their actual `bedrock_model_id` | - | 6 | Completed |
| 2026-03-18 | Fix `asyncpg` JSONB columns returning raw strings in `PostgresConfigRegistry` and `PostgresListenDispatcher` — added `_scope_from_json()` / `_def_from_json()` safe-parse helpers | - | 2 | Completed |
| 2026-03-18 | Fix SQLAlchemy DSN format mismatch: strip `+asyncpg` driver prefix before passing to raw `asyncpg` in `factory.py` | - | 2 | Completed |
| 2026-03-18 | Fix scope header not applied to list/get endpoints in `skills.py`, `models.py`, `agents.py` routes — added optional `extract_scope` dependency to all read and write endpoints | - | 6 | Completed |
| 2026-03-19 | Fix scope not propagated from `send_message` route through `agent_event_stream` → `stream_response` → `create_cognition_agent` — `ConfigRegistrySkillsBackend` always received `scope=None`, making all user-scoped API skills invisible to the agent | - | 4/6 | Completed |
| 2026-03-18 | Fix `schema.py` `scope` columns as plain `JSON` preventing B-tree UNIQUE index on Postgres — replaced with `_JsonbOrJson` TypeDecorator (JSONB on Postgres, JSON on SQLite) | - | 2 | Completed |

---

## Performance Improvements

| Description | Target Metric | Before | After | Layer | Status |
|-------------|---------------|--------|-------|-------|--------|
| Strict mypy type checking for all production code (`server/`, `client/`) | Production mypy errors | ~341 errors | 0 errors | 1–6 | Completed |
| Ruff lint cleanup for `feature/config-registry` — unused imports, unsorted import blocks, unused `type: ignore` comments (12 auto-fixed; 10 mypy suppressions removed as stubs are now available) | Lint/mypy errors | 12 ruff + 10 mypy | 0 | 1–6 | Completed |

---

## Dependency Updates

| Package | From | To | Breaking Changes | Status |
|---------|------|-----|------------------|--------|
| CI/CD Docker Images | N/A | N/A | None | Completed |
| `psycopg2-binary` → `psycopg[binary,pool]` | psycopg2-binary (any) | psycopg 3.x | API surface change (psycopg3 vs psycopg2); only affects import paths, not used directly | Completed |

### CI/CD Docker Image Builds

**Description**: Automated Docker image builds on GitHub releases

**Changes**:
- Build `ghcr.io/cognicellai/cognition` and `ghcr.io/cognicellai/cognition-sandbox` images
- Multi-arch support: linux/amd64 + linux/arm64
- Tagged with: semver (v1.2.3), major.minor (v1.2), major (v1), and latest
- OCI labels for version, build date, and commit SHA
- GitHub Container Registry (ghcr.io) integration

**Files Modified**:
- `.github/workflows/ci.yml` - Added build-images job
- `Dockerfile` - Added OCI labels and build args
- `Dockerfile.sandbox` - Added OCI labels and build args

---

# Features (P0-P3 Tiers)

## Priority Definitions

- **P0 (Table Stakes)**: Must-have for basic functionality. Blocks other feature work.
- **P1 (Production-Ready)**: Required for safe, reliable production use.
- **P2 (Robustness)**: Edge cases, resilience, scalability improvements.
- **P3 (Full Vision)**: Advanced features and complete architectural vision.

---

## P0: Table Stakes (Blocking)

| Task | Layer | Status | Acceptance Criteria | Effort | Dependencies |
|------|-------|--------|---------------------|--------|--------------|
| Message persistence (SQLite/Postgres) | Layer 2 | In Progress | Messages survive server restart; SQLite works; Postgres works | 2 days | None |
| Session lifecycle management | Layer 2 | In Progress | Sessions can be created, listed, retrieved, deleted | 1 day | None |
| Basic tool execution security | Layer 3 | In Progress | No shell=True; no arbitrary code execution | 1 day | None |

---

## P1: Production-Ready

| Task | Layer | Status | Acceptance Criteria | Effort | Dependencies |
|------|-------|--------|---------------------|--------|--------------|
| **Configurable agent recursion_limit and max_tokens** | Layer 4 | Completed | `agent_recursion_limit` configurable via env/Settings/YAML (default: 1000); `llm_max_tokens` wired to model factories (default: 20000) | 0.5 days | None |
| **MCP server wiring** | Layer 4/5 | Completed | `COGNITION_MCP_SERVERS` env var / YAML key configures remote MCP servers; each entry validated (HTTP/HTTPS only) at startup; tools from MCP servers available to agent in all execution paths | 0.5 days | None |
| **Bedrock IAM role support** | Layer 5 | Completed | Ambient credentials (instance profile, ECS task role, Lambda, IRSA) work without any key configuration; `COGNITION_BEDROCK_ROLE_ARN` enables cross-account role assumption; `AWS_SESSION_TOKEN` supported for STS temp credentials; partial key pair raises clear error | 0.5 days | None |
| **Wire rate_limit_per_minute / rate_limit_burst to RateLimiter** | Layer 6 | Completed | `COGNITION_RATE_LIMIT_PER_MINUTE` and `COGNITION_RATE_LIMIT_BURST` are actually enforced by the rate limiter (previously reported in `/config` but ignored) | 0.25 days | None |
| Multi-user session isolation | Layer 2 | Pending | Users can only see/access their own sessions | 2 days | P0: Session lifecycle |
| Graceful abort/cancellation | Layer 4 | Pending | Abort button immediately stops execution; no zombie processes | 1 day | P0: Session lifecycle |
| Proper error propagation | Layer 6 | Pending | Errors from tools/agents bubble up with context; client sees meaningful messages | 1 day | None |
| Rate limiting | Layer 6 | Pending | Per-user and global rate limits enforced | 1 day | None |

### P1-1: Configurable Agent Parameters

**Layer**: Layer 4 (Agent Runtime)

**Problem**: The agent has hardcoded limits that cannot be configured:
- `recursion_limit`: Not exposed to users (deepagents hardcodes 1000)
- `max_tokens`: Setting exists but is not wired to LLM model instantiation

**Solution**: Wire both settings through the entire stack:
1. Add `agent_recursion_limit` to Settings (default: 1000)
2. Set `llm_max_tokens` default to 20000 and wire to registry factories
3. Pass `recursion_limit` to DeepAgentRuntime for LangGraph config

**Acceptance Criteria**:
- [x] `COGNITION_AGENT_RECURSION_LIMIT` env var sets recursion_limit (default: 1000)
- [x] `COGNITION_LLM_MAX_TOKENS` env var sets max_tokens (default: 20000)
- [x] `agent.recursion_limit` works in `.cognition/config.yaml`
- [x] `llm.max_tokens` works in `.cognition/config.yaml`
- [x] `recursion_limit` appears in LangGraph config dict passed to astream_events/ainvoke
- [x] `max_tokens` is passed to ChatOpenAI, ChatBedrock, etc. constructors
- [x] Per-agent `AgentDefinition.config.recursion_limit` overrides global setting (wired in `create_agent_runtime`)
- [x] Per-session `SessionConfig.max_tokens` and `SessionConfig.recursion_limit` override global setting (wired in `DeepAgentService`); both survive storage round-trip

**Files Modified**:
- `server/app/settings.py`
- `server/app/config_loader.py`
- `server/app/llm/registry.py`
- `server/app/agent/runtime.py`
- `server/app/llm/deep_agent_service.py`
- `server/app/agent/definition.py`
- `config.example.yaml`

---

## P2: Robustness

| Task | Layer | Status | Acceptance Criteria | Effort | Dependencies |
|------|-------|--------|---------------------|--------|--------------|
| **Dynamic ConfigRegistry (hot-reloadable agent config)** | Layers 1–6 | Completed | Agent/model/provider config changeable via API without restart; scoped per user/project; DB-backed with file bootstrap | 5–7 days | P0: Persistence, P1: Scoping |
| LLM provider fallback | Layer 5 | In Progress | If primary provider fails, fallback to secondary | 2 days | None |
| Connection pooling | Layer 2 | Pending | Database connections pooled; no connection leaks | 1 day | None |
| Health check endpoint | Layer 6 | Pending | `/health` returns 200 when all deps ready | 0.5 days | None |
| Metrics and telemetry | Layer 7 | Pending | Prometheus metrics; OpenTelemetry traces | 2 days | None |

---

## P2-1: Dynamic ConfigRegistry (Hot-Reloadable Agent Configuration)

**Layer**: Layers 1–6 (Foundation → API)

**Problem**: All agent, model, and provider configuration is locked into `Settings` (environment variables / YAML at startup). There is no way to change agent definitions, LLM providers, skills, tools, or system prompts without a server restart. Multi-tenant deployments have no way to scope config per user or project.

**Solution**: Replace the agent/model/provider fields in `Settings` with a `ConfigRegistry` — a DB-backed, hot-reloadable, scoped configuration store. File-based bootstrapping (`.cognition/config.yaml`, agent YAML/MD files) seeds the registry on first startup with merge semantics (DB wins on restart). Built-in agents (`default`, `readonly`) are always overwritten by built-ins at startup. A `ConfigChangeDispatcher` invalidates in-process caches on every write — zero latency on SQLite (same process), near-real-time on Postgres via `LISTEN/NOTIFY`.

**Settings Split**:
- Keep in `config.yaml` / `Settings`: server, persistence, sandbox, cors, scoping, observability, rate_limit, security, workspace_root
- Move to `ConfigRegistry`: llm_provider, llm_model, llm_max_tokens, openai_*, bedrock_*, mcp_servers, agent_memory, agent_skills, agent_subagents, agent_interrupt_on, agent_recursion_limit, system_prompt

**New DB Tables**:
- `config_entities`: `(id, entity_type, name, scope JSON, definition JSON, source "file"|"api", created_at, updated_at)` — indexed on `(entity_type, name, scope)`
- `config_changes`: `(id, entity_type, name, scope JSON, operation "upsert"|"delete", changed_at)` — drives invalidation

**Hot-Reload Strategy**:
- SQLite (single instance): `InProcessDispatcher` — pub/sub in same process, change is live before API response returns
- Postgres (multi-instance): `PostgresListenDispatcher` with persistent `LISTEN cognition_config_changes` connection — no external broker needed

**API Surface Added**:
```
POST/PUT/PATCH/DELETE /agents/{name}
POST/DELETE           /tools/{name}
GET/POST/PUT/DELETE   /skills, /skills/{name}
POST/PATCH/DELETE     /models/providers, /models/providers/{id}
```
All write endpoints respect `X-Cognition-Scope-{key}` headers for multi-tenant scoping.

**Acceptance Criteria**:
- [x] Agent definition, LLM provider, skills, MCP servers, and system prompt can be changed via API without server restart
- [x] Changes are reflected in the next request (< 100ms propagation on SQLite, < 1s on Postgres)
- [x] Scope resolution: scope-level config → global defaults (walk up hierarchy)
- [x] File bootstrap: `.cognition/config.yaml` + agent YAML/MD files seed registry on first startup; DB wins on subsequent restarts
- [x] Built-in agents (`default`, `readonly`) are always re-seeded from code on startup
- [x] Credentials (`OPENAI_API_KEY`, `AWS_*`) remain env vars only — never stored in DB or config.yaml
- [x] `PATCH /config` restricted to infrastructure fields only (server, persistence, sandbox, cors, etc.)
- [x] All existing tests pass; new tests cover CRUD, scope resolution, hot-reload invalidation, and dispatcher
- [x] `Settings` fields being removed (`llm_*`, `openai_*`, `bedrock_*`, `mcp_servers`, `agent_*`, `system_prompt`) are deleted (not deprecated)
- [x] ROADMAP.md updated as part of PR

**Effort**: 5–7 days

**Dependencies**: P0: Message persistence, P1: Multi-user session isolation (scope infrastructure)

**Files**:
- New: `server/app/storage/config_models.py`, `config_registry.py`, `config_dispatcher.py`
- Modified: `server/app/storage/schema.py`, `factory.py`, `migrations.py`
- Modified: `server/app/agent/agent_definition_registry.py`, `cognition_agent.py`
- Modified: `server/app/llm/deep_agent_service.py`, `provider_fallback.py`
- Modified: `server/app/api/routes/agents.py`, `tools.py`, `config.py`, `models.py`
- New: `server/app/api/routes/skills.py`
- Modified: `server/app/settings.py`, `config_loader.py`, `main.py`
- New: `tests/unit/test_config_registry.py`, `test_config_dispatcher.py`
- New: `tests/unit/api/test_agents_crud.py`, `test_tools_crud.py`, `test_skills_crud.py`, `test_providers_crud.py`

---

## P3: Full Vision

| Task | Layer | Status | Acceptance Criteria | Effort | Dependencies |
|------|-------|--------|---------------------|--------|--------------|
| Multi-agent orchestration | Layer 4 | Pending | Primary agent can delegate to subagents seamlessly | 3 days | P1: Configurable agent params |
| Skills progressive disclosure | Layer 4 | Pending | Skills loaded on-demand based on context | 2 days | None |
| GraphQL API | Layer 6 | Pending | Alternative to REST for complex queries | 3 days | P1: Production-Ready |
| Evaluation framework | Layer 7 | Pending | Automated benchmark runs on agent performance | 5 days | P1: Production-Ready |
| `app = Cognition(agent); app.run()` | All | Pending | Single-line instantiation provides all features | 5 days | All above |

---

## Roadmap Governance

Per AGENTS.md requirements:

1. **ROADMAP.md Structure**: Organized by work category (Security, Bug, Performance, Dependency, Features).
2. **Work Categories**: All six categories tracked here with appropriate detail levels.
3. **Precedence**: Security fixes override all. Bug fixes > Features. Performance/Dependency can proceed alongside Features. Feature tiers: P0 > P1 > P2 > P3.
4. **When to Update**:
   - Features/Architectural: Before starting work
   - Security/Bug/Performance/Dependency: As part of PR

**Last Updated**: 2026-03-18
