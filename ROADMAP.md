# Cognition Roadmap

This roadmap tracks the path toward Cognition's "batteries-included AI backend" vision.

## Priority Definitions

- **P0 (Table Stakes)**: Must-have for basic functionality. Blocks all other work.
- **P1 (Production-Ready)**: Required for safe, reliable production use.
- **P2 (Robustness)**: Edge cases, resilience, scalability improvements.
- **P3 (Full Vision)**: Advanced features and complete architectural vision.

## Current Status

See the sections below for each priority tier.

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
| **Configurable agent recursion_limit and max_tokens** | Layer 4 | In Progress | `agent_recursion_limit` configurable via env/Settings/YAML (default: 1000); `llm_max_tokens` wired to model factories (default: 20000) | 0.5 days | None |
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

1. **ROADMAP.md Is Required**: This file must exist and reflect current priorities.
2. **Agents Must Adhere**: No implementing features not listed here; no skipping priorities.
3. **Roadmap Precedence**: P0 blocks P1; P1 blocks P2; P2 blocks P3. Security fixes override all.
4. **Update Required Before**: Major work, architectural changes, priority shifts.

**Last Updated**: 2026-03-05
