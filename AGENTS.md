# AGENTS.md — Cognition Coding Agent

Guidelines for agentic coding assistants working on this codebase.

## Project Overview

Cognition is an OpenCode-style coding agent with:
- **Server**: FastAPI WebSocket API with Deep Agents runtime (`server/`)
- **Client**: CLI/TUI for interactive sessions (`client/`)
- **Execution**: Container-per-session with optional network isolation
- **Scope**: Python repos only, pytest-based testing

## Build / Test / Lint Commands

This project uses `uv` for dependency management and task execution.

```bash
# Install dependencies
uv sync

# Run server (development)
# Runs the FastAPI server with hot reload
uv run uvicorn server.app.main:app --reload --port 8000

# Run client (development)
# Starts the CLI/TUI client
uv run python -m client.cli.main

# Run all tests
uv run pytest

# Run single test file
uv run pytest tests/unit/test_settings.py -v

# Run single test case
uv run pytest tests/unit/test_settings.py::TestSettingsDefaults::test_default_server_settings -v

# Type checking (Strict)
uv run mypy .

# Linting & Formatting (Ruff)
uv run ruff check .
uv run ruff format .
```

## Code Style Guidelines

### Python Standards
- **Python 3.11+** required.
- **Type Hints**: strict `mypy` compliance. Use `from __future__ import annotations`.
- **Docstrings**: Google style for all public functions/classes.
- **Line Length**: Follow `ruff` config (default 88/100).
- **Imports**: Grouped as stdlib, third-party, local. Use absolute imports.

### Naming Conventions
- `snake_case`: functions, variables, modules.
- `PascalCase`: classes, types.
- `UPPER_CASE`: constants.
- `_prefix`: private methods/attributes.
- `async` functions: often prefixed with verbs like `get_`, `fetch_`, `handle_`.

### Async Patterns
- **Async/Await**: Use for all I/O operations (DB, Network, File).
- **Concurrency**: Use `asyncio.gather()` for parallel independent tasks.
- **Subprocesses**: Use `asyncio.create_subprocess_exec`.

### Error Handling
- **Exceptions**: Use custom hierarchy in `server/app/exceptions.py`.
- **Pattern**: Catch external errors -> Raise domain-specific `CognitionError`.
- **Logging**: Log errors before raising if context is needed, otherwise let middleware handle it.

### Data Models
- **Pydantic V2**: Use for all data structures and validation.
- **Settings**: Use `pydantic-settings` (e.g., `server/app/settings.py`).
- **Secrets**: Use `SecretStr` for sensitive data.

## Project Structure

```
cognition/
├── server/
│   └── app/
│       ├── agent/       # Deep Agents runtime & tools
│       ├── api/         # FastAPI routes
│       ├── llm/         # LLM service integration
│       └── persistence/ # Database/Storage
├── client/
│   └── cli/             # CLI/TUI entry points
├── tests/
│   ├── e2e/             # End-to-end full workflow tests
│   └── unit/            # Isolated unit tests
├── pyproject.toml       # Project dependencies & tool config
└── uv.lock              # Dependency lock file
```

## Key Workflows

### Extending Cognition
Cognition is designed to be highly pluggable using native `deepagents` extension points. Deep Agents is the higher-level abstraction that Cognition is built on — it wraps LangGraph and provides its own primitives (tools, middleware, skills, subagents, memory, sandbox backends). We should prefer Deep Agents' API surface over reaching down to raw LangGraph.

#### Custom Tools
1. Define your tool as a plain Python callable or LangChain `BaseTool`.
2. Register it in `.cognition/config.yaml` under `agent.tools` or pass it to `create_cognition_agent(tools=[...])`.

#### Agent Middleware
1. Implement `deepagents.middleware.AgentMiddleware` for lifecycle hooks (observability, status streaming, etc.).
2. Add to `create_cognition_agent(middleware=[...])`.

