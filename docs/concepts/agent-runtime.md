# Agent Runtime

The agent runtime is the Layer 4 component responsible for translating a declarative `AgentDefinition` into a running agent, driving its event stream, and managing its lifecycle.

---

## AgentRuntime Protocol

Defined in `server/app/agent/runtime.py`, `AgentRuntime` is a Python `Protocol` that establishes the contract between the API layer and any agent implementation:

```python
class AgentRuntime(Protocol):
    async def astream_events(
        self,
        message: str,
        thread_id: str,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def ainvoke(
        self,
        message: str,
        thread_id: str,
    ) -> AgentEvent: ...

    async def get_state(self, thread_id: str) -> dict[str, Any]: ...

    async def abort(self, thread_id: str) -> None: ...

    def get_checkpointer(self) -> BaseCheckpointSaver: ...
```

`DeepAgentRuntime` is the only production implementation. It wraps Deep Agents, transforms its event stream into the canonical `AgentEvent` types, and handles abort via a thread-ID cancellation set. The protocol boundary exists so the underlying framework can be swapped without touching any Layer 5 or Layer 6 code.

---

## Canonical Event Types

All events emitted by the runtime are typed dataclasses defined in `server/app/agent/runtime.py`. The API layer (`server/app/api/sse.py:EventBuilder`) serializes them to SSE wire format.

```python
@dataclass
class TokenEvent:
    content: str

@dataclass
class ToolCallEvent:
    name: str
    args: dict[str, Any]
    id: str

@dataclass
class ToolResultEvent:
    tool_call_id: str
    output: str
    exit_code: int

@dataclass
class PlanningEvent:
    todos: list[str]

@dataclass
class StepCompleteEvent:
    step_number: int
    total_steps: int
    description: str

@dataclass
class DelegationEvent:
    target_agent: str
    task: str

@dataclass
class StatusEvent:
    status: str    # "thinking" | "idle"

@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    provider: str
    model: str

@dataclass
class DoneEvent:
    assistant_data: dict[str, Any]

@dataclass
class ErrorEvent:
    message: str
    code: str
```

Every consumer of the runtime (streaming endpoint, tests, evaluators) deals only with these types — not with raw LangGraph or Deep Agents events.

---

## AgentDefinition

`AgentDefinition` (`server/app/agent/definition.py`) is the declarative configuration for a single agent. It is a Pydantic v2 model:

```python
class AgentDefinition(BaseModel):
    name: str
    system_prompt: str | PromptConfig | None = None
    tools: list[str] = []           # dotted import paths, e.g. "myapp.tools.search"
    skills: list[str] = []          # paths to SKILL.md files or directories
    memory: list[str] = []          # paths to instruction files (AGENTS.md)
    subagents: list[SubagentDefinition] = []
    middleware: list[str | dict] = []
    config: AgentConfig = AgentConfig()
    mode: Literal["primary", "subagent", "all"] = "primary"
    description: str | None = None
    hidden: bool = False
    native: bool = False            # True for built-in agents
```

### Agent Modes

| Mode | Meaning |
|---|---|
| `primary` | Can be selected as the main agent for a session via `agent_name` |
| `subagent` | Can only be invoked by another agent via the `task` tool |
| `all` | Can function as either |

### System Prompt Sources

The `system_prompt` field accepts three forms via `PromptConfig`:

| Source | Config | Description |
|---|---|---|
| Inline | `system_prompt: "You are..."` | Direct text in the definition |
| File | `{file: "deploy-agent"}` | Loaded from `.cognition/prompts/deploy-agent.md` |
| MLflow | `{mlflow: "my-prompt@v3"}` | Loaded from an MLflow prompt registry at startup |

### AgentConfig

Per-agent LLM configuration that overrides the server default:

```python
class AgentConfig(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int = 300
```

### Tool Path Resolution

Tools are referenced as dotted Python import paths. At runtime, `AgentDefinition._resolve_tools()` imports each path and returns the callable. The `trusted_tool_namespaces` security setting constrains which namespaces are allowed.

---

## Loading Agent Definitions

### From YAML

```yaml
# .cognition/agents/security-auditor.yaml
name: security-auditor
mode: subagent
description: Audits code for security vulnerabilities
system_prompt: |
  You are a security expert. Audit code for vulnerabilities.
  Report findings with severity ratings.
tools:
  - "myapp.tools.security.run_semgrep"
  - "myapp.tools.security.check_dependencies"
config:
  model: gpt-4o
  temperature: 0.1
```

### From Markdown (with YAML frontmatter)

The file name becomes the agent name; the Markdown body becomes the `system_prompt`.

```markdown
---
mode: subagent
description: Read-only research assistant
tools:
  - "myapp.tools.web_search"
---

You are a research assistant. Gather information from the web and summarize findings.
Do not execute any code or modify files.
```

### Programmatically

```python
from server.app.agent.definition import AgentDefinition, AgentConfig

definition = AgentDefinition(
    name="my-agent",
    system_prompt="You are a helpful assistant.",
    tools=["myapp.tools.analyze"],
    config=AgentConfig(model="gpt-4o-mini", temperature=0.3),
    mode="primary",
)
```

