# Scoped Multi-Tenant Example

This example shows the settings and payload patterns used for scoped, multi-tenant deployments.

Typical request headers:

```http
X-Cognition-Scope-User: alice
X-Cognition-Scope-Project: gateway
```

If `COGNITION_SCOPE_KEYS` includes additional values, Cognition expects corresponding headers.
