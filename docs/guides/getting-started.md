# Getting Started

Cognition is a backend service your application talks to over HTTP. You deploy it alongside your app, point it at an LLM provider, and send messages from your code — Cognition handles the agent loop, tool execution, streaming, and persistence.

This guide gets you from zero to a working integration in under 15 minutes.

---

## Contents

- [1. Deploy Cognition](#1-deploy-cognition)
- [2. Connect your first LLM provider](#2-connect-your-first-llm-provider)
- [3. Send a message from your application](#3-send-a-message-from-your-application)
- [4. Parse the streaming response](#4-parse-the-streaming-response)
- [5. Persist context across turns](#5-persist-context-across-turns)
- [6. Give the agent your tools](#6-give-the-agent-your-tools)
- [7. Scope sessions to your users](#7-scope-sessions-to-your-users)
- [What's next](#whats-next)

---

## 1. Deploy Cognition

The fastest path is Docker Compose. Clone the repo, set your API key, and start:

```bash
git clone https://github.com/CognicellAI/Cognition.git
cd Cognition

# Create your env file — this is where secrets live, never config.yaml
cp .env.example .env
```

Edit `.env` — add your API key for whichever provider you want to start with:

```bash
# .env
COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-v1-...   # OpenRouter
# or
OPENAI_API_KEY=sk-...                               # OpenAI
# or
ANTHROPIC_API_KEY=sk-ant-...                        # Anthropic
```

Then configure the provider in `.cognition/config.yaml` (create the file if it doesn't exist):

```yaml
# .cognition/config.yaml
llm:
  provider: openai_compatible
  model: google/gemini-2.5-flash-preview
  base_url: https://openrouter.ai/api/v1
```

Start the server:

```bash
docker compose up -d cognition
```

Verify it's up:

```bash
curl -s http://localhost:8000/health
# {"status": "healthy", "version": "0.6.0", ...}
```

> **Don't want Docker?** Install with `uv sync --extra openai` and run `uv run uvicorn server.app.main:app --reload --port 8000`. See [Deployment](./deployment.md) for production options (PostgreSQL, Kubernetes, multi-instance).

---

## 2. Connect your first LLM provider

The `llm:` section in `.cognition/config.yaml` seeds the ConfigRegistry on first startup. You can also manage providers live via the API — changes take effect immediately, no restart required.

### Supported providers

| Provider | `provider` value | Key env var | Notes |
|---|---|---|---|
| OpenAI | `openai` | `OPENAI_API_KEY` | |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | |
| AWS Bedrock | `bedrock` | ambient AWS credentials | Instance role, ECS task role, IRSA — no static keys needed |
| OpenAI-compatible | `openai_compatible` | `COGNITION_OPENAI_COMPATIBLE_API_KEY` | OpenRouter, vLLM, Ollama, LM Studio, any OpenAI-spec endpoint |
| Google Vertex AI | `google_vertexai` | application default credentials | |
| Google AI Studio | `google_genai` | `GOOGLE_API_KEY` | |

### Switching providers at runtime

You do not need to restart Cognition to change providers. Use the API:

```bash
# Add a new provider
curl -X POST http://localhost:8000/models/providers \
  -H "Content-Type: application/json" \
  -d '{
    "id": "claude",
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "api_key_env": "ANTHROPIC_API_KEY",
    "enabled": true,
    "priority": 1
  }'

# Test it before using it
curl -X POST http://localhost:8000/models/providers/claude/test
# {"success": true, "provider": "anthropic", "model": "claude-sonnet-4-6", ...}

# List all configured providers
curl http://localhost:8000/models/providers
```

The `priority` field controls which provider is used when a session doesn't specify one — lower number means higher priority.

---

## 3. Send a message from your application

Cognition uses a session model: create a session once, send messages into it, and the agent maintains conversational context. Sessions map naturally to a user conversation or task.

### Create a session

```python
import httpx

COGNITION_URL = "http://localhost:8000"

async def create_session(title: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{COGNITION_URL}/sessions",
            json={"title": title},
        )
        resp.raise_for_status()
        return resp.json()["id"]
```

```typescript
const COGNITION_URL = "http://localhost:8000";

async function createSession(title: string): Promise<string> {
  const resp = await fetch(`${COGNITION_URL}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await resp.json();
  return data.id;
}
```

### Send a message and stream the response

Messages stream back as [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events). Every chunk has an `event:` type and a JSON `data:` payload.

```python
async def send_message(session_id: str, content: str):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{COGNITION_URL}/sessions/{session_id}/messages",
            json={"content": content},
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    import json
                    event_data = json.loads(line[6:])
                    yield event_data
```

```typescript
async function* sendMessage(sessionId: string, content: string) {
  const resp = await fetch(
    `${COGNITION_URL}/sessions/${sessionId}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ content }),
    }
  );

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        yield JSON.parse(line.slice(6));
      }
    }
  }
}
```

---

## 4. Parse the streaming response

Every event has a type. Here are the ones you'll use in a UI:

| Event type | `data` fields | What to do |
|---|---|---|
| `token` | `content: string` | Append to the response text buffer |
| `tool_call` | `name`, `args`, `id` | Show a "running tool…" indicator |
| `tool_result` | `tool_call_id`, `output`, `exit_code` | Hide the indicator; optionally show the output |
| `status` | `status: "thinking" \| "idle"` | Drive a loading spinner |
| `usage` | `input_tokens`, `output_tokens`, `estimated_cost`, `model` | Update a cost tracker |
| `done` | `assistant_data` | Final message stored; stream is complete |
| `error` | `message`, `code` | Show an error; stream is terminated |

`tool_call.id` always matches `tool_result.tool_call_id` — use this to correlate which spinner to replace with which result.

### Minimal example: print the response

```python
async def ask(session_id: str, question: str) -> str:
    response_text = ""
    async for event in send_message(session_id, question):
        match event.get("event"):
            case "token":
                response_text += event["content"]
                print(event["content"], end="", flush=True)
            case "done":
                print()  # newline after streaming
            case "error":
                raise RuntimeError(event["message"])
    return response_text
```

### React component example

```typescript
function AgentChat({ sessionId }: { sessionId: string }) {
  const [response, setResponse] = useState("");
  const [thinking, setThinking] = useState(false);
  const [activeTools, setActiveTools] = useState<Record<string, string>>({});

  async function submit(content: string) {
    setResponse("");
    setThinking(true);

    for await (const event of sendMessage(sessionId, content)) {
      switch (event.event) {
        case "token":
          setResponse((r) => r + event.content);
          break;
        case "tool_call":
          setActiveTools((t) => ({ ...t, [event.id]: event.name }));
          break;
        case "tool_result":
          setActiveTools((t) => {
            const { [event.tool_call_id]: _, ...rest } = t;
            return rest;
          });
          break;
        case "status":
          setThinking(event.status === "thinking");
          break;
        case "done":
          setThinking(false);
          break;
      }
    }
  }

  return (
    <div>
      {thinking && <Spinner />}
      {Object.entries(activeTools).map(([id, name]) => (
        <ToolBadge key={id} name={name} />
      ))}
      <div>{response}</div>
    </div>
  );
}
```

---

## 5. Persist context across turns

Sessions automatically persist conversation history — just reuse the same session ID:

```python
session_id = await create_session("Refactor auth module")

# Turn 1
await ask(session_id, "Read src/auth.py and summarise the current structure.")

# Turn 2 — agent remembers the previous exchange
await ask(session_id, "Now refactor it to use JWT tokens.")

# Turn 3
await ask(session_id, "Write tests for the new implementation.")
```

Sessions survive server restarts (SQLite or Postgres, depending on your backend). Retrieve a session's message history at any time:

```bash
curl "http://localhost:8000/sessions/$SESSION/messages?limit=50"
```

To cancel a running agent operation (e.g., if the user clicks Stop):

```bash
curl -X POST "http://localhost:8000/sessions/$SESSION/abort"
```

---

## 6. Give the agent your tools

Tools are the primary way to connect Cognition to your systems — databases, APIs, queues, filesystems. The agent calls them during its ReAct loop just like any other tool.

### Option A — File drop (for filesystem access)

Drop Python files into `.cognition/tools/`. Every public function becomes a tool. Hot-reloaded on change.

```python
# .cognition/tools/jira_tools.py
import httpx

def get_ticket(ticket_id: str) -> str:
    """Fetch a Jira ticket by ID and return its title and status."""
    resp = httpx.get(
        f"https://jira.example.com/rest/api/2/issue/{ticket_id}",
        headers={"Authorization": "Bearer ..."},
    )
    return resp.json()["fields"]["summary"]

def add_comment(ticket_id: str, comment: str) -> str:
    """Add a comment to a Jira ticket."""
    httpx.post(
        f"https://jira.example.com/rest/api/2/issue/{ticket_id}/comment",
        json={"body": comment},
    )
    return f"Comment added to {ticket_id}"
```

### Option B — API registration (for containerised builder apps)

When your builder app and Cognition run in separate containers, use `POST /tools` with inline Python source:

```python
import httpx

tool_code = """
from langchain_core.tools import tool
import httpx as _httpx

@tool
def get_ticket(ticket_id: str) -> str:
    \"\"\"Fetch a Jira ticket by ID and return its title and status.\"\"\"
    resp = _httpx.get(
        f"https://jira.example.com/rest/api/2/issue/{ticket_id}",
        headers={"Authorization": "Bearer ..."},
    )
    return resp.json()["fields"]["summary"]
"""

async with httpx.AsyncClient() as client:
    await client.post(
        "http://localhost:8000/tools",
        json={
            "name": "jira-tools",
            "code": tool_code,
            "description": "Jira ticket operations",
        },
    )
```

The tool is stored in the database and loaded on every agent invocation — no restart needed. Use `DELETE /tools/{name}` to remove it.

> **Trust model:** Tool code runs with full Python privileges inside the sandbox. Restrict `POST /tools` to administrators at your Gateway layer.

### Inspect what tools are active

```bash
curl http://localhost:8000/tools
```

```json
{
  "tools": [
    {"name": "ls",         "source_type": "file",     "enabled": true},
    {"name": "grep",       "source_type": "file",     "enabled": true},
    {"name": "jira-tools", "source_type": "api_code", "enabled": true}
  ],
  "count": 3
}
```

---

## 7. Scope sessions to your users

In a multi-user application, you must isolate sessions by user. Cognition enforces this through scope headers.

Enable scoping in your deployment config:

```bash
COGNITION_SCOPING_ENABLED=true
COGNITION_SCOPE_KEYS=["user"]
```

Then pass the user's identity with every request:

```python
USER_ID = "user_abc123"

headers = {
    "Content-Type": "application/json",
    "X-Cognition-Scope-User": USER_ID,
}

# Create session — scoped to this user
session = await client.post(
    f"{COGNITION_URL}/sessions",
    json={"title": "My task"},
    headers=headers,
)

# List sessions — only this user's sessions are returned
sessions = await client.get(
    f"{COGNITION_URL}/sessions",
    headers=headers,
)
```

A missing scope header returns `403 Forbidden` — fail-closed, never fail-open. Users cannot access each other's sessions, message history, or stored memories.

For per-project isolation, add a project dimension:

```bash
COGNITION_SCOPE_KEYS=["user", "project"]
```

```python
headers = {
    "X-Cognition-Scope-User": "alice",
    "X-Cognition-Scope-Project": "proj-456",
}
```

---

## What's next

You have a running Cognition instance, a connected LLM provider, streaming working in your app, and sessions scoped to your users. The natural next steps:

| What you want to do | Where to go |
|---|---|
| Create custom agent personas (different prompts, tools, models per agent) | [Extending Agents → Custom Agents](./extending-agents.md#3-custom-agents) |
| Inject project-specific context into every session (AGENTS.md) | [Extending Agents → Memory](./extending-agents.md#1-memory-agentsmd) |
| Add skills — progressive disclosure docs the agent reads on demand | [Extending Agents → Skills](./extending-agents.md#2-skills-skillmd) |
| Add retry logic, PII redaction, or call limits to tools | [Configuration → Middleware](./configuration.md#agent-defaults) |
| Run agent code in a Docker container instead of the server process | [Deployment → Docker Sandbox](./deployment.md) |
| Move to PostgreSQL and run multiple server instances | [Deployment](./deployment.md) |
| See every endpoint and SSE event schema | [API Reference](./api-reference.md) |
