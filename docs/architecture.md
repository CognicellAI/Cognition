# Architecture (Draft)

This document summarizes the MVP architecture described in `docs/mvp_designv2.md`
for the Cognition app.

## Components

- Textual TUI (client)
- FastAPI WebSocket Server (transport)
- Session Manager
- Deep Agents Runtime
- Tool Mediator
- Container Executor (per-session)

## Folder Structure
```
cognition/
├── pyproject.toml
├── README.md
├── .env
├── .gitignore
│
├── server/
│   ├── app/
│   │   ├── __init__.py
│   │   │
│   │   ├── main.py                 # FastAPI entrypoint (WebSocket + REST)
│   │   ├── settings.py             # env vars, model config, paths, docker image
│   │   │
│   │   ├── sessions/
│   │   │   ├── __init__.py
│   │   │   ├── manager.py          # session lifecycle + agent/container registry
│   │   │   ├── workspace.py        # per-session workspace dirs + repo mount rules
│   │   │   └── approvals.py        # approval state (MVP: optional)
│   │   │
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── deep_agent.py       # Deep Agents factory + tool binding
│   │   │   ├── prompts.py          # system prompt + coding loop guidance
│   │   │   └── policies.py         # MVP allowlist rules (run_tests, apply_patch, etc.)
│   │   │
│   │   ├── tools/                  # tool *interfaces* exposed to the agent
│   │   │   ├── __init__.py
│   │   │   ├── filesystem.py       # read_file/read_files/apply_patch
│   │   │   ├── search.py           # rg search wrappers
│   │   │   ├── git.py              # git status/diff/add (commit optional)
│   │   │   ├── tests.py            # run_tests (pytest) wrapper
│   │   │   └── safety.py           # argv-only + path confinement checks
│   │   │
│   │   ├── executor/               # trusted side-effect layer
│   │   │   ├── __init__.py
│   │   │   ├── container.py        # start/stop/exec in per-session container
│   │   │   ├── actions.py          # Action models + normalization
│   │   │   ├── repo_ops.py         # implementations (rg/read/apply_patch/git/pytest)
│   │   │   └── limits.py           # timeouts/output caps/resources
│   │   │
│   │   ├── protocol/
│   │   │   ├── __init__.py
│   │   │   ├── messages.py         # client → server models (create_session, user_msg)
│   │   │   ├── events.py           # server → client events (tool_start/output/end)
│   │   │   └── serializer.py       # JSON helpers
│   │   │
│   │   └── streaming/
│   │       ├── __init__.py
│   │       └── emitter.py          # unified event emitter
│   │
│   └── requirements.txt
│
├── client/
│   ├── tui/
│   │   ├── __init__.py
│   │   ├── app.py                  # Textual App
│   │   ├── state.py                # UI state machine (session/network banner)
│   │   ├── websocket.py            # WS client
│   │   ├── renderer.py             # event → UI rendering
│   │   └── widgets/
│   │       ├── log.py              # streaming timeline
│   │       ├── prompt.py           # input prompt
│   │       ├── diff.py             # diff viewer for apply_patch results
│   │       └── approval.py         # approval modal (optional in MVP)
│   │
│   └── requirements.txt
│
├── shared/
│   └── protocol/
│       └── schema.json             # canonical event/message spec
│
└── workspaces/
    └── .gitkeep                    # per-session sandboxes

```

## Data Flow

1. TUI opens a WebSocket session and sends user messages.
2. Server forwards to Session Manager and Deep Agents runtime.
3. Agent requests tools via Tool Mediator.
4. Tool Mediator validates requests and executes via Container Executor.
5. Server streams tool events and assistant messages back to TUI.

## Isolation Model

- One container per session.
- Workspace mounted into container at `/workspace/repo`.
- Network mode OFF by default; ON optional per session.
