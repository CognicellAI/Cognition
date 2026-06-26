# Support Agent Verification Scaffold

This directory is reserved for verification checks. The checks should prove the
agent-system behavior, not merely confirm that the example files exist.

The first verification pass should prove:

- JSON fixtures parse.
- Cognition can create a session.
- A message can be sent and streamed as agent progress.
- Support tools can read the expected tenant's ticket data.
- Requests scoped to one tenant cannot read another tenant's ticket data.

Checks that require a live LLM provider should skip gracefully when credentials
are missing.

Suggested starting files for a later lesson:

```text
verify/
├── verify_fixtures.py
├── verify_streaming.py
└── verify_scope.py
```
