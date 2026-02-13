# Quick Start Guide

> **Hello, Substrate.**

This guide will get you up and running with the Cognition API in 5 minutes. You will learn how to start the engine, create a session, and execute your first agent command.

## Prerequisites

- Docker and Docker Compose installed
- curl (or any HTTP client)

## 1. Start the Engine

Run the following command in the root of the repository:

```bash
docker-compose up -d
```

Verify the system is healthy:

```bash
curl -s http://localhost:8000/health | jq .
# {
#   "status": "healthy",
#   "version": "0.1.0",
#   ...
# }
```

## 2. Create a Session

A **Session** (or "Thread") is the container for your interaction. It persists state and conversation history.

```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "title": "My First Investigation",
    "project_id": "demo-project"
  }'
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "thread_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "status": "active",
  ...
}
```

Save the `id` from the response. We'll use it as `SESSION_ID`.

## 3. Send a Message

Now, let's instruct the agent to do something. We'll ask it to list the files in the current workspace.

**Note:** The API streams responses using Server-Sent Events (SSE).

```bash
export SESSION_ID="<your-session-id>"

curl -N -X POST "http://localhost:8000/sessions/$SESSION_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "List the files in the current directory."
  }'
```

**Output Stream:**

You will see a stream of events:

1.  **`token`**: The agent acknowledging the request.
2.  **`tool_call`**: The agent executing the `ls` command.
    ```json
    event: tool_call
    data: {"name": "execute", "args": {"command": "ls -la"}}
    ```
3.  **`tool_result`**: The output of the command.
    ```json
    event: tool_result
    data: {"output": "total 0\n..."}
    ```
4.  **`token`**: The agent summarizing the result.
5.  **`done`**: The stream ends.

## 4. Inspect the Trace

Because Cognition is built for trust, you can inspect exactly what happened.

Open your browser to [http://localhost:16686](http://localhost:16686) (Jaeger UI).
1.  Select Service: `cognition`
2.  Click **Find Traces**
3.  Click on the most recent trace.

You will see a waterfall diagram showing:
- The HTTP request
- The Agent's reasoning step
- The `LocalSandbox.execute` call
- The database write (checkpointing)

## Next Steps

- **[Build a Platform](./building-platforms.md):** Learn how to integrate this API into your React/Vue app.
- **[Add Custom Tools](./custom-tools.md):** Teach the agent new skills.
