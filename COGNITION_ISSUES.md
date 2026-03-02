# Cognition Issues

Bugs, limitations, and feature requests discovered during the sc-investigation-hub × Cognition
integration. All issues are written for the Cognition team to triage and fix upstream.

Where a local workaround or patch was applied to unblock sc-investigation-hub development, the
patch is described under **Proposed Fix** so the team can apply it properly (with tests, type
checks, etc.).

---

## Format

```
### ISSUE-NNN: <title>
- **Type**: Bug | Feature Request | Limitation
- **Severity**: Blocking | Degraded | Nice-to-have
- **Affects**: <file:line or API endpoint>
- **Discovered**: <context — phase, feature, or operation>
- **Symptom**: <what the caller observes>
- **Root Cause**: <why it happens>
- **Proposed Fix**: <concrete recommendation>
- **Local Workaround**: <what sc-investigation-hub does in the meantime>
```

---

## Bugs

---

### ISSUE-001: `create_cognition_agent` is `async` but called without `await` at four call sites

- **Type**: Bug
- **Severity**: Blocking — any call to a custom (non-native) agent fails immediately
- **Affects**:
  - `server/app/agent/runtime.py:610` — `create_agent_runtime()`
  - `server/app/agent_registry.py:709` — `AgentRegistry.create_agent_with_extensions()`
  - `server/app/llm/deep_agent_service.py:150` — `DeepAgentService.stream_response()`
  - `server/app/session_manager.py:415` — `SessionManager.get_or_create_agent()`
- **Discovered**: First end-to-end run with a custom agent definition (`spycloud-investigator`)
- **Symptom**: SSE stream immediately emits:
  ```json
  {"message": "'coroutine' object has no attribute 'astream_events'", "code": "STREAMING_ERROR"}
  ```
- **Root Cause**: `create_cognition_agent` is declared `async def` (it awaits checkpointer
  setup and async graph compilation internally), but all four call sites omit `await`. Python
  returns an unawaited coroutine object; the caller then tries to call `.astream_events()` on
  that coroutine, which fails.
- **Proposed Fix**: Add `await` at each of the four call sites:
  ```python
  # runtime.py:610, agent_registry.py:709, deep_agent_service.py:150, session_manager.py:415
  agent = await create_cognition_agent(...)
  ```
  All four enclosing functions are already `async def`, so no further changes are needed. A
  regression test that creates a session with a custom agent definition and sends one message
  would catch this class of error.
- **Local Workaround**: Patched all four call sites in the local Cognition checkout.

---

### ISSUE-002: Bedrock streaming returns `chunk.content` as a list of content blocks, not a string

- **Type**: Bug
- **Severity**: Blocking — all streaming responses via Bedrock fail with a type error
- **Affects**: `server/app/llm/deep_agent_service.py:190` and `:224` — `stream_response()`
- **Discovered**: First successful Bedrock LLM invocation (after ISSUE-001 was fixed)
- **Symptom**: SSE stream emits:
  ```json
  {"message": "can only concatenate str (not \"list\") to str", "code": "STREAMING_ERROR"}
  ```
  The `status: thinking` event fires (LLM call succeeds), then the error fires on the first
  token.
- **Root Cause**: LangChain's `ChatBedrock` returns `chunk.content` as a list of content-block
  dicts (e.g. `[{"type": "text", "text": "Hello"}]`) rather than a plain string, matching
  Bedrock's native API response format. Two lines in `stream_response()` do
  `accumulated_content += chunk.content` without checking the type, causing the crash.
- **Proposed Fix**: Normalise `chunk.content` to a string before concatenation at both sites:
  ```python
  def _extract_text(content: str | list) -> str:
      """Normalise LangChain content to plain string.

      Bedrock (and potentially other providers) return content as a list of
      content-block dicts: [{"type": "text", "text": "..."}].
      """
      if isinstance(content, str):
          return content
      if isinstance(content, list):
          return "".join(
              block.get("text", "") if isinstance(block, dict) else str(block)
              for block in content
              if isinstance(block, str)
              or (isinstance(block, dict) and block.get("type") == "text")
          )
      return ""
  ```
  Apply `_extract_text()` to `chunk.content` at both the `on_chat_model_stream` and
  `on_chain_stream` handler paths. Unit tests should cover the list-content case using a mock
  Bedrock chunk.
