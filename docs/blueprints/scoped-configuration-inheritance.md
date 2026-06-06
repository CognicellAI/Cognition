# Scoped Configuration Inheritance

Scoped Configuration Inheritance lets builders define broad configuration once
and override it at narrower ownership levels without weakening Cognition's
runtime isolation model.

This feature keeps Cognition's existing builder-defined scope model:

```python
effective_scope: dict[str, str]
```

There are no reserved scope keys. Builders define the vocabulary that matches
their tenancy model, such as `org`, `project`, and `user`, or
`customer`, `environment`, `repo`, and `actor`.

## Core Rule

Scope has two separate jobs:

1. **Configuration resolution** decides which definitions a runtime inherits
   before a session or run starts.
2. **Runtime ownership** decides who owns durable work produced by that session
   or run.

Configuration resolution may be hierarchical. Runtime ownership remains exact.

## Example

Given:

```python
effective_scope = {
    "org": "acme",
    "project": "mobile-app",
    "user": "automation",
}
```

and:

```yaml
scoping:
  enabled: true
  scope_keys:
    - org
    - project
    - user
  config_inheritance:
    enabled: true
    hierarchy:
      - org
      - project
      - user
```

Cognition resolves configuration from broadest to narrowest:

```text
{}
{"org": "acme"}
{"org": "acme", "project": "mobile-app"}
{"org": "acme", "project": "mobile-app", "user": "automation"}
```

For each config entity name, the most-specific matching definition wins.

## Builder-Defined Keys

Cognition should validate hierarchy shape, not key names:

1. Every hierarchy key must exist in `scope_keys`.
2. Hierarchy keys must be unique.
3. Inheritance must be explicitly enabled.
4. Missing configured scope headers still fail closed when scoping is enabled.

Valid domain-specific configuration:

```yaml
scoping:
  enabled: true
  scope_keys:
    - customer
    - environment
    - repo
    - actor
  config_inheritance:
    enabled: true
    hierarchy:
      - customer
      - environment
      - repo
      - actor
```

## Configuration Surfaces

Inheritance should apply only to configuration entities where parent defaults
and child overrides are useful:

- agents
- tools
- skills
- model/provider profiles
- MCP server registrations
- middleware/default policy config
- sandbox/runtime profiles once those are formalized

The effective runtime definition should be inspectable enough for builders to
understand which layer supplied each selected config value.

## Runtime Scope Contract

Runtime state is exact-scope owned.

For a run created with:

```python
{
    "org": "acme",
    "project": "mobile-app",
    "user": "automation",
}
```

the same exact scope remains persisted on:

- sessions
- session runs
- session events
- messages and checkpoint projections
- approvals
- artifacts
- memory namespaces
- sandbox metadata
- callbacks
- logs, metrics, and traces

A request with only `{"org": "acme"}` must not automatically read all child
project sessions. If a builder wants organization-admin views, that must be an
explicit builder-authorized access path, not an inferred Cognition entitlement.

## Non-Goals

- Do not introduce reserved scope keys.
- Do not infer admin access from partial parent scopes.
- Do not make hierarchy a runtime authorization model.
- Do not make Cognition an IAM, RBAC, OAuth, or entitlement system.
- Do not introduce secret references, secret resolution, or secret injection.
- Do not change exact runtime scope enforcement by default.

## Observability

Builders need to see where effective config came from without exposing raw
scope values in public events.

Useful attribution fields:

- entity type
- entity name
- selected source scope keys
- specificity depth
- source kind, such as `global`, `parent`, or `exact`
- config fingerprint
- session/run/thread correlation ids when runtime-bound

Public events should expose key names and specificity, not raw tenant values:

```json
{
  "event_type": "config.resolution.selected",
  "entity_type": "mcp_server",
  "entity_name": "github-tools",
  "scope_keys": ["org", "project"],
  "specificity": 2
}
```

## Implementation Plan

1. Add settings/config fields for `config_inheritance.enabled` and
   `config_inheritance.hierarchy`.
2. Add a deterministic scope-chain builder from `effective_scope` plus
   hierarchy.
3. Update ConfigRegistry get/list resolution paths to support explicit
   hierarchical mode.
4. Preserve exact/default behavior when inheritance is disabled.
5. Add config source attribution events/logs with scope-safe public fields.
6. Keep runtime read/write checks exact-scope by default.
7. Update API/configuration docs after implementation.

## Test Plan

Unit tests:

- hierarchy keys must be a subset of `scope_keys`
- duplicate hierarchy keys are rejected
- no reserved key assumptions
- exact mode behavior remains unchanged
- hierarchical lookup picks the most-specific match
- sibling scopes do not match
- missing scope headers still fail closed
- runtime session/run/event access remains exact
- MCP config inheritance works without leaking headers through API responses

E2E scenario:

1. Configure `scope_keys=["org", "project", "user"]`.
2. Enable config inheritance with hierarchy `["org", "project", "user"]`.
3. Register an org-level agent default.
4. Register a project-level MCP server override.
5. Start a session with full org/project/user scope.
6. Confirm the runtime uses inherited config.
7. Confirm runtime data does not become visible to a parent-only request.
