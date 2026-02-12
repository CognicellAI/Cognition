# Hybrid Architecture Proposal

**Status**: Conceptual / Future Consideration  
**Not part of current roadmap**  
**Consider for**: Phase 7+ (Enterprise/Distributed Systems)

---

## Overview

This document explores a hybrid communication architecture that combines the best of both REST/SSE and gRPC protocols. This is not planned for the MVP but represents a potential evolution as Cognition scales to enterprise multi-agent systems.

## The Problem at Scale

As Cognition grows beyond a single-server MVP, several challenges emerge:

1. **Multiple Agent Types**: Planning agents, coding agents, testing agents, research agents
2. **Distributed Execution**: Agents may run in separate containers, pods, or even regions
3. **High-Frequency Communication**: Agents need to coordinate rapidly with low latency
4. **Type Safety**: Cross-service communication needs strong contracts
5. **Observability**: Complex multi-agent workflows need comprehensive tracing

REST/SSE works well for human-facing clients but has limitations for service-to-service communication:
- JSON overhead becomes significant at high volume
- HTTP/1.1 head-of-line blocking
- No native bidirectional streaming for request-response patterns
- Weaker type safety compared to Protocol Buffers

## Proposed Hybrid Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │   TUI    │  │  Web UI  │  │ IDE Ext  │  │   CLI    │        │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
└───────┼─────────────┼─────────────┼─────────────┼───────────────┘
        │             │             │             │
        └─────────────┴──────┬──────┴─────────────┘
                             │
                    REST + SSE (HTTP/2)
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                    API GATEWAY / LOAD BALANCER                  │
│         (Handles auth, rate limiting, SSL termination)          │
└────────────────────────────┼────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                   ORCHESTRATION LAYER                           │
│              (FastAPI - Main API Server)                        │
│  ┌───────────────────────────────────────────────────────┐     │
│  │  REST Endpoints:                                      │     │
│  │  - POST /projects                                     │     │
│  │  - POST /sessions                                     │     │
│  │  - POST /sessions/:id/messages (returns SSE)          │     │
│  │  - GET /health                                        │     │
│  └───────────────────────────────────────────────────────┘     │
└────────────────────────────┬────────────────────────────────────┘
                             │ gRPC (HTTP/2)
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼────────┐  ┌────────▼────────┐  ┌──────▼──────┐
│   Planning     │  │     Coding      │  │   Testing   │
│    Agent       │  │     Agent       │  │    Agent    │
│   (gRPC)       │  │    (gRPC)       │  │   (gRPC)    │
└───────┬────────┘  └────────┬────────┘  └──────┬──────┘
        │                    │                   │
        │                    │                   │
┌───────▼────────────────────▼───────────────────▼──────┐
│              SHARED SERVICES (gRPC)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │   Memory     │  │   File       │  │   LSP      │  │
│  │   Service    │  │   Service    │  │  Service   │  │
│  └──────────────┘  └──────────────┘  └────────────┘  │
└───────────────────────────────────────────────────────┘
```

## Protocol Boundaries

### External: REST + SSE

**Use for:**
- Human-facing clients (TUI, Web, IDE extensions)
- Third-party integrations
- Browser-based tools
- SDK consumers

**Why:**
- Universally supported (every language has HTTP client)
- Excellent tooling (curl, Postman, browser devtools)
- OpenAPI ecosystem (auto-generated docs, SDKs, validation)
- Load balancer and CDN compatible
- Easy to debug and monitor

**Example Flow:**
```
Client → POST /sessions/:id/messages
       ← SSE stream (tokens, tool calls, done)
```

### Internal: gRPC

**Use for:**
- Agent-to-agent communication
- Agent-to-shared-service communication
- Container-to-container within cluster
- High-frequency, low-latency operations

**Why:**
- Binary Protocol Buffers (5-10x smaller than JSON)
- HTTP/2 multiplexing (no head-of-line blocking)
- Bidirectional streaming (request → response stream in single connection)
- Strongly typed contracts (protobuf schemas)
- Built-in load balancing and service discovery
- Deadline propagation and cancellation
- Efficient connection pooling

**Example Flow:**
```protobuf
service AgentCoordinator {
  rpc DispatchTask(TaskRequest) returns (stream TaskUpdate);
  rpc Coordinate(CoordinationMessage) returns (CoordinationResponse);
}

message TaskRequest {
  string task_id = 1;
  string agent_type = 2;
  string payload = 3;
  int64 deadline_ms = 4;
}

