# Example Workspace Agent Guidance

This file demonstrates the kind of project-local guidance Cognition can load by default through `agent.memory`.

## Project Rules

- Prefer minimal diffs over broad rewrites.
- Run focused tests before broad test suites.
- Treat generated files as read-only unless the task explicitly targets them.

## Review Policy

When asked for a review:
- prioritize bugs and regressions
- identify missing tests
- call out API or schema compatibility risks first

## Runtime Notes

- Provider configuration may be scoped and API-managed.
- New sessions should pick up newly registered tools automatically.
- Use the project `.cognition/` directory as the source of truth for file-based extensions.
