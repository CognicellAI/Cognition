# ADR-0003: Agent-Owned Capability Revisions

**Status:** Accepted  
**Date:** 2026-08-03  
**Deciders:** Cognition maintainers  
**Supersedes:** Global MCP-server configuration and runtime resolution  
**Related roadmap/issue:** `ROADMAP.md` — Deep Agents alignment, Agent Skills, and Agent-owned MCP configuration

## Context

Cognition is a multi-tenant backend runtime. Its Agent definition must be a
complete, inspectable description of the behavior and capabilities that a run
can use. A global MCP-server registry separates remote tools from that
definition, permits mutable deployment-wide capability changes, and prevents a
pinned Agent revision from fully identifying its tool set.

Deep Agents provides native Agent Skills and MCP integration primitives.
Cognition must use those primitives while preserving its exact-scope runtime
boundary and immutable run snapshots.

## Decision

1. Agent Skills and remote MCP server declarations belong to one complete Agent
   definition. They are selected by the Agent revision, not by a global runtime
   registry.
2. Updating an Agent creates a new immutable internal configuration revision;
   it does not create another logical Agent. A run resolves and pins one active
   revision at startup. Later updates apply only to later runs.
3. Skill bundles follow the Deep Agents Agent Skills model: each bundle has a
   required `SKILL.md` plus validated supporting files. Cognition materializes
   the selected revision in a private virtual directory and passes it to the
   Deep Agents runtime. Builder-authored scripts execute only in the selected
   session sandbox.
4. Cognition removes the global MCP-server API, ConfigRegistry entity, and
   runtime resolution path. There is no compatibility endpoint, adapter, or
   automatic conversion of legacy global server records.
5. An MCP declaration uses a builder-authored alias and endpoint. The model
   cannot choose the alias, server, endpoint, or capability policy. MCP
   transport authentication and authorization rules are governed by
   [ADR-0004](0004-mcp-transport-authentication-and-builder-authorization.md).

## Alternatives considered

### Retain global MCP CRUD with per-Agent references

This leaves shared mutable state outside the pinned Agent revision and makes
capability ownership ambiguous. It is rejected in favor of complete
Agent-owned configuration.

### Maintain a separate shared Skill catalog as the runtime authority

A shared catalog weakens atomic Agent deployment and lets an Agent's referenced
skill content change independently of its revision. It is deferred; v0.14 uses
Agent-owned bundles.

### Reimplement skills and MCP orchestration outside Deep Agents

Parallel primitives would duplicate upstream runtime behavior and increase
maintenance cost. Cognition uses Deep Agents extension points and adds only
its scope, persistence, and security boundaries.

## Consequences

### Positive

- A pinned Agent revision describes the skills and MCP capability set available
  to a run.
- Builder deployment is atomic and exact-scope isolation applies consistently
  to capability selection.
- Deep Agents remains the underlying skills and MCP runtime abstraction.

### Negative

- This is a breaking change; builders must redeploy Agents using the new shape.
- Global MCP configuration and standalone runtime Skill authority are removed.
- A public historic-version selector is not introduced; revision history is an
  internal runtime and audit concern.

## Migration and rollback

1. Recreate each required skill bundle and MCP declaration in its target Agent
   definition.
2. Drain active runs before deploying the new worker set.
3. Do not run mixed old/new writers or re-enable global MCP configuration.
4. Roll back only before new-format writes or restore the database and object
   storage backup; otherwise roll forward.

## Verification

- Agent create/update/read and runtime resolution preserve complete skill and
  MCP configuration under the exact trusted scope.
- A run pins its revision while a later Agent update affects the next run only.
- Skill validation rejects malformed `SKILL.md`, traversal, special files, and
  normalized duplicate paths.
- Only the selected Agent's MCP declarations contribute tools; no global
  registry or endpoint remains reachable.
- Focused Agent-owned MCP and skill-bundle tests pass alongside the full
  runtime regression suite.

## Architecture model updates

- `docs/proposals/v0.14.0-deep-agents-skills-mcp-storage.md`
- `docs/proposals/v0.14.0-mcp-runtime-contract.md`
- `docs/architecture/decisions/index.md`
- `docs/guides/configuration.md`
- `docs/guides/extending-agents.md`