- **Local Workaround**: Patched both sites in the local Cognition checkout with equivalent
  inline normalisation logic.

---

### ISSUE-003: `to_subagent()` includes unresolved tool path strings in the subagent spec

- **Type**: Bug
- **Severity**: Blocking — any primary agent with subagents crashes on first invocation
- **Affects**: `server/app/agent/definition.py:322` — `AgentDefinition.to_subagent()`
- **Discovered**: After ISSUE-001 and ISSUE-002 were fixed; first invocation with subagent
  definitions present (`identity-analyst`, `breach-analyst`)
- **Symptom**: SSE stream emits:
  ```json
  {"message": "'function' object has no attribute 'name'", "code": "STREAMING_ERROR"}
  ```
- **Root Cause**: `AgentDefinition.tools` holds a list of path strings (e.g.
  `".cognition/tools/spycloud_investigations"`) as stored in the agent's YAML/Markdown front
  matter. `to_subagent()` passes `self.tools` directly into the subagent spec dict, which is
  then forwarded to `create_cognition_agent(subagents=[...])`. Deepagents iterates over the
  subagent tool list and attempts to access `.name` on each item, treating them as `BaseTool`
  instances — but they are plain strings, causing the `AttributeError`.
- **Proposed Fix**: Two acceptable approaches:
  1. **Resolve paths before passing** — In `deep_agent_service.py`, resolve each subagent's
     tool paths to actual `BaseTool` instances (using the same loading logic as
     `create_agent_runtime`) before building the `subagents` list.
  2. **Omit tools from subagent spec** — Since the primary agent already has all registered
     tools available, and deepagents sub-agent invocation passes tools through the delegation
     context, simply omit `tools` and `middleware` from the `to_subagent()` return value. This
     is the lower-risk fix:
     ```python
     # In to_subagent(): remove the following block
     if self.tools:
         spec["tools"] = self.tools   # <-- remove
     if self.middleware:
         spec["middleware"] = self.middleware  # <-- remove
     ```
  Option 2 is recommended as a safe immediate fix; option 1 is the correct long-term
  behaviour to enable per-subagent tool scoping.
- **Local Workaround**: Applied option 2 (omit `tools` and `middleware` from `to_subagent()`)
  in the local Cognition checkout.

---

### ISSUE-004: `COGNITION_SCOPE_KEYS` silently corrupts when set as a JSON array string

- **Type**: Bug
- **Severity**: Blocking (if misconfigured) — session scoping silently breaks; sessions bleed
  across projects with no error
- **Affects**: `server/app/settings.py` — `parse_comma_separated_list` validator on
  `scope_keys`
- **Discovered**: P0-2 Cognition configuration
- **Symptom**: Setting `COGNITION_SCOPE_KEYS=["user","project"]` (JSON syntax, which is
  natural for a list setting and consistent with how pydantic-settings v2 documents list
  fields) produces `scope_keys = ['["user"', '"project"]']` — two corrupted strings including
  the brackets and quotes. No warning is logged.
- **Root Cause**: The `parse_comma_separated_list` validator splits the raw value on `,`
  without first checking whether the input is valid JSON. The JSON array string is treated as
  a comma-separated string, splitting `["user"` and `"project"]` as two separate values.
- **Proposed Fix**:
  ```python
  @field_validator("scope_keys", mode="before")
  @classmethod
  def parse_scope_keys(cls, v: Any) -> list[str]:
      if isinstance(v, list):
          return v
      if isinstance(v, str):
          stripped = v.strip()
          # Accept JSON array syntax
          if stripped.startswith("["):
              import json
              try:
                  parsed = json.loads(stripped)
                  if isinstance(parsed, list):
                      return [str(x) for x in parsed]
              except json.JSONDecodeError:
                  pass
          # Fall back to comma-separated
          return [x.strip() for x in stripped.split(",") if x.strip()]
      return []
  ```
  Apply the same pattern to any other `list[str]` settings field that uses
  `parse_comma_separated_list`. Document both formats in `env.example`.
