# A2A Exposed Agent Example

This example shows how to expose Cognition agents via the [Agent-to-Agent (A2A)](https://google.github.io/A2A/) protocol for interoperability with external systems.

## Quick Start

1. Place the agent definition in `.cognition/agents/`:

```yaml
# .cognition/agents/deploy-agent.yaml
name: deploy-agent
mode: primary
a2a_exposed: true
description: Handles deployment workflows
system_prompt: |
  You are a deployment agent. Deploy applications safely and report results.
  Always run tests before deploying. Report the deployment URL when complete.
mcp:
  servers:
    deployment:
      url: https://mcp-egress.internal/mcp/deployment
      auth:
        type: workload_token_exchange
        profile: production_egress
config:
  model: gpt-4o
  temperature: 0.1
```

2. Start Cognition:

```bash
docker compose up -d cognition
```

3. Discover agents via A2A:

```bash
curl -s http://localhost:8000/.well-known/agent-card.json \
  -H "X-Cognition-Scope-User: alice" | python3 -m json.tool
```

4. Send a message to the agent:

```bash
curl -X POST http://localhost:8000/a2a/deploy-agent \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -H "X-Cognition-Scope-User: alice" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [
          {"type": "text", "text": "Deploy the staging environment"}
        ]
      }
    }
  }'
```

## Exposing via API

You can also expose agents via the REST API:

```bash
curl -X POST http://localhost:8000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "code-reviewer",
    "system_prompt": "You are a code reviewer. Review code for quality and security.",
    "mode": "primary",
    "a2a_exposed": true
  }'
```

## Scope Filtering

Agent card discovery respects scope headers. If you use `COGNITION_SCOPE_KEYS=["user", "project"]`:

```bash
# Only shows agents visible to alice in proj-123
curl http://localhost:8000/.well-known/agent-card.json \
  -H "X-Cognition-Scope-User: alice" \
  -H "X-Cognition-Scope-Project: proj-123"
```

## Streaming

Use `SendStreamingMessage` for streamed responses:

```bash
curl -N -X POST http://localhost:8000/a2a/deploy-agent \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -H "X-Cognition-Scope-User: alice" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "SendStreamingMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [
          {"type": "text", "text": "Deploy the staging environment and verify it is healthy"}
        ]
      }
    }
  }'
```

## Python Client Example

```python
import httpx

COGNITION_URL = "http://localhost:8000"
HEADERS = {
    "A2A-Version": "1.0",
    "X-Cognition-Scope-User": "alice",
}

# Discover available agents
resp = httpx.get(f"{COGNITION_URL}/.well-known/agent-card.json", headers=HEADERS)
for card in resp.json()["cards"]:
    print(f"Agent: {card['name']} — {card['description']}")

# Send a message
resp = httpx.post(
    f"{COGNITION_URL}/a2a/deploy-agent",
    json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": "Deploy staging"}],
            }
        },
    },
    headers=HEADERS,
)
result = resp.json()
print(result["result"]["status"]["message"]["parts"][0]["text"])
```

## Notes

- Only agents with `a2a_exposed: true` are discoverable
- Built-in agents (`default`, `readonly`) are not exposed by default
- The `A2A-Version: 1.0` header is required for A2A v1.0 method names
- A2A endpoints are part of the main Cognition server — no additional services needed
- Agents are resolved at request time, so agents created after startup are immediately available
