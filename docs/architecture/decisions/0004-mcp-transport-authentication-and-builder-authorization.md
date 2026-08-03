# ADR-0004: MCP Transport Authentication and Builder Authorization

**Status:** Accepted  
**Date:** 2026-08-03  
**Deciders:** Cognition maintainers  
**Supersedes:** Raw-header MCP configuration, scope-header injection, and public authentication callbacks
**Related roadmap/issue:** `ROADMAP.md` — MCP authentication, readiness, and multi-tenant transport security

## Context

Protected MCP endpoints need authentication for both tool discovery and tool
invocation. Cognition must support direct provider endpoints and
builder-operated gateways without becoming a credential vault, identity
provider, or Agent IAM control plane.

Authentication must be outside model control. Optional Agent middleware and
builder-installed Python callbacks cannot provide that guarantee because they
may be omitted, reordered, or execute after transport construction. The
transport boundary therefore needs built-in, standards-based modes with
deployment policy owned by the builder.

## Decision

1. Agent MCP configuration supports four authentication types: `none`,
   `mcp_oauth`, `workload_token_exchange`, and `static_bearer`. Raw headers,
   bearer values, API keys, URL credentials, secret values, and executable
   authentication callbacks are invalid Agent configuration.
2. `none` adds no Cognition credential, identity, or trusted-context header. It
   never changes modes in response to an authentication challenge.
3. `mcp_oauth` uses standard MCP authorization through the upstream MCP SDK.
   Persistent token state is encrypted in the database and partitioned by exact
   `effective_scope`, immutable Agent identity, and canonical server URI. It
   never downgrades to anonymous transport.
4. `workload_token_exchange` references an opaque deployment profile, not a
   credential or callback. Cognition's built-in exchange client obtains an
   ambient workload identity and exchanges it for a short-lived token limited
   to one configured audience or resource. The Agent and model cannot select
   the token endpoint, subject-token source, audience, or resource.
5. A shared workload token authenticates the Cognition workload, not a logical
   Agent. Cognition sends immutable Agent identity and revision, exact trusted
   scope, server identity, correlation identifiers, and deadline in a fixed,
   reserved runtime-context envelope. A builder-controlled gateway validates
   the workload and performs live Agent authorization on every discovery and
   invocation before resolving any upstream provider credential.
6. `static_bearer` reads a named environment variable at transport construction
   and does not persist or project its value. It is supported but not
   recommended because long-lived bearer tokens have weaker lifecycle,
   scoping, and revocation properties.
7. Cognition does not infer a production or multi-tenant environment and does
   not ban an authentication type based on such a classifier. Builders own
   endpoint admission, authentication-mode policy, identity infrastructure,
   egress policy, and workload-isolation topology.
8. Credentials, authentication headers, OAuth payloads, raw scope values, tool
   arguments, and tool results never enter model context, Agent configuration,
   API responses, durable state, logs, traces, metrics, or readiness
   projections.
9. Cognition's mandatory MCP transport factory applies authentication to
   discovery, readiness observation, and invocation. It overwrites reserved
   runtime-context fields and prevents model-controlled transport selection.
10. Discovery is per server. Canonical tool identity is
    `(server_alias, provider_tool_name)`. Required-server failure stops the run
    with a typed, redacted error; optional-server failure preserves healthy
    tools. Readiness is a freshness-qualified observation, not authorization
    truth.

## Alternatives considered

### Put credentials or generic secret references in Agent definitions

This creates a Cognition secret-distribution surface and risks exposure through
configuration APIs, persistence, telemetry, and model context. It is rejected.

### Install a custom authentication callback or LangChain middleware

A public callback makes authentication proprietary and builder code part of the
Cognition process. Optional middleware is not a mandatory transport boundary.
Both are rejected in favor of built-in standards-based transport modes.

### Make Cognition own live binding authorization

That would duplicate the builder's tenant, role, connection, and credential
model. Cognition authenticates its transport and carries trusted runtime
context; the builder authorizes its own resources.

### Claim a shared workload token represents one logical Agent

A shared process identity cannot cryptographically prove isolation among
logical Agents. Cognition represents the workload honestly and relies on live
gateway authorization. Builders that need cryptographic Agent or tenant
isolation must assign separate workload identities at that execution boundary.

## Consequences

### Positive

- Direct protected providers use interoperable MCP OAuth without
  builder-specific Cognition code.
- Builder gateways use OAuth token exchange without installing Python into
  Cognition or exposing upstream provider credentials.
- Model input cannot alter authentication, endpoint selection, scope, profile,
  audience, resource, or server identity.
- Builders retain authority over deployment posture while Cognition guarantees
  structural isolation and redaction.

### Negative

- Direct OAuth requires encrypted, exact-scope token persistence and a
  builder-owned user-facing authorization experience.
- Workload token exchange requires compatible workload identity, authorization
  server, and gateway infrastructure.
- `static_bearer` remains a builder-accepted operational risk and does not gain
  the lifecycle properties of OAuth-based modes.
- Shared workload identities provide logical, not cryptographic, isolation
  between Agents.

## Migration and rollback

1. Classify every Agent-owned MCP server as `none`, `mcp_oauth`,
   `workload_token_exchange`, or `static_bearer`.
2. Configure encrypted database token persistence before enabling `mcp_oauth`.
3. Define deployment profiles and ambient subject-token sources before enabling
   `workload_token_exchange`; profiles are deployment configuration, not Agent
   configuration.
4. Provide named environment variables before enabling `static_bearer`.
5. Reject legacy raw headers and callbacks rather than translating them.
6. Roll back only before new token/configuration writes or restore the durable
   backup; do not revert to raw-header configuration.

## Verification

- `none` adds no credential, identity, or context header.
- Direct OAuth supports upstream discovery, PKCE, resource indicators, refresh,
  and scope challenges with exact scope/Agent/server token isolation.
- Workload exchange resolves one deployment-controlled audience or resource;
  model or Agent content cannot override its identity inputs.
- Two scoped Agents using the same alias can receive different live gateway
  authorization without credential or authorization-result cross-use.
- Revocation during a pinned run denies the next gateway operation.
- `static_bearer` reads only the configured environment variable and never
  persists or returns its value.
- Model-supplied authorization, URL, redirect, scope, alias, profile, audience,
  resource, or target cannot affect discovery or invocation transport.
- Required/optional failures, duplicate canonical identities, readiness
  freshness, telemetry redaction, and metric-cardinality constraints are
  tested.

## Architecture model updates

- `docs/proposals/v0.14.0-mcp-runtime-contract.md`
- `docs/proposals/v0.14.0-deep-agents-skills-mcp-storage.md`
- `docs/architecture/decisions/index.md`
- `docs/concepts/security.md`
- `docs/guides/configuration.md`
- `docs/guides/extending-agents.md`