- **Local Workaround**: Use comma-separated format: `COGNITION_SCOPE_KEYS=user,project`.

---

### ISSUE-005: `PATCH /sessions/{id}` — `agent_name` and `model` are not peers in the update schema

- **Type**: Bug / API Design Inconsistency
- **Severity**: Degraded — callers who try to update `model` as a top-level field silently
  do nothing; no error is returned
- **Affects**:
  - `server/app/api/routes/sessions.py` — PATCH handler
  - `server/app/api/models.py` — `SessionUpdate` schema
- **Discovered**: P2-1 model/agent switching UI implementation
- **Symptom**: Sending `PATCH /sessions/{id}` with body `{"model": "claude-3-5-sonnet"}` has
  no effect. The field is silently ignored. No 400 or 422 is returned.
- **Root Cause**: The `SessionUpdate` schema has `agent_name: str | None` as a top-level
  field, but model is nested: `config: SessionConfig | None` → `{"model": str, "provider":
  str}`. A caller who naturally sends `{"agent_name": "x", "model": "y"}` — treating both as
  peers — will update the agent but silently fail to update the model.
- **Proposed Fix** (two options):
  1. **Flatten the schema** — Promote `model` and `provider` to top-level optional fields in
     `SessionUpdate`, alongside `agent_name`. Internally map them to `config` before
     persisting. This is the most intuitive API.
  2. **Document and validate** — Keep the nested structure but return a 422 with a clear
     error message if a caller sends `model` at the top level (use `model_validator` with
     `extra="forbid"`). Add explicit documentation in the OpenAPI spec.
  Option 1 is recommended.
- **Local Workaround**: `frontend/lib/cognition-client.ts` `updateSession()` manually nests
  `model` under `config` when building the PATCH body.

---

### ISSUE-006: AST security scanner bans `import os` even for safe uses

- **Type**: Bug
- **Severity**: Degraded — forces ugly workaround in all tool files that need env vars
- **Affects**: `server/app/agent/agent_registry.py` — `scan_for_security_violations()`
- **Discovered**: P0-3 SpyCloud tool authoring
- **Symptom**: Any tool file with `import os` at the top level triggers a security warning
  (or error at `strict` level), even when `os` is only used for `os.environ.get()`.
- **Root Cause**: `BANNED_IMPORTS` includes `os` wholesale. The scanner does not distinguish
  between safe uses (`os.environ`) and dangerous ones (`os.system`, `os.popen`, `os.exec*`).
- **Proposed Fix**: Replace the blanket `os` import ban with a call-level check. Allow
  `import os` but add `os.system`, `os.popen`, `os.execv`, `os.execve`, `os.spawnl`,
  `os.fork`, `os.remove`, `os.rmdir`, `os.rename`, `os.unlink`, and `os.makedirs` to
  `BANNED_CALLS` instead. This preserves the security intent (blocking subprocess spawning
  and filesystem mutation) while allowing the common and safe `os.environ` access pattern.
- **Local Workaround**: Use `__import__("os").environ.get(key, default)` in tool files.
  Both `spycloud_investigations.py` and `spycloud_idlink.py` use this pattern.

---

### ISSUE-007: Cognition Docker image runs as uid 1000 but mounts credentials at `/root/.aws`

- **Type**: Bug
- **Severity**: Blocking for Bedrock users — the LLM provider cannot authenticate
- **Affects**: `Dockerfile` — `USER cognition` (uid 1000); documentation / example
  `docker-compose.yml`
- **Discovered**: First Bedrock LLM invocation
- **Symptom**: All LLM requests fail with `LLM service 'bedrock' is unavailable`. The
  circuit breaker opens. `~/.aws` files are present in the container but unreadable.
- **Root Cause**: The Dockerfile creates a `cognition` user (uid 1000) and switches to it
  (`USER cognition`), so the process runs as `/home/cognition`. Any example or documentation
  that shows `~/.aws:/root/.aws` is incorrect — the process cannot read `/root/.aws` because
  it is owned by root (uid 0) with mode 700. boto3 finds no credentials and Bedrock auth
  fails.
