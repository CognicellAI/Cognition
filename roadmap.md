# Cognition Roadmap

> **Current Status:** Phase 10 Complete ✅ (Agent-in-Container Architecture)
> **Last Updated:** 2026-02-11
> **Tests:** All new architecture tests passing ✅

---

## Quick Overview

| Phase | Status | Description |
|-------|--------|-------------|
| 1-7 | ✅ Complete | Foundation, Server, Agent, Tools, Container, Streaming, TUI |
| 8 | ✅ Complete | **Persistent Multi-Session Support** (Major Feature) |
| 9 | ✅ Complete | **OpenTelemetry Observability** (Major Feature) |
| 10 | ✅ Complete | **Agent-in-Container Architecture** (Major Rewrite) |
| 11 | ⏳ Planned | Post-MVP Features |

---

## Phase 1: Foundation ✓

### Project Setup
- [x] Initialize Python project with `pyproject.toml`
- [x] Set up project structure (server/, client/, shared/, workspaces/)
- [x] Configure development environment (uv, ruff, mypy, pytest)
- [x] Create `.env.example` with required API keys
- [x] Add pre-commit hooks configuration
- [x] Create Docker base image `opencode-agent:py` (Python 3.11+, git, ripgrep, patch)

### Shared Protocol
- [x] Define canonical schema in `shared/protocol/schema.json`
- [x] Implement message models (client → server)
- [x] Implement event models (server → client)
- [x] Create JSON serialization helpers

## Phase 2: Server Core ✓

### FastAPI WebSocket Server
- [x] Create FastAPI app with WebSocket endpoint
- [x] Implement connection management and routing
- [x] Add health check endpoints

### Session Manager
- [x] Implement session lifecycle (create, get, destroy)
- [x] Create workspace directory management
- [x] Handle per-session container registry
- [x] Store session state (network mode, history)

### Settings & Configuration
- [x] Environment variable management
- [x] Model configuration
- [x] Path and Docker image settings

## Phase 3: Agent Runtime ✓

### Deep Agents Integration
- [x] Set up LangGraph Deep Agents runtime (using `deepagents` library)
- [x] Create agent factory with tool binding
- [x] Implement system prompts and coding loop guidance
- [x] Add MVP allowlist policies for tools
- [x] Support for OpenAI, Anthropic, AWS Bedrock, and OpenAI-compatible APIs

### OpenAI-Compatible API Support
- [x] Support for any OpenAI API compatible endpoint:
  - LiteLLM Proxy
  - vLLM
  - Ollama
  - LocalAI
  - Any custom OpenAI-compatible proxy
