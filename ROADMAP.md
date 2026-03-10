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

---

## Performance Improvements

| Description | Target Metric | Before | After | Layer | Status |
|-------------|---------------|--------|-------|-------|--------|
| Strict mypy type checking for all production code (`server/`, `client/`) | Production mypy errors | ~341 errors | 0 errors | 1–6 | Completed |

---

## Dependency Updates

| Package | From | To | Breaking Changes | Status |
|---------|------|-----|------------------|--------|
| CI/CD Docker Images | N/A | N/A | None | Completed |

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
| LLM provider fallback | Layer 5 | In Progress | If primary provider fails, fallback to secondary | 2 days | None |
| Connection pooling | Layer 2 | Pending | Database connections pooled; no connection leaks | 1 day | None |
| Health check endpoint | Layer 6 | Pending | `/health` returns 200 when all deps ready | 0.5 days | None |
| Metrics and telemetry | Layer 7 | Pending | Prometheus metrics; OpenTelemetry traces | 2 days | None |

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

**Last Updated**: 2026-03-10