---

## Agent Registry

`AgentDefinitionRegistry` (`server/app/agent/agent_definition_registry.py`) is the server-level catalog of all available agents.

### Built-in Agents

| Name | Mode | Description |
|---|---|---|
| `default` | `primary` | Full-access coding agent; all built-in tools enabled |
| `readonly` | `primary` | Analysis-only; write and execute tools disabled |

### User-Defined Agents

On startup, the registry scans `.cognition/agents/` for `*.md` and `*.yaml` files and loads each as an `AgentDefinition`. The file watcher (`server/app/file_watcher.py`) calls `registry.reload()` when files change, enabling hot-reload without a server restart.

### Registry API

```python
registry = get_agent_definition_registry()

# List all non-hidden agents
agents = registry.get_all()

# Get a specific agent
agent = registry.get("security-auditor")

# Only agents that can own sessions
primaries = registry.primaries()

# Only agents invocable by other agents
subs = registry.subagents()

# Check if a name is valid for session creation
registry.is_valid_primary("readonly")  # True
registry.is_valid_primary("my-subagent")  # False
```

### REST Interface

```bash
# List all available agents
curl http://localhost:8000/agents

# Get a specific agent
curl http://localhost:8000/agents/readonly
```

Response fields include `name`, `description`, `mode`, `hidden`, `native`, `model`, `temperature`, `tools`, `skills`, and a truncated `system_prompt` (max 500 characters).

---

## Agent Factory

`create_cognition_agent()` (`server/app/agent/cognition_agent.py`) is the async factory that builds a Deep Agent from an `AgentDefinition`. It is called by `DeepAgentRuntime` when creating a new session's runtime.

The factory:

1. Selects the sandbox backend from settings (`local` or `docker`)
2. Loads built-in tools: `BrowserTool`, `SearchTool`, `InspectPackageTool`
3. Loads MCP tools from configured remote servers
4. Resolves tools from the `AgentDefinition` (dotted import paths + ConfigRegistry API-registered tools)
5. Attaches the middleware stack:
   - `ToolSecurityMiddleware` — blocks tools on the `COGNITION_BLOCKED_TOOLS` deny-list
   - `CognitionObservabilityMiddleware` — tracks LLM and tool Prometheus metrics
   - `CognitionStreamingMiddleware` — emits `thinking`/`idle` status events
6. Loads upstream middleware specified in the definition (see [Extending Agents](../guides/extending-agents.md))
7. Injects subagents as Deep Agents `SubAgent` dicts
8. Passes `store=` (LangGraph `BaseStore`) and `context_schema=CognitionContext` for cross-thread memory

### Agent Caching

Agents are cached by an MD5 hash of their definition. If two sessions share the same `AgentDefinition`, they reuse the same agent object. The cache is invalidated by `invalidate_agent_cache(name)` and cleared entirely by `clear_agent_cache()`. The file watcher triggers cache invalidation on `.cognition/` changes.

---

## Multi-Agent Delegation

When a `primary` agent needs to delegate a task, it invokes the `task` tool with a target agent name and a task description. The runtime:

1. Emits a `DelegationEvent` to the stream (visible to the client)
2. Creates a `DeepAgentRuntime` for the target subagent
3. Runs the subagent to completion
4. Returns the result to the primary agent's context

Subagents have their own tool sets and system prompts but run within the same session's thread, preserving shared state. Clients can observe delegation via the `delegation` SSE event and the subsequent events from the subagent's execution.

---

## CognitionContext and Cross-Thread Memory

`CognitionContext` (`server/app/agent/cognition_agent.py`) is a typed invocation context injected into every agent run. It is built from `session.scopes` and forwarded to `astream()` and `ainvoke()` so that nodes and middleware can access it via `runtime.context`.

```python
@dataclass
class CognitionContext:
    user_id: str = "anonymous"
    org_id: str | None = None
    project_id: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
```

This context serves two purposes:

1. **Store namespace scoping** — `runtime.store` (a LangGraph `BaseStore`) is available inside agent nodes and middleware. `CognitionContext.user_id` is the natural key for building per-user memory namespaces, ensuring user A cannot read user B's stored memories.

2. **Middleware access** — any custom middleware can read `runtime.context` to branch on user, org, or project dimensions without coupling to the HTTP layer.

```python
# Inside a custom tool or middleware
async def save_to_memory(content: str, config: RunnableConfig) -> str:
    store = config["store"]
    context = config["configurable"].get("cognition_context")
    namespace = (context.user_id, "memories")
    await store.aput(namespace, key, {"content": content})
    return "Saved."
```

> **Note:** Built-in memory tools (`save_memory`, `search_memories`) are planned but not yet shipped. See [GitHub issue #45](https://github.com/CognicellAI/Cognition/issues/45) for the design discussion. In the meantime, the Store infrastructure is fully wired and custom tools can use it today.
