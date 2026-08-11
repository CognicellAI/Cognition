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

1. Remote MCP server declarations belong to the complete Agent definition.
   Skills are a selected sandbox capability: the builder mounts them into the
   isolated Agent workspace before Cognition constructs the runtime.
2. Updating an Agent creates a new immutable internal configuration revision;
   it does not create another logical Agent. A run resolves and pins one active
   revision at startup. Later updates apply only to later runs.
3. Skills follow the Deep Agents Agent Skills model: the builder supplies a
   `SKILL.md` plus supporting files under `<sandbox workspace>/skills`, and
   Cognition passes that root directly to Deep Agents. Cognition does not fetch,
   store, validate, or expose Skill bundle payloads.
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

### Maintain a Cognition Skill catalog as the runtime authority

A Cognition catalog makes the backend a registry client, installer, and package
store. Builders already own sandbox admission and package selection, so the
runtime consumes the mounted workspace instead.

### Reimplement skills and MCP orchestration outside Deep Agents

Parallel primitives would duplicate upstream runtime behavior and increase
maintenance cost. Cognition uses Deep Agents extension points and adds only
its scope, persistence, and security boundaries.

## Consequences

### Positive

- A pinned Agent revision describes MCP capability; the selected sandbox image
  and mounted workspace describe Skills available to a run.
- Builder deployment is atomic and exact-scope isolation applies consistently
  to capability selection.
- Deep Agents remains the underlying skills and MCP runtime abstraction.

### Negative

- This is a breaking change; builders must redeploy Agents using the new shape.
- Global MCP configuration and standalone runtime Skill authority are removed.
- A public historic-version selector is not introduced; revision history is an
  internal runtime and audit concern.

## Migration and rollback

1. Configure each sandbox initializer to mount the intended Skill directories
   below its workspace root; retain MCP declarations in Agent definitions.
2. Drain active runs before deploying the new worker set.
3. Do not run mixed old/new writers or re-enable global MCP configuration.
4. Roll back only before new-format writes or restore the database and object
   storage backup; otherwise roll forward.

## Verification

- Agent create/update/read and runtime resolution preserve complete MCP
  configuration under the exact trusted scope.
- A run pins its revision while a later Agent update affects the next run only.
- Native Deep Agents discovery finds builder-mounted `SKILL.md` directories
  through the selected sandbox backend.
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
