# Support Agent App Scaffold

This directory is reserved for the learner's API-only Python implementation.

The first implementation should include:

- A small HTTP client for the Cognition backend.
- Session creation.
- SSE parsing for streamed agent progress.
- Support tools that read fixture data from `../data/`.
- Scope headers for tenant and end-user isolation.

Suggested starting files for a later lesson:

```text
app/
├── client.py
├── support_tools.py
└── main.py
```

Do not add a frontend in the first capstone slice. Keep the first goal focused
on the backend contract, tool design, and verification.
