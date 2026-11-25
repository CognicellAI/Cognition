# Cognition

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

Cognition is a headless runtime for building agent applications. Define an Agent,
connect a model provider, and invoke it through REST with Server-Sent Events or
expose it through Agent-to-Agent (A2A) 1.0. Cognition uses
[Deep Agents](https://github.com/langchain-ai/deepagents) and
[LangGraph](https://github.com/langchain-ai/langgraph) for agent execution and
checkpointing.

- **Agent definitions and runs** — Provision Agents through the API or workspace
  files. Each run pins the resolved definition and dependencies. Sessions,
  tasks, run events, checkpoints, and artifacts have distinct storage roles.
- **Tools and execution** — Configure remote MCP servers per Agent, mount Skills
  in its sandbox workspace, and extend behavior through middleware and
  subagents. Execution backends include local development, Docker, Kubernetes,
  and AWS Lambda MicroVM.
- **Storage and operations** — Use SQLite for local persistence or PostgreSQL
  for shared runtime state. Configure OpenTelemetry traces, Prometheus metrics,
  and structured logs for operation and investigation.

Your application owns caller identity, authorization, tenancy, and the product
experience. Cognition enforces exact scope on runtime resources using trusted
context supplied by your gateway or embedding service. See the
[architecture overview](docs/architecture/index.md) and
[builder boundary guide](docs/guides/core-vs-app-layer.md).

## Quick start

This example uses [uv](https://docs.astral.sh/uv/), Python 3.11 or newer,
`curl`, `jq`, and an OpenAI API key. Run it in a fresh clone on a trusted
development machine. The example enables host-local execution and binds the
server to loopback.

```sh
git clone https://github.com/CognicellAI/Cognition.git
cd Cognition
mkdir -p .cognition
cat > .cognition/config.yaml <<'YAML'
llm:
  provider: openai
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY
YAML
```

Set `OPENAI_API_KEY` in your shell. Start the server with:

```sh
COGNITION_ALLOW_UNSAFE_LOCAL_EXECUTION=true \
  uv run --locked --no-dev --extra openai --extra deploy cognition serve --host 127.0.0.1 --port 8000
```

In another terminal, provision an Agent, create a session for it, and stream a
message:

```sh
curl -fsS http://127.0.0.1:8000/ready

curl -fsS http://127.0.0.1:8000/agents \
  -H 'Content-Type: application/json' \
  -d '{"name":"hello","mode":"primary","system_prompt":"Answer briefly."}'

session_id=$(curl -fsS http://127.0.0.1:8000/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"First session","agent_name":"hello"}' | jq -er '.id')

curl -fsSN "http://127.0.0.1:8000/sessions/$session_id/messages" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"content":"Say hello in one sentence."}'
```

For other model providers, see [configuration](docs/guides/configuration.md).
For shared deployments, see [deployment and operations](docs/architecture/08-deployment-and-operations.md)
and [security and scoping](docs/concepts/security.md).

## Documentation

| Topic | Guide |
| --- | --- |
| System boundaries, runtime flows, and decisions | [Architecture](docs/architecture/index.md) |
| Expose an Agent and invoke it over A2A 1.0 | [A2A builder guide](docs/guides/a2a.md) |
| Add remote MCP tools, Skills, middleware, or subagents | [Extending Agents](docs/guides/extending-agents.md) |
| Choose an execution backend | [Sandboxes](docs/concepts/sandboxes/index.md) |
| Work with sessions, messages, and streamed events | [API reference](docs/guides/api-reference.md) |
| Use the terminal client or explore reference applications | [Cognition CLI](docs/blueprints/cognition-cli.md) · [Blueprints](docs/index.md#blueprints) |
| Configure a deployment or Agent from an example | [Examples](examples/README.md) |

Browse the [documentation site](https://cognition.cognicellai.com/docs/) or
[repository documentation](docs/index.md).

## Contributing

Bug reports and pull requests are welcome. Open an issue before submitting
large changes. See [AGENTS.md](AGENTS.md) for the development workflow and the
[release checklist](docs/guides/release-checklist.md) for release validation.

```sh
uv run --locked --all-extras python -m pytest tests/unit
uv run --locked --extra docs mkdocs build --strict
```

## License

[MIT](LICENSE) © CognicellAI