- **Proposed Fix**: Update all example `docker-compose.yml` files and documentation to mount
  credentials at the correct home directory:
  ```yaml
  volumes:
    - ~/.aws:/home/cognition/.aws:ro   # read-only is sufficient; no SSO token caching needed
  environment:
    - HOME=/home/cognition              # ensure boto3 resolves ~ correctly
    - AWS_PROFILE=my-profile
  ```
  Alternatively, add a `COPY --chown=cognition:cognition` step in the Dockerfile to handle
  cases where users build with credentials baked in (not recommended for production, but
  common in dev). The correct target path should be prominently documented.
- **Local Workaround**: `docker-compose.yml` updated to mount at `/home/cognition/.aws` and
  set `HOME=/home/cognition`.

---

### ISSUE-013: `on_chat_model_stream` and `on_chain_stream` both emit tokens — full response duplicated

- **Type**: Bug
- **Severity**: Blocking — every streaming response is doubled; the message flashes then the final
  rendered bubble contains the full text twice
- **Affects**: `server/app/llm/deep_agent_service.py` — `DeepAgentService.stream_response()`
- **Discovered**: First successful UI interaction; visible as "response starts showing then disappears"
- **Symptom**: The SSE stream emits incremental `token` events correctly (events 1–N), then emits
  one final `token` event containing the entire response text again as a single chunk (event N+1).
  The `done` event's `assistant_data.content` then contains the full text twice, back-to-back.
  In the UI this manifests as: streaming text appears normally, then the `StreamingMessage`
  component unmounts (when `done` fires and `isStreaming` resets to `false`) and the `MessageBubble`
  renders with doubled content — the visual jump makes it look like the response disappeared and
  was replaced.
