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
| 2026-05-27 | Resolve open Dependabot alerts in `uv.lock` by upgrading `GitPython`, `Mako`, `urllib3`, `python-multipart`, `idna`, and `langchain-openai` to patched versions | High | 1 | Completed |
| 2026-05-21 | K8s sandbox shell injection in `_upload_files_via_execute` — file paths interpolated into shell command without `shlex.quote()` | High | 3 | Planned |
| 2026-05-21 | K8s sandbox thread-unsafe lazy init in `_ensure_sandbox()` — concurrent calls can create duplicate sandbox CRs | Medium | 3 | Planned |
| 2026-05-21 | Dead unreachable `execute()` return block — duplicate return statements at end of method, second block never reached | Low | 3 | Planned |
| 2026-05-21 | Hardcoded `error="is_directory"` in upload failure path — masks actual error with incorrect category | Low | 3 | Planned |

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
| 2026-03-19 | Remove `ProviderFallbackChain` — silent fallback to mock provider masked real provider errors; replace with direct `init_chat_model` via Deep Agents' native model resolution. `provider_fallback.py` and `registry.py` deleted (~550 lines). Errors now surface immediately with the actual provider error message. | - | 5 | Completed |
| 2026-03-19 | Fix agent cache key collision — `_generate_cache_key` used only `type(model).__name__`, causing `ChatOpenAI(kimi-k2.5)` and `ChatOpenAI(gpt-4o-mini)` to share a compiled graph. Fixed by including `model_name`/`model_id` in the key via `_model_cache_key()`. | - | 4 | Completed |
| 2026-03-19 | Fix provider bootstrap from config.yaml — `llm:` section was parsed but never consumed after ConfigRegistry migration. Added `seed_providers_from_config()` in `bootstrap.py` to seed a `ProviderConfig` entry on startup using `seed_if_absent` semantics (config.yaml provides defaults, API rows always win). | #24 comment | 1/2 | Completed |
| 2026-03-30 | Fix config loading to honor workspace root and resolve env templates in YAML — `load_config()` now receives workspace-aware cwd at bootstrap/API call sites and `load_yaml_file()` resolves `${VAR}` / `${VAR:-default}` placeholders before provider bootstrap and config reads. | `COGNITION_ISSUE.md` | 1/6 | Completed |
| 2026-03-30 | Fix agent config flow gaps — wire `AgentConfig.max_tokens` into model builders, parse nested frontmatter `config:` blocks, expose full agent config through API responses/CRUD, and stop truncating `system_prompt` in agent responses. | `COGNITION_ISSUE.md` | 4/5/6 | Completed |
| 2026-03-30 | Fix runtime streaming error surfacing so graph execution failures emit `ErrorEvent` instead of ending with an empty assistant response. | `COGNITION_ISSUE.md` | 4/6 | In Progress |
| 2026-05-25 | Fix Deep Agents runtime activity not updating durable session state — assistant responses are streamed but not persisted, tool activity does not advance `updated_at`, tool logs show `session_id=None`, and overlapping turns can mark a session done while earlier runtime work continues. | AEP report: session `2488ce4e-13fa-47c4-95fb-d5120d89f097`; hotfix merged as PR #131 | 2/4/6/7 | Completed |
| 2026-05-25 | Fix K8s sandbox filesystem API compatibility and terminal run contradictions — K8s wrapper now uses Deep Agents' current `ls`/`glob`/`grep` result APIs, and runtime errors suppress later `done` events/callback success. | AEP runtime report: `RUNTIME_ERROR` with "new `ls` API" followed by `run.done` | 3/4/6/7 | Completed |
| 2026-05-28 | Align A2A protocol surface with current JSON-RPC method names and task/event wire shapes; add per-agent Agent Card discovery under `/a2a/{agent_name}/.well-known/agent-card.json`; ensure scoped dynamic agent route lookup respects request scope. | A2A interoperability report for Cognition 0.10.2 | 2/6 | Completed |

---

## Architectural Changes