**Available upstream middleware** (declarative in `.cognition/agent.yaml`):
- `tool_retry` - Exponential backoff retry on tool failure
- `tool_call_limit` - Per-tool and global call limits
- `human_in_the_loop` - Approve/edit/reject tool calls before execution
- `pii` - Detect and redact PII (email, credit card, IP, etc.)

#### Skills & Memory
1. Add `SKILL.md` files to `.cognition/skills/` for progressive disclosure capabilities.
2. Update `AGENTS.md` in the workspace to provide the agent with project-specific conventions.

#### Subagents
1. Define specialized subagents in `.cognition/config.yaml` to handle complex domain-specific tasks in isolated contexts.

### Testing & Scenarios
- **Unit Tests**: Fast, mocked dependencies. No containers.
- **E2E Tests**: Use `tests/e2e/`. May require running server.
- **Mocking**: Use `unittest.mock` or `pytest-mock` for external services (LLMs).
- **Scenarios**: Scenarios are business logic use case against the APIs. They should be tested in E2E tests with realistic inputs against the docker-compose environment.
  - If keys are not available for external services, let the user know to set them in `.env`.

## Security & Safety
- **Path Traversal**: Validate all file paths against workspace root.
- **Command Execution**: No shell=True. Use argument lists.
- **Secrets**: Never commit keys. Use `.env` and `Settings` class.

### Tool Security Trust Model

Cognition does **not** perform AST scanning or Python-level restrictions on tool source
code. This is a deliberate design decision, not an oversight.

**Why AST scanning was removed:**
AST scanning is a blocklist approach that catches accidental mistakes but not intentional
abuse — it is bypassable via reflection (`getattr(__builtins__, '__import__')('subprocess')`).
It also creates an inconsistency: file-discovered tools were scanned, API-registered
source-in-DB tools were not. The consistent model is: trust is established at the API
authentication boundary.

**The real security boundaries are:**

| Boundary | Mechanism |
|----------|-----------|
| API authentication | Gateway/proxy (OAuth, API keys, mTLS) — Cognition assumes authenticated callers |
| Multi-tenant tool isolation | `ToolSecurityMiddleware` — `COGNITION_BLOCKED_TOOLS` blocklists specific tool names per deployment |
| Process isolation | Docker sandbox backend — container per session, separate network namespace |
| Network isolation | `network_mode="none"` on Docker sandbox backend |
| Filesystem isolation | `CognitionLocalSandboxBackend` protected paths; `virtual_mode=True` |
| Memory isolation | LangGraph Store namespaces scoped per user via `CognitionContext` |

**What this means for builders:**
- `POST /tools` executes arbitrary Python with full privileges inside the sandbox.
  Restrict this endpoint to authorized administrators at the Gateway layer.
- Tools registered via the API are equivalent in trust to tools placed in `.cognition/tools/`.
- If you need strict tool sandboxing (e.g., multi-tenant SaaS where untrusted users can
  register tools), use the Docker sandbox backend with `network_mode="none"` and resource
  limits. Do not rely on Python-level restrictions.

**`COGNITION_BLOCKED_TOOLS` (per-name blocklist) is still supported** — this is real
multi-tenant security enforced by `ToolSecurityMiddleware` at the middleware layer.
It prevents specific tool names from being called regardless of who registered them.


# Hard Requirements


## Mission

Cognition is a **batteries-included AI backend**.

An agent definition (tools, prompt, skills, middleware) must be sufficient to generate:

* API
* Streaming
* Persistence
* Sandboxing
* Observability
* Multi-user scoping
* Evaluation

## Recommended Integration Principles

When evaluating ideas from other agent products or codebases, only integrate features that reinforce Cognition's backend mission.

1. **Backend-first, not CLI-first**
   - Features should strengthen API, streaming, persistence, sandboxing, observability, multi-user scoping, or evaluation.
   - Terminal-only UX features are not core unless they expose reusable backend primitives.