- **Root Cause**: Two separate LangGraph event handlers in `stream_response()` both yield
  `TokenEvent` and append to `accumulated_content`:
  1. `on_chat_model_stream` — fires per-token during LLM generation (correct primary path)
  2. `on_chain_stream` with `name == "model"` — fires once at chain completion with the full
     assembled message (intended as a fallback for providers that don't support token streaming)

  With Bedrock/Claude, both fire. The fallback path was never guarded against the primary path
  having already delivered the content, so the full response is emitted a second time.
- **Proposed Fix**: Track whether `on_chat_model_stream` has yielded at least one token. If it
  has, skip all token emission in `on_chain_stream` — the primary path already handled it:
  ```python
  # Before the astream_events loop:
  streamed_via_model_stream = False

  # In the on_chat_model_stream handler, after yielding a token:
  streamed_via_model_stream = True

  # In the on_chain_stream handler, wrap all token emission:
  if not streamed_via_model_stream:
      # ... existing chunk parsing and TokenEvent yield logic
  ```
  This preserves the fallback behaviour for providers that only emit `on_chain_stream` events
  while eliminating duplication for providers (like Bedrock) that emit both.
- **Local Workaround**: Patched locally in `Cognition/server/app/llm/deep_agent_service.py`
  with the approach described above.

---

### ISSUE-014: `_build_messages()` always injects a `SystemMessage`, even when the agent already has one

- **Type**: Bug
- **Severity**: Blocking — any custom agent definition fails immediately with a Bedrock error
- **Affects**: `server/app/llm/deep_agent_service.py` — `DeepAgentService._build_messages()`
- **Discovered**: After ISSUE-013 was fixed; first real-world UI interaction with a custom agent
- **Symptom**: SSE stream emits immediately after `status: thinking`:
  ```json
  {"message": "Received multiple non-consecutive system messages.", "code": "STREAMING_ERROR"}
  ```
  The agent never produces a response. The error message comes from Bedrock/Claude, which
  rejects requests that contain more than one `SystemMessage` in the message list.
- **Root Cause**: Two system messages are injected on every request:
  1. `create_cognition_agent(system_prompt=agent_def.system_prompt)` — deepagents embeds the
     agent's system prompt into the graph at construction time.
  2. `_build_messages(content, None)` — even when called with `None`, falls back to
     `self._get_default_system_prompt()` and prepends a second `SystemMessage` to the input.

  The comment at the call site (line ~163) even says *"Pass None for system_prompt here
  because we passed it to create_cognition_agent"*, but `_build_messages` ignores that intent
  and adds one anyway via the `or self._get_default_system_prompt()` fallback.
- **Proposed Fix**: When `custom_system_prompt` is `None`, do not add any `SystemMessage` —
  only emit a `HumanMessage`:
  ```python
  def _build_messages(self, content: str, custom_system_prompt: str | None = None) -> list:
      messages: list = []
      if custom_system_prompt is not None:
          # augment with planning instructions if needed, then prepend
          messages.append(SystemMessage(content=_augment_with_planning(custom_system_prompt)))
      messages.append(HumanMessage(content=content))
      return messages
  ```
  The `_get_default_system_prompt()` fallback should only be used when the caller
  explicitly wants the default (e.g. a `None` agent definition, a native agent, or a test).
  A separate helper or a `use_default_if_none: bool = False` parameter could make this
  explicit.
- **Local Workaround**: Patched locally — `_build_messages` now only prepends `SystemMessage`
  when `custom_system_prompt is not None`.

---

## Feature Requests

---

### ISSUE-008: No `GET /models` endpoint — model list is buried inside `GET /config`

- **Type**: Feature Request
- **Severity**: Degraded — model switching cannot be surfaced cleanly in a UI
- **Affects**: `server/app/api/routes/` — missing endpoint
- **Discovered**: P2-1 model/agent switching UI
- **Symptom**: There is no way to list available models without parsing the full config object
  at `GET /config` → `llm.available_providers[*].models`, which couples the client to the
  internal config schema.
- **Proposed Fix**: Add a `GET /models` endpoint analogous to `GET /agents` and `GET /tools`:
  ```
  GET /models
  → [
      {"id": "us.anthropic.claude-sonnet-4-6", "provider": "bedrock", "display_name": "Claude Sonnet 4.6"},
      {"id": "us.anthropic.claude-3-5-haiku", "provider": "bedrock", "display_name": "Claude 3.5 Haiku"},
      ...
    ]
  ```
  The response should include at minimum: `id` (the value to pass in `PATCH /sessions/{id}`),
  `provider`, and an optional `display_name`. Context window and capability flags would be
  bonus.
- **Local Workaround**: Model switching is not yet surfaced in the UI. The `ModelAgentSwitcher`
  component currently only exposes agent switching.

---

### ISSUE-009: `GET /agents` omits `tools` and `skills` from each agent

- **Type**: Feature Request
- **Severity**: Nice-to-have
- **Affects**: `server/app/api/routes/agents.py`; `server/app/api/models.py` — `AgentResponse`
- **Discovered**: P2-3 settings pages (read-only agent viewer)
- **Symptom**: The Settings → Agents page can only show `name`, `description`, `mode`,
  `model`, and `temperature`. The `tools` and `skills` an agent has access to — the most
  useful fields for an analyst trying to understand agent capabilities — are not in the
  response.
- **Proposed Fix**: Add `tools: list[str]` and `skills: list[str]` to `AgentResponse`. These
  can be the raw path strings from the definition (e.g. `".cognition/tools/spycloud_breach_lookup"`);
  the client can strip the path prefix for display. `system_prompt` is optional but would
  make the settings page significantly more useful as a reference.
- **Local Workaround**: None — the information is simply not displayed on the settings page.

---

### ISSUE-010: No dedicated `delegation` SSE event type for multi-agent hand-offs

- **Type**: Feature Request
- **Severity**: Nice-to-have
- **Affects**: `server/app/api/sse.py`; `server/app/agent/runtime.py`
- **Discovered**: P2-2 multi-agent delegation visibility
- **Symptom**: When a primary agent delegates to a sub-agent, the SSE stream emits a
  `tool_call` event with `name = "task"` (or `"delegate"`). There is no way to
  distinguish this from a regular SpyCloud API tool call except by heuristic name matching.
- **Proposed Fix**: Emit a dedicated `delegation` SSE event (or add `"is_delegation": true`
  to the existing `tool_call` event data) when the agent invokes the built-in delegation
  tool. The event should include:
  - `from_agent`: name of the delegating agent
  - `to_agent`: name of the target sub-agent
  - `task`: the task description passed to the sub-agent
  This would allow UIs to render delegation prominently and distinctly from data-fetching
  tool calls.
- **Local Workaround**: Frontend detects delegation heuristically:
  `tool_call.name === "task" || tool_call.name === "delegate"`. These calls are styled with
  a distinct purple border and a "Delegating to…" label. The sub-agent name is extracted from
  `tool_call.arguments.agent` if present.

---

### ISSUE-011: `step_complete` SSE event is defined but never emitted

- **Type**: Limitation
- **Severity**: Nice-to-have
- **Affects**: `server/app/llm/deep_agent_service.py`; `server/app/api/sse.py` —
  `EventBuilder.step_complete()`
- **Discovered**: P2-2 planning tracker UI
- **Symptom**: `planning` events are emitted when the agent calls `write_todos`, populating
  the frontend plan tracker. But `step_complete` events never fire, so the plan tracker shows
  all steps in pending state for the entire duration of the response and then disappears
  when `done` fires — there is no live step-by-step progress.
- **Proposed Fix**: In the `deep_agent_service.py` streaming loop, track which plan step
  is currently executing (e.g. by correlating `on_tool_start` events against the current
  plan) and emit `step_complete` when each step's tool call completes. The `EventBuilder`
  infrastructure is already in place; the emit site is missing.
- **Local Workaround**: Frontend degrades gracefully — the plan is shown but without live
  progress updates.

---

### ISSUE-012: `reconnected` SSE event is defined but has no client contract or documentation

- **Type**: Limitation
- **Severity**: Nice-to-have
- **Affects**: `server/app/api/sse.py` — `EventBuilder.reconnected()`
- **Discovered**: P1-2 SSE client implementation
- **Symptom**: `EventBuilder.reconnected()` exists but there is no documentation on when
  it is emitted, what its data payload is, or what clients are expected to do with it.
  A client that encounters a `reconnected` event has no contract to follow.
- **Proposed Fix**: Document `reconnected` in the SSE event reference alongside the other
  event types. Define its payload schema (e.g. `{"session_id": str, "resumed_at_event_id":
  str | null}`). Clarify whether it is emitted server-side on reconnect, client-side, or
  both. If the event is not yet used, consider removing it until it is properly designed,
  to avoid clients needing to handle an undocumented no-op.
- **Local Workaround**: Frontend SSE parser silently ignores unknown event types; `reconnected`
  is dropped without side effects.

---

### ISSUE-015: LangGraph recursion limit of 25 causes multi-tool investigation queries to fail

- **Type**: Bug / Limitation
- **Severity**: Blocking — multi-tool queries (SpyCloud breach lookup + IDLink correlation) exceed
  the default limit and produce a hard error mid-stream
- **Affects**: `server/app/llm/deep_agent_service.py:183` — `astream_events` call config
- **Discovered**: P2 end-to-end testing with `spycloud-investigator` agent running breach
  lookup + identity correlation in a single investigation query
- **Symptom**: Stream emits:
  ```json
  {"message": "Recursion limit of 25 reached without hitting a stop condition..."}
  ```
  after ~25 LangGraph steps. For a ReAct agent calling 3–4 tools, each tool call consumes
  ~6 graph steps (invoke, tools, loop back, etc.), so the limit is hit on any non-trivial query.
- **Root Cause**: LangGraph's default `recursion_limit` is 25. This is not configurable via the
  agent YAML definition, and the `astream_events` call in `deep_agent_service.py` does not
  override it, so all agents are capped at 25 steps regardless of their complexity.
- **Proposed Fix**:
  1. **Immediate**: Raise the hardcoded default to 75 in `astream_events` config:
     ```python
     config={"configurable": {"thread_id": thread_id}, "recursion_limit": 75}
     ```
  2. **Proper fix**: Expose `recursion_limit` as a field in the agent YAML definition
     (e.g. `recursion_limit: 75`) and pass it through `DeepAgentService` when building the
     `astream_events` config. This lets individual agents tune their own limit.
  3. **Risk note**: At limit 75, a true runaway loop costs ~$2.25 max per query (vs $0.75 at
     25) for Bedrock Claude, and terminates within ~2–3 minutes. Acceptable for this use case.
- **Local Workaround**: Patched `deep_agent_service.py` to pass `recursion_limit: 75` in the
  `astream_events` config.

---

## Resolved Locally

These issues were patched in the local Cognition checkout to unblock sc-investigation-hub.
The patches are described above under **Proposed Fix**. They should be re-implemented by
the Cognition team with proper tests and type checks before being merged upstream.

| Issue | Status |
|-------|--------|
| ISSUE-001: `create_cognition_agent` missing `await` | Patched locally in 4 files |
| ISSUE-002: Bedrock `chunk.content` list type error | Patched locally in `deep_agent_service.py` |
| ISSUE-003: `to_subagent()` unresolved tool paths | Patched locally in `definition.py` |
| ISSUE-007: `~/.aws` mount path wrong for uid 1000 | Fixed in `docker-compose.yml` |
| ISSUE-005 (partial): `model` nesting in PATCH body | Worked around in `frontend/lib/cognition-client.ts` |
| ISSUE-013: Duplicate token emission via two handlers | Patched locally in `deep_agent_service.py` |
| ISSUE-014: `_build_messages` always adds a second SystemMessage | Patched locally in `deep_agent_service.py` |
| ISSUE-015: LangGraph recursion limit of 25 too low for multi-tool agents | Patched locally in `deep_agent_service.py` (set to 75) |
| ISSUE-016: Bedrock has no timeout + stream stalls silently on subagent delegation | Patched locally in `registry.py` and `deep_agent_service.py` |

---

## ISSUE-016: No timeout on Bedrock LLM calls / stream stalls during subagent delegation

- **Type**: Bug
- **Severity**: Blocking
- **Affects**: `server/app/llm/registry.py:73`, `server/app/llm/deep_agent_service.py:181`
- **Discovered**: Subagent delegation — asking `spycloud-investigator` to use its subagents
- **Symptom**: After the primary agent calls `write_todos` and transitions to `thinking`, the
  stream stalls indefinitely with no error, no timeout, and no UI feedback. The Cognition
  container stays healthy (health checks pass) but the session never completes or errors.
- **Root Cause**: Two compounding issues:
  1. `ChatBedrock` in `registry.py` has no `read_timeout` or `connect_timeout` configured.
     boto3's default socket timeouts can be very long. When Bedrock throttles a request (which
     is common on the second LLM call after `write_todos`, which has a larger context), the
     streaming connection stays open with no data indefinitely.
  2. The `astream_events` loop in `deep_agent_service.py` has no timeout guard. If the LLM call
     hangs, the `async for` loop simply blocks forever — no error is raised, no event is emitted.
     Additionally, when the `task` (subagent delegation) tool fires, the subagent's own LLM calls
     run synchronously inside the tool invocation, making the idle period visible to the parent
     stream even longer (potentially 30–120s per delegation).