| Date | Description | Layer | Migration Plan | Status |
|------|-------------|-------|----------------|--------|
| 2026-03-19 | **Remove provider fallback chain; use Deep Agents `init_chat_model` natively** — `ProviderFallbackChain`, circuit breakers, and custom LLM factories replaced by a single `_resolve_provider_config()` + `_build_model()` using LangChain's `init_chat_model`. No fallback — if the configured provider fails, the error surfaces immediately. | 5 | Non-breaking: error path changes (errors surface instead of falling back to mock). Mock provider now test-only. `GlobalProviderDefaults.provider` default changed from `"mock"` to `"openai_compatible"`. | Completed |
| 2026-03-21 | **Replace `astream_events()` callback parser with `astream()` v2 format** — `DeepAgentRuntime.astream_events()` previously called `astream_events(version="v2")` and manually matched raw LangGraph callback strings (`on_chat_model_stream`, `on_tool_start`, `on_tool_end`). Replaced with `astream(stream_mode=["messages", "updates", "custom"], subgraphs=True, version="v2")`. Uses `isinstance(msg, AIMessageChunk)` / `isinstance(msg, ToolMessage)` and real `tool_call_id` fields — fixes broken tool call correlation. Subagent execution now visible via `chunk["ns"]`. | 4 | Non-breaking for callers: same `AgentEvent` domain types emitted. `ToolCallEvent.tool_call_id` now real ID instead of CPython `id(data)`. Requires LangGraph >= 1.1 (upgraded as part of this change). | Completed |
| 2026-03-21 | **Remove AST security theater — document real trust model** — Removed `BANNED_IMPORTS`, `BANNED_CALLS`, `BANNED_OS_CALLS`, `SecurityASTVisitor`, `scan_for_security_violations()` from `agent_registry.py` and the `tool_security` setting from `Settings`. AST scanning was a blocklist bypassable via reflection (`getattr(__builtins__, '__import__')('subprocess')`). It also created an inconsistency: file-discovered tools were scanned, API-registered source-in-DB tools were not. The consistent model: trust is established at the API authentication boundary. Real security boundaries (container isolation, `ToolSecurityMiddleware` blocklist, `COGNITION_BLOCKED_TOOLS`) are unchanged. Security trust model documented in `AGENTS.md`. | 3, 1 | `COGNITION_TOOL_SECURITY` env var is no longer read. Deployments using `tool_security=strict` will no longer block tool loading — if hard tool blocking is needed, migrate to `COGNITION_BLOCKED_TOOLS` + Gateway-layer authorization. | Completed |
| 2026-04-08 | **Use per-session sandbox roots for local execution while keeping `CognitionLocalSandboxBackend` as a thin policy wrapper** — session workspaces move under the configured workspace root (`<workspace_root>/.cognition/sandboxes/<session_id>`) so repo clones, branches, rebases, and uncommitted diffs do not leak across sessions. Shared control-plane state (`cognition.db`, ConfigRegistry, `.cognition/skills`) remains under the configured workspace root. | 4, 3 | Non-breaking API shape; runtime behavior changes from shared workspace to session-scoped worktrees. Existing sessions continue to reference their persisted `workspace_path`; new sessions derive sandbox roots automatically. | In Progress |
| 2026-04-09 | **Defer runtime-controlled repo bootstrap for strict git isolation** — current local backend now provides session-scoped sandbox roots, but successful agent runs can still choose a shared clone destination like `/workspace/Cognition-Gateway`. Future work should move repo bootstrap under runtime control or per-session Docker sandboxes so clone destination is enforced structurally instead of via prompt guidance. | 4, 3 | Follow-on change after local sandbox rollout: introduce runtime-managed clone/bootstrap into `<session.workspace_path>/<repo>` or migrate to per-session Docker sandboxes. Keep current prompt guidance as a temporary mitigation until structural enforcement exists. | Planned |
| 2026-04-12 | **Refactor `create_cognition_agent()` into `CognitionAgentParams` + `RuntimeContext`** — replace the 17-arg agent construction path with a structured parameter object, use runtime-context cache keys instead of MD5, and ensure subagent security middleware is injected into nested agent specs. | 4 | Migrate all internal callers to `CognitionAgentParams`, keep a compatibility wrapper only until callers are updated, then remove the keyword-based entry point and validate with boundary tests. | In Progress |
| 2026-05-01 | **Unify file/API skills and tools through ConfigRegistry** — introduce workspace-level `skill_sources` / `tool_sources` bootstrap config, seed file-managed skills/tools into ConfigRegistry at startup, lock file-managed records from API mutation, and make agent `skills` / `tools` attach registry names only. Runtime resolves selected names through registry-backed backends/loaders instead of direct filesystem paths from agent definitions. | 4, 2, 6 | Non-backward-compatible config cleanup: agent `skills` / `tools` now mean selected registry names only; file source directories move to workspace config. Existing path-based agent attachments must migrate to seeded names. | Completed |

---

## Explicit Error Policy

Cognition does not use silent fallback logic. The following invariants are enforced:

1. **Provider resolution fails loudly** — no silent fall-through to mock or default providers
2. **Missing provider configuration** → `LLMProviderConfigError` with actionable message
3. **Registry unavailable** → logged and surfaced to caller, never silently swallowed
4. **Unscoped providers act as globals** — providers with empty scope (`{}`) are visible to all scopes. No separate "GlobalProviderDefaults" object in the LLM resolution path.

### Known Fallback Sites (Backlog — to be removed)

The following fallback patterns exist and are tracked for removal. They produce incorrect silent behavior in production:

| ID | File | Line(s) | Pattern | Impact |
|----|------|---------|---------|--------|
| F-01 | `llm/deep_agent_service.py` | 306 | `except Exception: pass` on tool loading | Agent silently runs without custom tools |
| F-02 | `llm/deep_agent_service.py` | 320 | Agent name not found → falls back to `"default"` | Wrong agent, no warning |
| F-03 | `llm/deep_agent_service.py` | 463 | `model or "gpt-4o"` when session sets provider but not model | Wrong model, no error |
| F-05 | `agent/definition.py` | 363 | `except Exception: continue` on tool file load | Broken tools silently skipped |
| F-06 | `llm/deep_agent_service.py` | 592 | `except RuntimeError: return []` on MCP config | MCP servers silently unavailable |
| F-08 | `agent/cognition_agent.py` | 323 | `reg = None` → hardcoded memory/skills/subagent defaults | Configured values silently lost |
| F-09 | `agent/cognition_agent.py` | 404 | `except (FileNotFoundError, RuntimeError):` on prompt file | Custom prompt silently replaced with default |
| F-10 | `api/routes/tools.py` | 18, 44 | `except RuntimeError: return []` | `GET /tools` returns empty instead of 503 |
| F-11 | `api/routes/models.py` | 92 | `except RuntimeError: return empty` | `GET /models/providers` returns empty instead of 503 |
| F-21 | `agent/runtime.py` | 337+ | `thread_id or "default"` | All threads share one slot → cross-session state bleed |
| F-22 | `agent/runtime.py` | 484 | `except Exception: return None` on state retrieval | DB errors indistinguishable from "no state" |
| F-23 | `rate_limiter.py` | 137 | `except Exception: pass` with no logging | Rate limiter cleanup errors invisible |
| F-24 | `execution/backend.py` | 94 | `except Exception: pass` on Docker container check | Docker daemon errors masked |

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
| `langgraph` + `langchain` | langgraph 1.0.8, langchain 1.2.9 | langgraph 1.1.3, langchain 1.2.13 | `astream()` now supports `version="v2"` unified StreamPart format; required for streaming rewrite (#34) | Completed |
| `deepagents` + `anthropic` + `langchain-anthropic` | deepagents 0.3.12, anthropic 0.79.0, langchain-anthropic 1.3.2 | deepagents 0.4.12, anthropic 0.86.0, langchain-anthropic 1.4.0 | `execute()` in `SandboxBackendProtocol` gained `timeout: int \| None = None` kwarg — updated `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend` to match. Also fixed latent `self._timeout` bug (attribute didn't exist; now uses `self._default_timeout` from parent). | Completed |
| Core runtime and framework refresh | deepagents 0.4.12, fastapi 0.128.6, starlette 0.52.1, langgraph 1.1.3, langchain 1.2.13, langsmith 0.6.9, openai 2.17.0, typer 0.23.0, rich 14.3.2, uvicorn 0.40.0, websockets 15.0.1 | deepagents 0.5.2, fastapi 0.135.3, starlette 1.0.0, langgraph 1.1.6, langchain 1.2.15, langsmith 0.7.30, openai 2.31.0, typer 0.24.1, rich 15.0.0, uvicorn 0.44.0, websockets 16.0 | `deepagents` composite backend now expects routed backends to implement `ls()` in addition to legacy `ls_info()`; test shim updated. Verified with full unit suite after lock refresh. | Completed |
| deepagents → 0.6.2 + LangChain family upgrade | deepagents 0.5.2, langchain-core >=0.3.0,<1.3.0 | deepagents 0.6.2, langchain-core >=1.4.0, langchain >=1.3.0 | deepagents 0.6.2 requires langchain-core>=1.4.0; removes current `<1.3.0` cap. Async subagents, v3 event streaming, ContextHubBackend, interpreter middleware. Blocked by: none → unblocks all v0.10.0 features. | Planned |

### Post-RFC Cleanup Train

**Release batch**: PRs #86-#91 complete the post-RFC cleanup train started after the architecture-deepening RFC landed.

**Merged cleanup PRs**:
- `#86` Unify scoping API across all routes
- `#87` Remove dead `ConfigRegistry` globals
- `#88` Collapse `AgentDefinitionRegistry` into `DefaultConfigStore`
- `#89` Remove `AgentRegistry` runtime
- `#90` Simplify runtime resolution and remove streaming shims
- `#91` Deduplicate shared storage backend logic

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
| **v0.10.0 P0 blocker: Canonical `effective_scope` propagation** | Layer 1/2/3/4/6/7 | Completed | Builder-defined `effective_scope: dict[str, str]` is extracted from trusted ingress and propagated through API, persistence, Deep Agents runtime context, tools, memory, artifacts, MCP, sandboxes, callbacks, events, logs, metrics, and traces; session/run scope is immutable; mismatched-scope runtime access is rejected; config inheritance is separated from exact-scope runtime resource access; tool-safety and external policy hooks receive scope plus action/resource descriptors; scoped isolation tests cover every touched v0.10.0 runtime boundary. Cognition carries builder-authorized scope and enforces returned decisions, but does not own authorization logic. Merged into release branch as Wave 2A gate. | 3–5 days | None |
| **v0.10.0 P0 blocker: Runtime durability + observability foundation** | Layer 2/4/6/7 | Completed | Cognition models long-running work as durable `session_runs` plus append-only `session_events`; Deep Agents/LangGraph checkpoint state is the authoritative conversation source; `/messages` becomes a checkpoint-derived projection; every runtime action advances durable run/session activity and emits builder-usable events with `session_id`, `run_id`, `thread_id`, scope-safe fields, and trace correlation; active runs prevent premature session `done`; builders can poll `/runs` and `/events` instead of scraping logs or inferring progress from `message_count`; OTel/log/callback/SSE signals share the same correlation spine. Full design: `localdocs/v0.10.0-runtime-durability-refactor.md`. | 7–10 days | PR #131 hotfix; canonical scope propagation; Deep Agents runtime/checkpoint primitives |
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
| **K8s sandbox backend** | Layer 3/4 | Completed | `langchain-k8s-sandbox` workspace package; `CognitionKubernetesSandboxBackend`; Helm RBAC + values; SandboxTemplate example; 35 unit tests + 13 e2e tests; live-verified on Talos cluster | 5 days | agent-sandbox controller installed separately |
| **AWS Lambda MicroVM sandbox backend** | Layer 2/3/4/6/7 | Completed | `aws_lambda_microvm` backend key; `SandboxProfile` registry CRUD and file bootstrap; agents select `sandbox_profile` and trusted `sandbox_execution_role_arn`; `executionRoleArn` resolves from agent/profile config only; workspace package `packages/langchain-aws-lambda-microvms` launches Lambda MicroVMs, creates in-memory proxy auth tokens, maps execute/upload/download/terminate to the runtime command server, and exposes token-free runtime metadata; Cognition wrapper maps profile networking, idle policy, image ARN/version, and role fingerprint; sandbox lifecycle snapshots stream and persist MicroVM id, endpoint, profile, image, status, and role fingerprint without auth tokens; profile/role selectors participate in cache keys; capabilities API advertises support; unit tests and live AWS smoke passed. | 3-5 days | Prebuilt Lambda MicroVM image ARNs; boto3/botocore with Lambda MicroVM service model |
| Multi-user session isolation | Layer 2 | Pending | Users can only see/access their own sessions | 2 days | P0: Session lifecycle |
| Graceful abort/cancellation | Layer 4 | Pending | Abort button immediately stops execution; no zombie processes | 1 day | P0: Session lifecycle |
| Proper error propagation | Layer 5/6 | Completed | All 14 fallback sites resolved: provider errors surface with `LLMProviderConfigError`, registry-missing returns 503, silent `except Exception: pass` replaced with logged warnings throughout | 2 days | None |
| Rate limiting | Layer 6 | Pending | Per-user and global rate limits enforced | 1 day | None |
| **v0.10.0: Durable run lifecycle + unified observability spine** | Layer 2/4/6/7 | Folded into P0 | Superseded by the P0 runtime durability + observability foundation after the AEP persistence incident. The v0.10.0 implementation must land the core session/run/event/checkpoint projection model first; replay/fork and richer retry policy surfaces can remain follow-on work if they are not required for the P0 contract. | 7–10 days | P0 runtime durability foundation |
| **v0.10.0: Persistent artifacts + structured handoffs** | Layer 2/4/6/7 | Planned | Typed artifact routes (`/scratch/`, `/artifacts/`, `/contracts/`, `/evals/`, `/memories/`, `/skills/`, `/policies/`); artifact API (CRUD, diff, version history, link to run/checkpoint); built-in schemas (feature_list.json, progress.json, contract.json, eval_report.json); structured handoff events; scope-aware artifact visibility. | 3–5 days | Track A |
| **v0.10.0: Trusted runtime context + tool arg repair + permissions/HITL** | Layer 1/3/4/6/7 | Completed | Merged into release branch as Wave 2B via PR #121 and stabilized by PR #123. Adds trusted runtime context via `CognitionContext` and Deep Agents `ToolRuntime`; spoof-resistant reserved arg injection; schema-aware tool argument validation with repair feedback; v0.10-native HITL policy objects; Deep Agents filesystem permissions across agent/default/subagent config; `tool_safety` SSE events for context injection, validation failure, and blocked tools; `hitl_decision` SSE events for approve/edit/reject; structured logs, OTel spans, and Prometheus metrics for tool safety and HITL decisions; redaction tests for raw scope values, edited values, and rejection message bodies. Validated on dev K8s image `0.10.0-w2b` with 46 passed / 2 skipped e2e tests, then stabilized against local Docker Compose with 58 passed / 1 skipped focused e2e tests. No secret-reference, secret-resolution, or secret-injection surface ships in v0.10.0; builders own credential handling in their own tools/infrastructure. | 5–7 days | P1: runner-lifecycle |

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
| **Deep Agents HITL end-to-end (`interrupt` SSE + `POST /sessions/{id}/resume`)** | Layers 4/6 | In Progress | Native `interrupt_on` pauses are surfaced as SSE events; sessions can be resumed via LangGraph `Command(resume=...)`; interrupted sessions expose `waiting_for_approval` status | 1–2 days | P2: astream v2 rewrite |
| **Emit planning and `step_complete` SSE events from native todo state** | Layer 4/6 | In Progress | `write_todos` state deltas from Deep Agents are translated to `planning` and `step_complete` SSE events without custom planner logic | 1 day | P2: astream v2 rewrite |
| **Expose and operationalize Deep Agents context controls (`tool_token_limit_before_evict`, summarization tool middleware)** | Layer 4 | In Progress | Agent config can set `tool_token_limit_before_evict`; declarative middleware supports `summarization_tool` via Deep Agents factory; effective config is externally observable; deterministic verification exists for context offloading/summarization behavior | 1–2 days | deepagents >= 0.4.8 |
| **Expose structured output via Deep Agents `response_format`** | Layers 4/5 | In Progress | AgentDefinition and SessionConfig accept `response_format`; dotted path resolves to Pydantic model class; forwarded directly to `create_deep_agent(response_format=...)` | 1 day | None |
| **Enforce ProviderConfig `max_retries` and `timeout` in `init_chat_model()`** | Layer 5 | In Progress | Provider registry values are passed directly to LangChain model init; no Cognition-level retry wrapper introduced | 0.5 day | None |
| **Expose ConfigRegistry global defaults over REST** | Layer 6 | In Progress | `GET/PATCH /config/defaults/provider` and `GET/PATCH /config/defaults/agent` expose existing global default models with partial update semantics and API validation | 0.5 day | P2: Dynamic ConfigRegistry |
| **Dynamic ConfigRegistry (hot-reloadable agent config)** | Layers 1–6 | Completed | Agent/model/provider config changeable via API without restart; scoped per user/project; DB-backed with file bootstrap | 5–7 days | P0: Persistence, P1: Scoping |
| ~~LLM provider fallback~~ | Layer 5 | Cancelled | Superseded — explicit errors replace silent fallback. See Architectural Changes. | - | - |
| **Remove `GlobalProviderDefaults` from LLM resolution path** | Layer 5 | Completed | `_resolve_provider_config()` no longer calls `get_global_provider_defaults()` as step 3; unscoped `ProviderConfig` entries (scope=`{}`) serve as globals; `LLMProviderConfigError` raised when no providers configured | 0.5 days | None |
| **`POST /models/providers/{id}/test` endpoint** | Layer 6 | Completed | Calls `_build_model()` + lightweight `model.ainvoke()` to verify credentials; returns success/error with actual provider message; usable from GUI settings | 0.5 days | None |
| **Eliminate known fallback sites (F-01 through F-24)** | Layers 4–6 | Completed | All `except Exception: pass` patterns replaced with logged warnings; `GET /tools` and `GET /models/providers` return 503 when registry unavailable; `model or "gpt-4o"` removed; thread_id/state retrieval warnings added; see Explicit Error Policy section | 2 days | None |
| **Model catalog integration (models.dev)** | Layer 5/6 | Completed | `ModelCatalog` service fetches models.dev catalog; `GET /models` returns enriched model metadata (context window, tool call support, pricing, modalities); `GET /models/providers/{id}/models` lists catalog models for a provider config; `SessionConfig.provider_id` enables per-session provider selection by config ID; tool call validation warning on model resolution; `DiscoveryEngine` deprecated | 2 days | P2: ConfigRegistry |
| Connection pooling | Layer 2 | Pending | Database connections pooled; no connection leaks | 1 day | None |
| Health check endpoint | Layer 6 | Pending | `/health` returns 200 when all deps ready | 0.5 days | None |
| Metrics and telemetry | Layer 7 | Pending | Prometheus metrics; OpenTelemetry traces | 2 days | None |
| **v0.10.0: Experimental async subagents as remote workstreams** | Layer 2/4/6/7 | Completed | Merged into release branch via PR #126. Adds `async_subagents` to agent definitions/global defaults, validates header-free remote Agent Protocol specs, and wires them into Deep Agents async task tools (`start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`). This is an experimental remote Agent Protocol delegation surface, not Cognition's simple in-process supervisor/subagent pattern. Production follow-ups remain out of scope until concrete remote deployment requirements are defined: external lifecycle API/SSE wrappers, parent/worker trace correlation, and durable task registry semantics. | 3–5 days | P1: runner-lifecycle |
| **v0.10.0: Context compression + offloading + token controls** | Layer 2/4/6/7 | Completed | Merged into release branch via PR #124. First slice adds declarative `ContextPolicy`, Deep Agents `SummarizationToolMiddleware` alignment, Deep Agents summarization-state translation into `context` SSE events, Prometheus/OTel context signals, scoped redacted context debug metadata, and Deep Agents-aligned approximate token counting. Future follow-up: deterministic verification for large-tool-output offload and richer summarization/offload artifact IDs as upstream/runtime signals become available. | 2–3 days | P1: artifacts-handoffs, P1: runner-lifecycle |
| **v0.10.0: Model-aware harness profiles** | Layer 4/5/6/7 | Deferred | Profile definitions in ConfigRegistry scoped by org/project/user/agent; profile selection by provider+model; fields: subagent config, evaluator cadence, context policy, tool visibility, middleware stack, model kwargs, cost/latency budgets; effective-profile introspection on each run; deterministic scope-aware selection. Deferred to v0.11.0. | 2–3 days | P1: runner-lifecycle |
| **v0.11.0: Eval orchestration adapters + optional agent harness** | Layer 2/4/6/7 | Deferred to v0.11.0 | See `localdocs/v0.10.0-eval-orchestration-shaping.md` for full design. Definition-driven `EvalDefinition` records; `EvalJob`/`EvalEvidence`/`EvalReport` models; `agent` and `webhook` adapters first, then LangSmith/Braintrust/Promptfoo/custom; lifecycle policy (blocking/non-blocking/advisory); planner/generator/evaluator as optional harness preset. Explicit non-goals: datasets, dashboards, leaderboards, hardcoded rubrics. | 8–12 days | P1: runner-lifecycle, P1: artifacts-handoffs |
| **v0.11.0: Scoped memory + background consolidation** | Layer 2/4/6/7 | Deferred to v0.11.0 | Memory scopes (user/assistant/project/organization policy); read-only/write policies per route; background consolidation jobs with cadence/source threads/output artifact; episodic search over prior threads scoped by user/org/project; memory update events and audit logs; namespaces derived from Cognition scope; org memory read-only to agents by default. | 3–4 days | P1: artifacts-handoffs |
| **v0.11.0: Code interpreter middleware (QuickJS)** | Layer 3/4/6/7 | Deferred to v0.11.0 | Declarative `code_interpreter` middleware config; allowlisted callable tools and execution limits; interpreter state visibility (eval count, logs, errors, result size, linked tool calls); policy controls per agent/subagent; disabled by default; no host filesystem/network/shell access except through explicit bridges. | 2–3 days | Track A |
| **v0.10.0: Sandbox lifecycle + resource scheduling + K8s hardening** | Layer 2/3/4/6/7 | Planned | Resource-aware sandbox scheduling (capacity check, queued state, pending reason, estimated retry, capacity endpoint); sandbox lifecycle events (provision/queue/pending/ready/verified/env-verified/warm-pool/command/file-transfer/ttl/cleanup/teardown); cleanup status for abort/delete (pod/claim/worktree/volume); SandboxTemplate runtime verification (env vars, workspace root, required CLIs, writable dirs); sandbox resource metrics; built-in Git/GitHub execution evidence without Cognition-managed credentials. Includes K8s security hardening (shell injection fix, thread-safe lazy init, dead code removal, accurate error codes). | 4–6 days | Track A, P1: runner-lifecycle |
| **v0.10.0: MCP alignment (auth/capability/progress)** | Layer 3/4/5/6/7 | Planned | MCP server capability discovery; tool interceptors and policy hooks; MCP auth/readiness status as builder-owned server metadata without Cognition credential refs; MCP progress notifications as Cognition events; connection lifecycle metrics and logs; resource and prompt surfacing in agent definitions. | 2–3 days | Track A |
| **v0.10.0: Capability registry + compatibility surface** | Layer 1/4/5/6/7 | Planned | `GET /capabilities` with version info (Cognition, Deep Agents, LangGraph, LangChain), stream protocol versions, enabled middleware types, sandbox backends, async subagent support, interpreter support, MCP support, checkpoint/time-travel, evaluation support; startup compatibility checks; schema discovery for agent definition fields. | 1–2 days | Track A |

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

## P2-2: Model Catalog Integration (models.dev)

**Layer**: Layer 5 (LLM Provider) / Layer 6 (API)

**Problem**: Cognition had no way for users to browse available models for their configured providers. The `DiscoveryEngine` used stale hardcoded model lists (e.g., `o1-preview` which is outdated; missing Claude 3.5+, GPT-4o variants). `ModelInfo.context_window` and `ModelInfo.capabilities` were always `None`/`[]`. There was no per-session provider selection by config ID. Tool call validation was absent — users could configure non-tool-capable models with no warning.

**Solution**: Replace `DiscoveryEngine` with a `ModelCatalog` service backed by models.dev (configurable URL). The catalog is **enrichment only** — it never blocks model execution. If unreachable, endpoints degrade gracefully.

**Key Design Decisions**:
- **Only models.dev**: No multi-catalog abstraction. URL configurable for mirrors/self-hosted.
- **Static provider mapping**: `PROVIDER_TYPE_TO_CATALOG_SLUGS` maps Cognition provider types to models.dev slugs. `openai_compatible` returns empty (backing provider depends on base_url).
- **In-memory cache with TTL**: Default 1-hour cache. Stale data served on refresh failure.
- **Enrichment, not validation**: Any model ID accepted — catalog data only enriches API responses and powers warnings.
- **`SessionConfig.provider_id`**: References a `ProviderConfig.id` from ConfigRegistry. Takes priority over `provider`/`model` overrides.

**New API Surface**:
- `GET /models` — enhanced with query parameters (`provider`, `tool_call`, `q`) and enriched `ModelInfo` fields (context_window, capabilities, pricing, modalities)
- `GET /models/providers/{id}/models` — list catalog models available for a specific provider config

**Acceptance Criteria**:
- [x] `ModelCatalog` fetches and caches models.dev catalog with configurable TTL
- [x] `GET /models` returns enriched model metadata from catalog
- [x] `GET /models/providers/{id}/models` returns models filtered by provider config type
- [x] `SessionConfig.provider_id` enables per-session provider selection by config ID
- [x] Provider_id resolution takes priority over provider/model overrides in `_resolve_provider_config`
- [x] Tool call validation warning logged when resolving a model without tool call support
- [x] `DiscoveryEngine` deprecated (kept for backward compatibility)
- [x] `GET /config` uses `ModelCatalog` instead of `DiscoveryEngine`
- [x] `COGNITION_MODEL_CATALOG_URL` and `COGNITION_MODEL_CATALOG_TTL_SECONDS` settings added
- [x] 36 new model catalog tests + 5 new provider_id resolution tests (all pass)
- [x] 533 total unit tests pass, ruff clean, mypy clean

**Effort**: 2 days

**Dependencies**: P2: Dynamic ConfigRegistry

**Files**:
- New: `server/app/llm/model_catalog.py` — ModelCatalog service with caching, provider mapping, search
- Modified: `server/app/settings.py` — Added `model_catalog_url`, `model_catalog_ttl_seconds`
- Modified: `server/app/models.py` — Added `provider_id` to `SessionConfig`, updated serialization
- Modified: `server/app/llm/deep_agent_service.py` — `provider_id` resolution, tool call warning
- Modified: `server/app/api/routes/models.py` — Rewritten: catalog-backed endpoints, new `/providers/{id}/models`
- Modified: `server/app/api/models.py` — Enriched `ModelInfo` with new fields
- Modified: `server/app/api/routes/config.py` — Switched from `DiscoveryEngine` to `ModelCatalog`
- Modified: `server/app/llm/discovery.py` — Deprecated
- Modified: `server/app/llm/__init__.py` — Added `ModelCatalog` exports
- New: `tests/unit/test_model_catalog.py` — 36 tests
- Modified: `tests/unit/test_provider_resolution.py` — 5 new `provider_id` tests

---

## P3: Full Vision

| Task | Layer | Status | Acceptance Criteria | Effort | Dependencies |
|------|-------|--------|---------------------|--------|--------------|
| Multi-agent orchestration | Layer 4 | Pending | Primary agent can delegate to subagents seamlessly | 3 days | P1: Configurable agent params |
| Skills progressive disclosure | Layer 4 | Pending | Skills loaded on-demand based on context | 2 days | None |
| GraphQL API | Layer 6 | Pending | Alternative to REST for complex queries | 3 days | P1: Production-Ready |
| Evaluation framework | Layer 7 | Pending | Automated benchmark runs on agent performance | 5 days | P1: Production-Ready |
| Builder boundary documentation | Layer 6 | Completed | A builder-facing guide clearly defines Cognition Core vs app-layer responsibilities, gives decision rules, and links from the main docs index and root README | 0.25 days | None |
| `app = Cognition(agent); app.run()` | All | Pending | Single-line instantiation provides all features | 5 days | All above |
| **v0.10.0: A2A server adapter** | Layer 4/6/7 | Completed | Cognition exposes itself as an A2A Server via `a2a-sdk`. Per-agent Agent Cards and JSON-RPC endpoints at `/a2a/{agent_name}`. Builders opt agents into A2A via `a2a_exposed` field. Catch-all dynamic dispatch — agents created at runtime are immediately available without restart. Scope-aware card discovery — callers see only agents visible to their `X-Cognition-Scope-*` headers. `contextId` maps to `session_id`; `taskId` maps to `run_id`. HITL pauses surface as `INPUT_REQUIRED`. Push notifications, gRPC, outbound A2A client, and file/media parts deferred. | 2–3 days | P1: runner-lifecycle |

### Explicitly Deferred

- `#45` LangGraph Store memory tools — deferred until memory UX/design is specified (tool vs middleware, namespace, retrieval strategy).
- `#53` ACP transport layer — deferred until core Deep Agents runtime capabilities are surfaced over existing REST/SSE transport.
- `harness-eval` — deferred to v0.11.0 as headline feature. Full design in `localdocs/v0.10.0-eval-orchestration-shaping.md`.
- `scoped-memory` — deferred to v0.11.0.
- `code-interpreter` — deferred to v0.11.0. Depends on deepagents 0.6.x upgrade.
- `model-profiles` — deferred to v0.11.0.

---

## v0.10.0 Release Tracking

This section tracks the v0.10.0 long-running agents release. See `localdocs/v0.10.0-plan.md` for the full branching strategy, wave schedule, and K8s cluster context.

### Release Branch

- **Branch**: `release/v0.10.0` (cut from `main` after Track A + Track 0 land)
- **Strategy**: Feature branches PR into release branch; tested per-wave with pre-release images; one final merge to `main`

### Wave Schedule

| Wave | Branches | Pre-Release Tag | Gate |
|------|----------|-----------------|------|
| 1 | `runner-lifecycle`, `artifacts-handoffs`, `sandbox-hardening` | `0.10.0-w1` | Complete -- 27/28 API tests + 13/13 scenario tests pass on dev K8s |
| 2A | `scope-alignment` | `0.10.0-w2a` | Complete -- P0 scope propagation merged into release branch; Wave 2B unblocked |
| 2B | `tool-safety`, `context-controls`, `async-subagents`, `model-profiles` | `0.10.0-w2b` | Complete -- `tool-safety` merged (PR #121, stabilized PR #123); `context-controls` merged (PR #124); `async-subagents` merged (PR #126); `model-profiles` deferred to v0.11.0 |
| 2C | `runtime-durability-refactor` | `0.10.0-w2c` | Complete -- merged as PR #132; durable `session_runs`/`session_events`, run/event read APIs, scoped access tests, SSE/run/event correlation, OTel metrics/spans, checkpoint projection safety, K8s filesystem API compatibility fix, terminal error handling fix, and long-running e2e acceptance scenarios are included in `release/v0.10.0`; local non-Bedrock unit pass: 717 passed / 4 skipped / 15 deselected; Docker Compose strict runtime/tool durability e2e passes with DeepSeek (`COGNITION_STRICT_TOOL_E2E=1`) |
| 3 | `mcp-alignment`, `capability-registry`, `protocol-adapters` | `0.10.0-w3` | Complete -- `mcp-alignment` merged (PR #128); `capability-registry` merged (PR #129); `protocol-adapters` A2A adapter merged (PR #130) |
| *Deferred* | `harness-eval`, `scoped-memory`, `code-interpreter`, `model-profiles` | N/A | Deferred to v0.11.0. Full eval orchestration design in `localdocs/v0.10.0-eval-orchestration-shaping.md`. |

### Release Gates And Phase 0 Blockers

| Task | Target | Status |
|------|--------|--------|
| `feat/runtime-upgrade` → `main` | deepagents 0.6.3 + langchain-core >=1.4.0 | Merged (PR #115) |
| `feat/k8s-security` → `main` | Shell injection, thread safety, dead code, error accuracy | Merged (PR #116) |
| Canonical scope propagation | P0 feature entry + dedicated Wave 2A implementation gate | Complete |
| Runtime durability + observability foundation | P0 feature entry + dedicated Wave 2C implementation gate | Complete -- merged as PR #132 with core implementation, scoped API contract tests, SSE durable correlation fix, K8s filesystem API compatibility fix, terminal error handling fix, and Docker Compose strict runtime/tool durability scenario pass locally |
| ROADMAP.md populated | All feature entries | Complete |
| `server/version.py` → `"0.10.0"` | Version bump on release branch | Complete |
| `localdocs/v0.10.0-plan.md` | Agent reference document | Complete |
| CHANGELOG.md updated | Waves 1-3 documented | Complete |
| Pre-release image gate | `0.10.0-rc1` validated | Complete -- app/sandbox amd64, app/sandbox arm64, and both multi-arch manifests passed in pre-release workflow |

### Done Criteria

See `localdocs/v0.10.0-long-running-agents-priorities.md` §v0.10.0 Definition Of Done for the full release-level acceptance criteria.

---

## Roadmap Governance

Per AGENTS.md requirements:

1. **ROADMAP.md Structure**: Organized by work category (Security, Bug, Performance, Dependency, Features).
2. **Work Categories**: All six categories tracked here with appropriate detail levels.
3. **Precedence**: Security fixes override all. Bug fixes > Features. Performance/Dependency can proceed alongside Features. Feature tiers: P0 > P1 > P2 > P3.
4. **When to Update**:
   - Features/Architectural: Before starting work
   - Security/Bug/Performance/Dependency: As part of PR

**Last Updated**: 2026-05-25 (v0.10.0 final release validation complete — `0.10.0-rc1` app and sandbox image gate passed)