2. **Definition-driven over hardcoded modes**
   - Prefer agent definition, middleware, skill, and config-registry fields over bespoke runtime switches.
   - New behavior should be externally configurable and inspectable.

3. **Scope-aware by default**
   - New persistence, memory, scheduling, and orchestration features must respect user, org, and project boundaries.
   - Never introduce features that can silently cross tenant boundaries.

4. **Observable and debuggable**
   - Long-running jobs, delegation flows, memory processes, and approval paths must emit explicit logs, metrics, and streaming events where appropriate.
   - Hidden background behavior without attribution is architectural drift.

5. **Deep Agents-native where possible**
   - Prefer Deep Agents primitives and extension points over parallel custom orchestration systems.
   - Avoid re-implementing capabilities already supported by the runtime unless there is a concrete gap.

6. **Smallest correct integration**
   - Import the minimal useful idea, not the full product surface area around it.
   - Re-express features in Cognition's architecture rather than copying foreign abstractions wholesale.

7. **Respect the 7-layer architecture**
   - New integrations must preserve layer direction and avoid mixing client, runtime, persistence, and observability concerns.
   - If a feature requires architectural change, it must be treated as such in ROADMAP.md.


---

# 0. Work Categories & Roadmap Governance

Cognition is past MVP. The project now operates on a **category-based** model that supports continual improvement: security patches, bug fixes, performance work, dependency updates, and features all flow through ROADMAP.md with appropriate rigor for each type.

## Work Categories

All work falls into one of six categories. Each has different ROADMAP.md requirements and Definition of Done criteria.

| Category | ROADMAP Entry | Priority | Can Proceed Without Full Planning? |
|----------|--------------|----------|-----------------------------------|
| **Security Fix** | Line item (severity + layer) | **Immediate** — overrides all | Yes, always |
| **Bug Fix** | Line item (description + layer) | **High** — next available cycle | Yes, always |
| **Performance Improvement** | Standard entry (description + benchmarks) | **Medium-High** | Yes, unless requires architectural change |
| **Dependency / Library Update** | Line item (package + versions) | **Medium** — batch when practical | Yes, unless breaking changes require planning |
| **Feature / Enhancement** | Full entry (criteria + layer + effort + deps) | Per roadmap tier (P0-P3) | **No** — must have roadmap entry first |
| **Architectural Change** | Full entry + migration plan | Per roadmap tier (P0-P3) | **No** — must have roadmap entry first |

### Category Details

**Security Fix**
- Address vulnerabilities, exposure of secrets, or attack vectors
- Severity: Critical, High, Medium, Low
- ROADMAP.md entry: brief description + severity + affected layer
- Can be merged immediately after review, bypassing feature roadmap

**Bug Fix**
- Correct incorrect behavior, crashes, or regressions
- ROADMAP.md entry: brief description + link to issue/reproduction + affected layer
- Should include test that reproduces the bug and verifies the fix

**Performance Improvement**
- Optimize latency, throughput, memory usage, or resource consumption
- Must include benchmark or measurement (before/after)
- ROADMAP.md entry: description + target metric + affected layer + effort estimate
- Should not degrade code readability without strong justification

**Dependency / Library Update**
- Upgrade external packages, frameworks, or tools
- ROADMAP.md entry: package name + old version → new version + breaking changes (if any)
- Breaking changes that require code modifications bump this to "Feature" category
- Lock file (`uv.lock`) must be updated

**Feature / Enhancement**
- New capabilities, user-facing improvements, or API additions
- Must have full ROADMAP.md entry before work begins:
  - Concrete task description
  - Layer assignment (1–7)
  - Acceptance criteria
  - Estimated effort
  - Dependencies

**Architectural Change**
- Refactoring that changes layer boundaries, data flow, or major abstractions
- Must have full ROADMAP.md entry plus migration plan if breaking
- Follows feature roadmap tier ordering (P0-P3)

---