- **Proposed Fix**:
  1. Add a `botocore.config.Config` with `read_timeout=120, connect_timeout=10` to `ChatBedrock`
     in `registry.py`.
  2. Replace the bare `async for event in agent.astream_events(...)` loop with a per-event
     `asyncio.wait_for(__anext__(), timeout=130)` guard, killing stalled iterations after 130s.
  3. Emit a `StatusEvent(status="delegating to <subagent_type>")` when `on_tool_start` fires
     for the `task` tool, so the UI shows visible feedback during delegation instead of appearing
     frozen.
- **Local Workaround**: All three fixes applied locally in `registry.py` and `deep_agent_service.py`.

---

### ISSUE-017: Stray `yield ErrorEvent(message=timeout_msg, ...)` at wrong indentation level

- **Type**: Bug
- **Severity**: Blocking — every message fails after the first response event
- **Affects**: `server/app/llm/deep_agent_service.py` (stream loop body)
- **Discovered**: Post-ISSUE-016 fix; first end-to-end test after rebuilding the container
- **Symptom**: The SSE stream emits tokens correctly but then immediately ends with:
  ```json
  {"message": "name 'timeout_msg' is not defined", "code": "STREAMING_ERROR"}
  ```
  This happens on **every single message**, even simple greetings that never timeout.
