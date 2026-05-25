# Scoped Multi-Tenant Example

This example shows the settings and payload patterns used for scoped, multi-tenant deployments.

Scope keys are **builder-defined** — you choose the keys that match your tenancy model.
Cognition does not hardcode a vocabulary.

## Configuration

```yaml
# .cognition/config.yaml
scoping:
  enabled: true
  scope_keys:
    - "user"
    - "project"
```

## Headers

Typical request headers for the above config:

    X-Cognition-Scope-User: alice
    X-Cognition-Scope-Project: gateway

If `COGNITION_SCOPE_KEYS` includes additional values, Cognition expects corresponding
`X-Cognition-Scope-{key}` headers.

## How scope propagates

The scope flows through the full stack:

1. HTTP headers (`X-Cognition-Scope-*`) → `SessionScope` (FastAPI dependency)
2. `effective_scope` dict → ConfigRegistry CRUD (scoped reads/writes)
3. Session persistence (stored on `ManagedSession`)
4. `CognitionContext.effective_scope` → LangGraph `runtime.context`
5. Middleware reads from `runtime.context` (trusted)
6. Tools receive via runtime context, **not** model-supplied arguments

## Other scope key examples

```yaml
# SaaS platform with org-level isolation
scoping:
  scope_keys:
    - "org"
    - "env"

# Multi-team with department + team
scoping:
  scope_keys:
    - "department"
    - "team"
```