## ROADMAP.md Structure

ROADMAP.md must exist at the repository root. It is organized by work category:

```markdown
# Cognition Roadmap

## Security Fixes
| Date | Description | Severity | Layer | Status |
|------|-------------|----------|-------|--------|
| ...

## Bug Fixes
| Date | Description | Issue | Layer | Status |
|------|-------------|-------|-------|--------|
| ...

## Performance Improvements
| Description | Target Metric | Before | After | Layer | Status |
|-------------|---------------|--------|-------|-------|--------|
| ...

## Dependency Updates
| Package | From | To | Breaking Changes | Status |
|---------|------|-----|------------------|--------|
| ...

## Features (P0-P3 Tiers)
### P0: Table Stakes (Blocking)
...

### P1: Production-Ready
...

### P2: Robustness
...

### P3: Full Vision
...
```

### When to Update ROADMAP.md

- **Before starting work**: Features and architectural changes
- **As part of PR**: Security fixes, bug fixes, performance improvements, dependency updates
- **Before merging architectural changes**: Ensure migration plan is documented

---

## Precedence Rules

1. **Security fixes override all other work**
2. **Bug fixes take priority over new features**
3. **Architectural corrections take priority over feature work**
4. **Performance improvements and dependency updates can proceed alongside feature work**
5. **Features and architectural changes follow roadmap tier ordering (P0 > P1 > P2 > P3)**

---

# 1. Architectural Alignment Rules

All work MUST respect the 7-layer architecture:

```
Layer 7: Observability
Layer 6: API & Streaming
Layer 5: LLM Provider
Layer 4: Agent Runtime
Layer 3: Execution
Layer 2: Persistence
Layer 1: Foundation
```

Dependency direction is strictly top-down.

No lateral or upward imports.

---

# 2. Definition of Done (Tiered by Category)

A PR is not complete until its category-specific DoD is met:

### Security Fixes
- [ ] Tests verifying the vulnerability is addressed
- [ ] No regressions (existing tests pass)
- [ ] Respects layer boundaries

### Bug Fixes
- [ ] Tests reproducing and verifying the fix
- [ ] No regressions (existing tests pass)
- [ ] Respects layer boundaries

### Performance Improvements
- [ ] Tests verifying no regressions
- [ ] Benchmark or measurement demonstrating improvement (before/after)
- [ ] Respects layer boundaries
- [ ] Does not degrade readability without justification

### Dependency / Library Updates
- [ ] Full test suite passes
- [ ] No regressions
- [ ] Breaking changes documented and handled (if any)
- [ ] Lock file (`uv.lock`) updated

### Features / Enhancements
- [ ] Listed in ROADMAP.md with acceptance criteria
- [ ] Clear layer assignment
- [ ] Has observability hooks (where applicable)
- [ ] Respects persistence boundaries
- [ ] Respects multi-user isolation boundaries
- [ ] Has tests
- [ ] Does not introduce architectural drift

### Architectural Changes
- [ ] Meets full Feature DoD, plus:
- [ ] Migration path documented (if breaking)
- [ ] Layer dependency direction preserved

---

# 3. Enforcement Protocol

Before merging any PR, agents must verify:

- [ ] Work category is identified (security / bug / performance / dependency / feature / architectural)
- [ ] ROADMAP.md is updated appropriately for the category
- [ ] Layer boundaries are respected
- [ ] Tests pass for the category
- [ ] Category-specific Definition of Done is met

If any answer is "no," the PR must be revised.

---

# 4. Architectural North Star

Cognition must eventually allow:

```python
agent = AgentDefinition(
    tools=[...],
    skills=[...],
    system_prompt="...",
)

app = Cognition(agent)
app.run()
```

And automatically provide:

* REST API
* SSE streaming
* Persistence
* Sandbox isolation
* Observability
* Multi-user scoping
* Evaluation pipeline

The roadmap exists to force convergence toward that state.