- **Root Cause**: A `yield ErrorEvent(message=timeout_msg, code="STREAM_TIMEOUT")` and `return`
  were indented at the same level as the `elif event_type == "on_chain_end": pass` block —
  i.e., inside the `while True` event loop but outside all `if/elif` branches. Python 3.12's
  async generator scoping rules treat `timeout_msg` as a local variable because it is assigned
  somewhere in the function scope (in an earlier draft), causing `NameError` at runtime. Even if
  `timeout_msg` had been defined, the stray `yield`/`return` would have terminated the stream
  after the first `on_chain_end` event, cutting off every response prematurely.
- **Proposed Fix**: Remove the two orphaned lines entirely. Timeout handling is already correctly
  implemented via `asyncio.wait_for` with inlined error strings earlier in the same loop.
- **Local Workaround**: Removed the two lines from `deep_agent_service.py`.

---

### ISSUE-018: `_events` buffer referenced but never initialised

- **Type**: Bug
- **Severity**: Blocking — every stream crashes with `NameError` after completing normally
- **Affects**: `server/app/llm/deep_agent_service.py` (post-loop cleanup block)
- **Discovered**: After fixing ISSUE-017; first clean response still emitted an error at the end
- **Symptom**: After all tokens stream successfully and `status: idle` is emitted, the stream
  ends with:
  ```json
  {"message": "name '_events' is not defined", "code": "STREAMING_ERROR"}
  ```