- [x] Configurable via `OPENAI_API_BASE` environment variable
- [x] Optional API key (some local instances don't require auth)

### AWS Bedrock Support
- [x] AWS Bedrock client factory with multiple auth methods:
  - IAM Role authentication (USE_BEDROCK_IAM_ROLE)
  - Explicit AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
  - AWS Profile (AWS_PROFILE)
  - Default credential chain fallback
- [x] LLM provider factory supporting all three providers
- [x] Bedrock model configuration (Claude, Titan, Llama)

### Planning & Action Selection
- [x] Multi-step planning logic (built-in `write_todos` tool)
- [x] Tool request handling with validation
- [x] Result processing and loop continuation
- [x] Subagent spawning support (built-in `task` tool)

## Phase 4: Tool System ✓

### Tool Interfaces
- [x] `filesystem.py`: read_file, read_files, apply_patch
- [x] `search.py`: rg search wrappers
- [x] `git.py`: git_status, git_diff
- [x] `tests.py`: run_tests (pytest) wrapper
- [x] `safety.py`: argv-only validation, path confinement

### Tool Mediator
- [x] Convert tool calls to internal Action objects
- [x] Enforce MVP constraints (argv-only, workspace paths)
- [x] Emit streaming events
- [x] Validate and authorize tool requests

## Phase 5: Container Execution ✓

### Container Management
- [x] Start/stop session containers
- [x] Configure network modes (OFF default, ON optional)
- [x] Mount workspace volumes
- [x] Set resource limits (CPU, memory, output size)

### Action Execution
- [x] `repo_ops.py`: Implement rg, read, apply_patch, git, pytest
- [x] Stream stdout/stderr chunks
- [x] Enforce timeouts
- [x] Handle output caps

### Safety & Limits
- [x] Path traversal protection
- [x] Command validation (no shell strings)
- [x] Resource monitoring
- [x] cwd confinement enforcement

## Phase 6: Streaming & Events ✓

### Event Streaming
- [x] Unified event emitter
- [x] Stream tool events (start, output, end)
- [x] Assistant message streaming
- [x] Error handling and propagation

### Event Types
- [x] session_started
- [x] assistant_message
- [x] tool_start, tool_output, tool_end
- [x] diff_applied
- [x] tests_finished
- [x] error, done

## Phase 7: Client TUI ✓

### Textual App
- [x] Create main Textual application
- [x] Implement WebSocket client
- [x] Add UI state machine (session, network banner)

### Widgets
- [x] `log.py`: Streaming timeline widget
- [x] `prompt.py`: Input prompt widget
- [x] `diff.py`: Diff viewer for apply_patch results
- [x] `approval.py`: Approval modal (optional)

### Event Rendering
- [x] Render assistant messages
- [x] Display tool execution progress
- [x] Show diff previews
- [x] Update network mode banner

## Phase 8: Persistent Multi-Session Support ✓ (NEW - Major Feature)

### Project Management System
- [x] Project data models (Project, ProjectConfig, SessionRecord)
- [x] Project lifecycle management (create, load, save, delete)
- [x] User-friendly naming with prefixes (e.g., `my-api-a7b3c2d1`)
- [x] Project metadata persistence (JSON)
- [x] Statistics tracking (sessions, messages, duration)

### Hybrid Memory Strategy
- [x] Hot memories in RAM (`/memories/hot/` - StoreBackend)
- [x] Persistent memories on disk (`/memories/persistent/` - FilesystemBackend)
- [x] Automatic memory snapshots every 5 minutes
- [x] Memory restoration on session resume
- [x] Background task for periodic snapshots

### Auto-Cleanup System
- [x] Configurable cleanup period (default 30 days)
- [x] Warning system (3 days before deletion)
- [x] Pin support to prevent deletion
- [x] Background task for cleanup checks (daily)
- [x] Soft limit on total projects (1000 default)

### Container Lifecycle
- [x] Fresh container on each reconnect (1-2s startup)
- [x] Workspace preservation across sessions
- [x] Resource efficiency (stop when idle)
- [x] Zero-copy workspace mounting

### API Endpoints
- [x] `GET /api/projects` - List projects with filters
- [x] `POST /api/projects` - Create new project
- [x] `GET /api/projects/{id}` - Get project details
- [x] `POST /api/projects/{id}/sessions` - Create session
- [x] `GET /api/sessions/resumable` - List resumable sessions
- [x] `POST /api/projects/{id}/extend` - Extend/pin project
- [x] `DELETE /api/projects/{id}` - Delete project

### TUI Enhancements
- [x] `/create <name>` - Create persistent project
- [x] `/resume <project-id>` - Resume existing project
- [x] `/list` - Show resumable projects
- [x] Updated command parsing and help text

### Configuration
- [x] All settings in `.env.example`
- [x] `MAX_PROJECTS`, `PROJECT_CLEANUP_*` settings
- [x] `MEMORY_SNAPSHOT_*` settings
- [x] `CONTAINER_STOP_ON_DISCONNECT`, `CONTAINER_RECREATE_ON_RECONNECT`
- [x] `AGENT_BACKEND_ROUTES` for hybrid memory

### Documentation
- [x] USER_GUIDE.md - Projects section added
- [x] QUICK_START.md - Project workflow added
- [x] README.md - Features updated
- [x] BACKEND_ROUTES_CONFIG.md - Hybrid strategy documented
- [x] PROJECT_PERSISTENCE_COMPLETE.md - Implementation summary

---

## Phase 9: Integration & Polish ✓ (COMPLETE)

### End-to-End Flow
- [x] Connect TUI to server
- [x] Test full coding loop (search → read → patch → test)
- [x] Verify streaming output in TUI
- [x] Test network ON/OFF modes
- [x] Test project resume flow

### Observability
- [x] **OpenTelemetry Integration** (NEW - Major Feature)
  - Multi-exporter fan-out (LangSmith + custom OTLP)
  - HTTP request auto-instrumentation via FastAPIInstrumentor
  - WebSocket lifecycle tracing with custom middleware
  - Trace context correlation in all structlog messages
  - Support for Jaeger, Grafana Tempo, Datadog, and any OTLP backend
- [x] **LangSmith Integration** (NEW)
  - Deep Agents native tracing support
  - Automatic agent turn and tool execution spans
  - Configurable project routing
  - Support for self-hosted LangSmith
- [x] Structured logging with OpenTelemetry correlation
- [x] Error tracking and reporting with span status codes
- [x] Session metrics (via health endpoint)
  - [x] Project usage analytics (via API)

---

## Phase 10: Agent-in-Container Architecture ✅ (MAJOR REWRITE)

### Overview
Complete architectural rewrite moving from "Server runs agent, executes tools via Docker" to "Agent runs in container, server just orchestrates". This positions the system for Kubernetes support, better security, and industry-standard architecture.

### New Architecture
```
Client → Server (WebSocket) → AgentBridge → Agent Container (WebSocket)
                                              ↓
                                    LangGraph + LocalSandbox
                                              ↓
                                    Direct subprocess execution
```

### What Changed
- **Before**: Server process runs LangGraph, calls tools which execute in Docker via `docker exec`
- **After**: Agent runs inside container with LocalSandbox, all execution is local subprocess
- **Benefits**: True isolation, K8s-ready, no Docker-in-Docker, simpler security model

### Server Changes
- [x] **Removed server-side agent execution**
  - Deleted: `server/app/agent/deep_agent.py`
  - Deleted: `server/app/executor/repo_ops.py`
  - Deleted: `server/app/executor/actions.py`
  - Deleted: `server/app/tools/filesystem.py`
  - Deleted: `server/app/tools/search.py`
  - Deleted: `server/app/tools/tests.py`
  - Deleted: `server/app/tools/git.py`
  
- [x] **New server-side bridge**
  - `server/app/executor/bridge.py` - WebSocket client to agent container
  - `AgentBridge` class for bi-directional communication
  - `AgentBridgePool` for managing multiple bridges
  - Event streaming with real-time callbacks

- [x] **Updated session management**
  - `SessionManager.initialize_agent_bridge()` - Connects to agent container
  - `SessionManager.send_to_agent()` - Forwards user messages
  - Dynamic port allocation for agent containers
  - Graceful shutdown handling

- [x] **Updated container lifecycle**
  - Agent containers expose port 9000 (WebSocket)
  - Port mapping handled automatically
  - Agent Docker image with entrypoint
  - Network configuration for LLM API access

### Agent Container (NEW)
- [x] **Agent runtime package** (`agent/`)
  - `agent/entrypoint.py` - Container entry point
  - `agent/runtime.py` - WebSocket server + LangGraph runner
  - `agent/sandbox.py` - LocalSandboxBackend (BaseSandbox subclass)
  - `agent/local_tools.py` - GitTools, TestTools for local execution
  - `agent/requirements.txt` - Agent dependencies

- [x] **Agent capabilities**
  - Runs LangGraph with Deep Agents
  - Local filesystem via BaseSandbox (read, write, edit, ls, glob, grep)
  - Local subprocess execution (pytest, git)
  - WebSocket server for server communication
  - Real-time event streaming (assistant messages, tool events)
  - Cancellation support
  - Graceful shutdown

### Protocol (NEW)
- [x] **Internal protocol** (`shared/protocol/internal.py`)
  - Server → Agent: `AgentStartMessage`, `UserMessage`, `CancelMessage`, `ShutdownMessage`
  - Agent → Server: `AssistantMessageEvent`, `ToolStartEvent`, `ToolOutputEvent`, `ToolEndEvent`, `ErrorEvent`, `DoneEvent`
  - JSON serialization with dataclasses
  - Full test coverage

### Docker
- [x] **Agent Dockerfile** (`docker/Dockerfile.agent`)
  - Based on `python:3.11-slim-bookworm`
  - Includes git, ripgrep, patch
  - Installs agent dependencies
  - Exposes port 9000
  - Health check for WebSocket

### Tests (NEW)
- [x] `tests/test_internal_protocol.py` - Protocol serialization/parsing
- [x] `tests/test_agent_bridge.py` - Bridge connection and messaging
- [x] `tests/test_local_sandbox.py` - Sandbox backend functionality
- [x] `tests/test_local_tools.py` - Git and test tools

### Configuration
- [x] New settings:
  - `AGENT_DOCKER_IMAGE` - Agent runtime image
  - Agent port exposed via Docker port mapping
  - Environment variable injection for LLM credentials

### Migration Path
1. Build agent image: `docker build -f docker/Dockerfile.agent -t cognition-agent:latest .`
2. Set `AGENT_DOCKER_IMAGE=cognition-agent:latest` in .env
3. Server automatically uses new architecture
4. Old `docker_image` setting still works for backward compatibility

### Benefits
- **Security**: Agent runs in isolated container, not on server
- **Scalability**: Ready for Kubernetes (each agent = pod)
- **Performance**: No Docker exec overhead for file operations
- **Simplicity**: Server is thin orchestrator, not execution engine
- **Industry Standard**: Matches Devin, Copilot Workspace architecture

### Test Status
- Unit tests: 25/25 passing for new components ✅
- Integration tests: Framework ready
- Old tests: Updated for async session manager methods

---

## Phase 11: Post-MVP (Future)

### Additional Actions
- [ ] `pip install` action (requires network ON)
- [ ] Lint/typecheck actions (`ruff`, `mypy`)
- [ ] Git commit/push support
- [ ] Database migration actions

### Enhancements
- [ ] Approval prompts for risky commands
- [ ] Egress allowlist networking
- [ ] Repo indexing and context summarization
- [ ] LangGraph middleware plugins
- [ ] Skills system
- [ ] Project templates (pre-configured project types)
- [ ] Project sharing between users
- [ ] Real-time collaboration (multiple users per project)

### Security & Enterprise
- [ ] Multi-tenant support
- [ ] Authentication and RBAC
- [ ] SOC2/ISO controls
- [ ] Distributed execution
- [ ] Audit logging for all actions

---

## Current Status Summary

**Last Updated:** 2026-02-11

### What's Working ✅
- Full WebSocket server with FastAPI
- Multi-LLM support (OpenAI, Anthropic, AWS Bedrock, OpenAI-compatible)
- **Agent-in-Container Architecture** (NEW! - Major Rewrite)
  - Agent runs inside Docker container with full LangGraph runtime
  - Server is thin orchestrator, forwards messages via WebSocket
  - LocalSandboxBackend for filesystem operations
  - Real-time streaming events (assistant messages, tool execution)
  - Dynamic port allocation, graceful shutdown
  - Ready for Kubernetes deployment
- Deep Agents runtime with LangGraph
- Complete tool system (git, tests - local execution in container)
- Container-per-session execution
- Textual TUI client
- **Persistent multi-session projects**
- Hybrid memory strategy (RAM + disk)
- Auto-cleanup with warnings
- **OpenTelemetry observability**
  - Multi-exporter fan-out (LangSmith + custom OTLP)
  - HTTP/WebSocket request tracing
  - Trace-logs correlation
  - Deep Agents native tracing
- 80+ tests passing

### In Progress 🚧
- Phase 10 planning

### Test Status
```
✅ Unit tests: 55/55 passing
✅ Integration tests: Framework complete
✅ E2E tests: Framework complete
✅ Observability tests: Complete
```

### Code Stats
- **Total lines added:** ~1,200+ (persistent session feature)
- **Test coverage:** Core functionality covered
- **Documentation:** Complete for all features

### Next Immediate Actions
1. Plan Phase 10 features (pip install, lint, git commit)
2. Review user feedback and feature requests
3. Consider modern Web GUI or TUI enhancements