message TaskUpdate {
  oneof update {
    ProgressUpdate progress = 1;
    ToolInvocation tool_call = 2;
    CompletionResult result = 3;
    ErrorStatus error = 4;
  }
}
```

## When This Architecture Makes Sense

### Phase 1-6: Don't Use

During MVP and single-server production:
- Single-process agent execution
- No distributed components
- REST + SSE is simpler and sufficient
- gRPC adds complexity without benefit

### Phase 7: Consider

When adding:
- Multiple specialized agent types (planning, coding, testing)
- Containerized agent isolation
- Horizontal scaling (multiple agent pods)
- Multi-region deployment
- Service mesh (Istio, Linkerd)

### Post-MVP: Likely Needed

When:
- 100+ concurrent agent instances
- Sub-50ms inter-agent latency requirements
- Complex multi-agent workflows
- Service ownership by different teams

## Trade-off Analysis

### gRPC Advantages

| Aspect | REST | gRPC | Winner |
|--------|------|------|--------|
| Payload size | JSON ~2-5x larger | Protobuf compact | gRPC |
| Latency | Higher parsing overhead | Low binary overhead | gRPC |
| Streaming | SSE (one-way) | Bidirectional native | gRPC |
| Type safety | JSON Schema/OpenAPI | Protobuf (compile-time) | gRPC |
| Multiplexing | HTTP/1.1 or HTTP/2 | HTTP/2 native | gRPC |
| Code generation | Optional | Built-in | gRPC |
| Deadlines | Manual timeout | Built-in propagation | gRPC |

### REST Advantages

| Aspect | REST | gRPC | Winner |
|--------|------|------|--------|
| Browser support | Native | Needs grpc-web proxy | REST |
| Debugging | curl, devtools, logs | grpcurl, specialized tools | REST |
| Human readability | JSON is readable | Binary, needs tools | REST |
| Load balancers | Universal support | Requires HTTP/2 + specific config | REST |
| Caching | HTTP caching works | Must implement custom | REST |
| Ecosystem maturity | Massive | Smaller but growing | REST |
| Learning curve | Low | Higher (protobuf, codegen) | REST |

## Implementation Strategy

If/when this architecture is adopted:

### Phase 1: Internal gRPC Only

Start with gRPC only for agent-to-agent communication within the cluster:

```python
# Agent A (Planning) calls Agent B (Coding)
import grpc
from generated import agent_pb2, agent_pb2_grpc

channel = grpc.insecure_channel('coding-agent:50051')
stub = agent_pb2_grpc.CodingAgentStub(channel)

request = agent_pb2.CodeRequest(
    task_id="task-123",
    description="Implement auth middleware",
    file_context=["auth.py", "middleware.py"]
)

for update in stub.GenerateCode(request):
    if update.HasField('progress'):
        print(f"Progress: {update.progress.percent}%")
    elif update.HasField('tool_call'):
        print(f"Tool: {update.tool_call.name}")
    elif update.HasField('result'):
        return update.result.code
```

### Phase 2: Service Mesh

Add Istio or Linkerd for:
- mTLS between services
- Traffic splitting (canary deployments)
- Circuit breaking
- Observability (distributed tracing)

### Phase 3: API Gateway

If needed, add a gRPC-to-REST gateway for specific use cases:

```
External Client → REST → API Gateway → gRPC → Internal Services
```

Tools like [grpc-gateway](https://github.com/grpc-ecosystem/grpc-gateway) generate REST endpoints from protobuf.

## Alternatives to Consider

Before committing to gRPC, evaluate these alternatives:

### 1. tRPC
- TypeScript-first RPC framework
- End-to-end type safety without code generation
- Good for TypeScript-heavy environments
- Not as mature for Python

### 2. Connect-RPC
- Simple, lightweight RPC
- Works over HTTP/1.1 or HTTP/2
- Streaming support
- Good interoperability story

### 3. JSON-RPC 2.0
- Simple request-response over WebSocket or HTTP
- Human-readable
- Wide language support
- Less efficient than gRPC

### 4. Keep REST + SSE Everywhere
- Add HTTP/2 push for server-initiated events
- Use binary JSON (BSON, MessagePack) for efficiency
- Accept latency trade-off for simplicity

## Recommendation

**For Cognition:**

1. **Phases 1-6**: REST + SSE everywhere. Proven, simple, well-documented.

2. **Phase 7 evaluation**: If microservices architecture is adopted:
   - Keep REST + SSE for external API
   - Add gRPC for internal service communication
   - Benchmark actual performance gains before committing

3. **Decision criteria for gRPC**:
   - Latency requirements < 50ms between services
   - Throughput > 1000 requests/second
   - Teams are comfortable with protobuf
   - Service mesh (Istio) is already adopted

4. **Decision criteria to stay with REST**:
   - Simple deployment model
   - Team prefers debugging with standard tools
   - Browser clients are primary consumers
   - Latency requirements are relaxed (> 100ms acceptable)

## Conclusion

This hybrid architecture is a powerful pattern used successfully by Netflix, Google, Uber, and others. However, it adds significant complexity and should only be adopted when the scale and requirements justify it.

For Cognition's MVP and early production phases, the simplicity of REST + SSE is the right choice. This document serves as a reference for future architectural decisions as the system evolves.

---

**Document History:**
- Created: 2024 - As architectural reference for future consideration
- Not part of official roadmap
- To be reviewed when planning Phase 7 (Enterprise)