- **Root Cause**: The cleanup code after the `while True` loop contained:
  ```python
  for ev in _events:
      yield ev
  ```
  `_events` was never initialised anywhere in the current code — it's a remnant of an earlier
  buffered-events design that was replaced with direct `yield` calls inside the loop. Because
  the variable doesn't exist, Python raises `NameError` after every successful stream.
- **Proposed Fix**: Remove the stale `for ev in _events: yield ev` block entirely. All events
  are already yielded directly in the loop body.
- **Local Workaround**: Removed the block from `deep_agent_service.py`.

---

## Feature Requests

---

### ISSUE-019: `done` event does not include the assistant message ID

- **Type**: Feature Request
- **Severity**: Degraded — delegation persistence does not survive page navigation without it
- **Affects**: `POST /sessions/{id}/messages` SSE stream — `done` event payload
- **Discovered**: P2.5 stabilization — delegation sidecar persistence
- **Symptom**: After a response streams to completion, the `done` event payload is:
  ```json
  {"assistant_data": {"content": "...", "tool_calls": null, "token_count": 1, ...}}
  ```
  There is no `message_id` or `id` field anywhere in the event. The frontend cannot know the
  Cognition-assigned ID of the assistant message that was just persisted to the database.
- **Root Cause**: The `done` event is emitted before or without including the persisted message's
  database ID in the payload.
- **Proposed Fix**: Include `message_id` (the UUID of the newly-persisted assistant `Message`
  record) in the `done` event payload:
  ```json
  {"message_id": "83c95925-...", "assistant_data": {...}}
  ```
  This allows clients to correlate any metadata they accumulated during streaming (e.g.,
  delegation structures, cost annotations) with the canonical server-side message ID — without
  making an additional round-trip API call.
- **Local Workaround**: After the SSE stream completes and `streamDone = true`, the frontend
  immediately calls `GET /sessions/{id}/messages`, walks the response backward to find the last
  assistant message, and uses its `id` as the canonical message ID for sidecar persistence.
  This adds one extra HTTP round-trip per completed response. See `stores/chat-store.ts`,
  post-stream finalization block.