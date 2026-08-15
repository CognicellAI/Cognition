# Tasks and Streaming

Cognition projects A2A operations onto its durable runtime rather than keeping
protocol-local task state. Native REST/SSE and A2A therefore share persistence,
execution, cancellation, artifacts, and observability.

## Identity mapping

```text
A2A contextId -> Cognition session
A2A taskId    -> durable RuntimeTask
execution     -> one SessionRun attempt
```

A task can contain multiple runs. The first message creates the task, context,
session, and initial run. A valid continuation keeps the same task and context
while creating a new run attempt.

## Message idempotency

The client-provided `messageId` is the idempotency identity for delivery within
the agent and effective scope. Repeating the same message returns the existing
task instead of creating another task, run, or set of derived input artifacts.

## State and continuation

Task state is derived from durable runtime state. Interrupted work can surface as
`TASK_STATE_INPUT_REQUIRED` or `TASK_STATE_AUTH_REQUIRED`. Follow-up messages
continue an eligible interrupted task. Terminal tasks reject ambiguous
continuation.

## Streaming

`SendStreamingMessage` streams ordered A2A task, status, message, and artifact
updates over the JSON-RPC binding. `SubscribeToTask` replays the durable task
projection and continues with new events, allowing a client to reconnect without
depending on process-local buffers.

Streaming does not weaken scope checks: subscriptions resolve the task using the
same agent and exact effective scope as every other operation.

## Cancellation

`CancelTask` requests cancellation through the shared runtime lifecycle. A task
that is missing, outside the caller's scope, belongs to another agent, or is no
longer cancelable returns the corresponding A2A error without revealing whether
an inaccessible identifier exists.

## Persistence and observability

Tasks, sessions, runs, messages, events, and artifacts use Cognition's configured
memory, SQLite, or PostgreSQL backends. Runtime events retain the same session,
run, task, thread, and trace correlation used by native APIs.
