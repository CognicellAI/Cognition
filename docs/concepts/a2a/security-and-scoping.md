# Security and Scoping

A2A is a Cognition protocol boundary, not a replacement for builder-owned
authentication and authorization.

## Trusted ingress

The embedding application or gateway authenticates the caller, authorizes the
requested operation, and supplies configured `X-Cognition-Scope-*` headers.
Cognition treats those headers as trusted ingress and carries the resulting
`effective_scope` through the runtime.

Builders own tenant, organization, membership, role, billing, entitlement, and
route-selection models. Cognition intentionally does not infer or administer
those concepts.

## Exact-scope isolation

The same effective scope protects:

- agent discovery and route lookup;
- tasks and contexts;
- sessions and runs;
- messages and events;
- artifacts;
- continuation, subscription, listing, and cancellation.

Part metadata, filenames, URLs, task IDs, and artifact IDs are never
authorization inputs. Missing, cross-agent, and cross-scope identifiers are
reported as not found so the protocol does not disclose inaccessible resources.

## Sandboxes and artifacts

Inbound raw bytes and URL references are persisted by Cognition's scoped
ArtifactStore. Sandboxed agents see virtual `/artifacts/{id}` paths mediated by
Cognition; they do not receive direct database access or a host filesystem
mount.

Receiving a Part never executes it. URL Parts are not downloaded automatically.
Parsing files, fetching remote resources, or executing content requires an
explicit builder-authorized tool and remains subject to sandbox, network, size,
and tool policies.

## Authentication discovery

`COGNITION_A2A_SECURITY_SCHEMES` and
`COGNITION_A2A_SECURITY_REQUIREMENTS` publish canonical A2A authentication
metadata for gateway-protected endpoints. Cognition validates and publishes
these values but does not enforce the advertised authentication scheme. Gateway
enforcement must match the card.

Never place client secrets, bearer tokens, private keys, or private scope values
in Agent Card authentication metadata.

## Exposure controls

The deployment-wide `COGNITION_A2A_ENABLED=false` setting removes the A2A
surface. At the agent level, `a2a.exposed` defaults to `false`. Hidden agents and
agents whose mode is `subagent` are not published even if exposure is requested.
