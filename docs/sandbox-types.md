# Sandbox Types and Engine Run Modes

**Status:** Brainstorming / Future Exploration  
**Last Updated:** 2026-02-12  

This document explores potential sandbox backends and engine run modes for Cognition. These ideas are not currently on the roadmap but represent promising directions for future development.

## Overview

Cognition is designed as an AI engine that supports multiple sandbox backends. The sandbox abstraction allows the engine to run in different environments while maintaining a consistent API. This enables use cases ranging from local development to production SaaS deployments.

The "run mode" determines which sandbox backend is active for a given workspace or session.

## Sandbox Taxonomy

### By Isolation Level

#### 1. Process Isolation (Lightweight)
- **LocalDirectorySandbox** - Direct filesystem access (current implementation)
- **SubprocessSandbox** - Isolated process, same filesystem
- **ChrootSandbox** - Filesystem-level isolation without containers

**Characteristics:** Fast, low overhead, suitable for trusted environments

#### 2. Container Isolation (Medium)
- **DockerSandbox** - Standard Docker containers
- **PodmanSandbox** - Daemonless container alternative
- **ContainerdSandbox** - Lower-level container runtime

**Characteristics:** Good balance of isolation and performance, industry standard

#### 3. VM Isolation (Strong)
- **FirecrackerMicroVMSandbox** - AWS Firecracker microVMs
- **KVM/QemuSandbox** - Full virtualization
- **CloudHypervisorSandbox** - Modern Rust-based VMM

**Characteristics:** Maximum isolation, suitable for untrusted code

#### 4. Cloud-Native (Managed)
- **LambdaSandbox** - AWS Lambda functions
- **ECSFargateSandbox** - AWS serverless containers
- **CloudRunSandbox** - Google Cloud serverless
- **AzureContainerInstancesSandbox** - Azure managed containers

**Characteristics:** No infrastructure management, auto-scaling, pay-per-use

#### 5. Orchestration (Scalable)
- **KubernetesSandbox** - K8s pods
- **NomadSandbox** - HashiCorp Nomad jobs
- **SwarmSandbox** - Docker Swarm services

**Characteristics:** Production-grade orchestration, complex but powerful

### By Lifecycle Model

| Type | Creation | Destruction | Use Case |
|------|----------|-------------|----------|
| **Persistent** | Server start | Server stop | Local dev, single tenant |
| **Workspace-lifecycle** | First session | Last session ends | Team environments |
| **Session-lifecycle** | Session start | Session ends | Maximum isolation |
| **Ephemeral** | Per operation | Operation complete | Stateless processing |
| **Scheduled** | On cron/job | After completion | Batch processing |

### By Resource Model

1. **Shared Resources**
   - Direct host access
   - Shared filesystem
   - *Use case:* Trusted environments, maximum performance

2. **Limited Resources**
   - CPU/memory quotas
   - Disk limits
   - Network restrictions
   - *Use case:* Controlled environments, multi-tenant

3. **Burstable Resources**
   - Scale to zero (Lambda)
   - Auto-scale (ECS/K8s)
   - *Use case:* Cost optimization, variable workloads

4. **Dedicated Resources**
   - Fixed allocation
   - Guaranteed performance
   - *Use case:* Production workloads, SLAs

## Cloud Provider Options

### AWS-Native

#### Lambda Sandbox
```yaml
run_mode: lambda
sandbox:
  type: aws_lambda
  runtime: python3.11
  timeout: 900  # 15 minutes
  memory: 10240  # 10GB max
  
# Use case: Short operations, file commands, quick processing
# Pros: Serverless, cheap for sporadic use, auto-scaling
# Cons: 15min limit, cold starts, stateless
```

**Best for:**
- File read/write operations
- Git commands
- Quick validations
- Stateless processing

#### ECS Fargate Sandbox
```yaml
run_mode: ecs_fargate
sandbox:
  type: aws_ecs_fargate
  cpu: 1024
  memory: 2048
  storage: 20
  
# Use case: Long-running interactive sessions
# Pros: Full container capabilities, persistent, no server management
# Cons: Higher cost for idle time, slower startup
```

**Best for:**
- Interactive development sessions
- Long-running AI conversations
- Full development environments

#### EKS Fargate Sandbox
```yaml
run_mode: eks_fargate
sandbox:
  type: aws_eks_fargate
  namespace: cognition-workspaces
  service_account: cognition-runner
  
# Use case: Kubernetes-native with serverless nodes
# Pros: K8s API, no node management, pod-per-workspace
# Cons: Complex setup, higher baseline cost
```

### Hybrid Approaches

#### Lambda + ECS Hybrid
```yaml
run_mode: hybrid_aws
sandbox:
  primary:
    type: ecs_fargate
    use_for: [sessions, streaming, long_running]
  secondary:
    type: lambda
    use_for: [file_ops, git, quick_commands]
    
# Intelligent routing:
# - File reads/writes → Lambda (fast, cheap)
# - Chat sessions → ECS (persistent, streaming)
# - Git operations → Lambda (stateless)
# - Code execution → ECS (full environment)
```

**Benefits:**
- Optimize cost per operation type
- Fast response for simple operations
- Full capabilities for complex tasks

## Specialized and Emerging Options

### WebAssembly (Wasm) Sandbox
```yaml
run_mode: wasm
sandbox:
  type: wasm_runtime
  runtime: wasmtime
  capabilities: [filesystem, networking]
  
# Near-native performance
- Secure by design (memory-safe)
- Fast startup (instant)
- Sandboxed execution
- Portable across platforms
```

**Potential use cases:**
- Tool execution in browser
- Edge computing
- Plugin system for extensions

### GitHub Codespaces Integration
```yaml
run_mode: codespaces
sandbox:
  type: github_codespaces
  prebuild: true
  dotfiles_repo: user/dotfiles
  
# Leverages GitHub's dev environment
- Pre-configured workspaces
- VS Code integration
- GitHub Actions integration
```

### Browser-Based Sandbox
```yaml
run_mode: browser
sandbox:
  type: web_assembly
  compile_target: wasm32-wasi
  
# Runs entirely client-side
- No server required
- Privacy-preserving
- Instant startup
```

## Security Tiers

### Tier 1: Trusted (LocalDirectory)
- **Use case:** Personal development, your own code
- **Isolation:** Process-level only
- **Performance:** Maximum
- **Trust:** Full

### Tier 2: Semi-Trusted (Docker)
- **Use case:** Team collaboration, internal tools
- **Isolation:** Container-level
- **Performance:** Good
- **Trust:** Moderate (team members)

### Tier 3: Untrusted (Firecracker/VM)
- **Use case:** Public SaaS, user-submitted code
- **Isolation:** VM-level
- **Performance:** Moderate (VM overhead)
- **Trust:** None (treat as hostile)

### Tier 4: Maximum (Lambda/Ephemeral)
- **Use case:** Complete isolation required
- **Isolation:** Fresh environment per operation
- **Performance:** Varies (cold starts)
- **Trust:** Zero

## Decision Framework

### When to Use Each Sandbox

| Sandbox | Isolation | Startup | Cost | Best For |
|---------|-----------|---------|------|----------|
| LocalDirectory | Low | Instant | Free | Local dev, trusted |
| Docker | Medium | 1-2s | Low | Teams, CI/CD |
| Lambda | High | 100ms-3s | Pay/use | API operations |
| ECS Fargate | Medium | 30-60s | Low/med | Long sessions |
| Kubernetes | Medium | 30-60s | Medium | Scale, orchestration |
| Firecracker | High | 125ms | Low | Untrusted code |
| Wasm | High | Instant | Free | Edge, browser |

### Migration Path

```
Local (now)
  ↓
Docker (easy upgrade)
  ↓
ECS/K8s (production scale)
  ↓
Hybrid (optimization)
  ↓
Specialized (specific needs)
```

## Configuration Examples

### Minimal Local Setup
```yaml
# cognition.yaml in workspace
run_mode: local
```

### Team Docker Setup
```yaml
run_mode: docker
sandbox:
  image: cognition/sandbox:latest
  volumes:
    - .:/workspace
    - /var/run/docker.sock:/var/run/docker.sock
```

### Production AWS Setup
```yaml
run_mode: ecs_fargate
sandbox:
  type: aws_ecs
  cluster: cognition-production
  task_definition: cognition-sandbox
  subnets: [subnet-xxx]
  security_groups: [sg-yyy]
  
storage:
  type: efs
  file_system_id: fs-zzz
  
auto_scaling:
  min_tasks: 1
  max_tasks: 10
  target_cpu: 70
```

### Lambda-Optimized Setup
```yaml
run_mode: hybrid_aws
sandbox:
  primary:
    type: ecs_fargate
    for: [sessions, streaming]
  secondary:
    type: lambda
    for: [files, git, validate]
    functions:
      read_file: arn:aws:lambda:region:account:function:read-file
      write_file: arn:aws:lambda:region:account:function:write-file
      git_status: arn:aws:lambda:region:account:function:git-status
```

## Open Questions

1. **Multi-Sandbox Workflows**
   - Can a session span multiple sandbox types?
   - How to coordinate state between sandboxes?
   - Migration: Can we move a session from Docker to K8s?

2. **Sandbox Composition**
   - Primary sandbox + sidecars?
   - Tool-specific sandboxes (separate container per tool)?
   - Network of sandboxes (microservices approach)?

3. **Intelligent Routing**
   - Auto-detect operation type and route to optimal sandbox?
   - User-specified or automatic?
   - Cost-based routing decisions?

4. **State Management**
   - Shared state layer (Redis, S3) across ephemeral sandboxes?
   - Session migration between sandboxes?
   - Checkpoint/restore for long-running sessions?

5. **Security Boundaries**
   - How to handle secrets across sandbox types?
   - Network policies per sandbox?
   - Audit logging across all types?

## Implementation Considerations

### Priority Order (Suggested)

1. **LocalDirectory** (✅ Implemented)
   - Baseline functionality
   - Development and testing

2. **DockerSandbox**
   - Easy upgrade from local
   - Team sharing
   - CI/CD integration

3. **ECSFargateSandbox**
   - AWS-native production
   - Serverless containers
   - Good balance of features

4. **LambdaSandbox**
   - Cost optimization
   - Stateless operations
   - API-like interactions

5. **KubernetesSandbox**
   - Enterprise scale
   - Multi-cloud portability
   - Complex but powerful

6. **Specialized** (Future)
   - Firecracker (security)
   - Wasm (edge/browser)
   - Hybrid (optimization)

### Technical Challenges

1. **Unified Interface**
   - All sandboxes must implement same interface
   - Async/await patterns
   - Error handling consistency

2. **Performance Monitoring**
   - Track cost per sandbox type
   - Measure latency by operation
   - Optimize routing decisions

3. **State Consistency**
   - Filesystem abstraction
   - Session persistence
   - Tool availability

4. **Debugging Across Types**
   - Unified logging
   - Distributed tracing
   - Error correlation

## Conclusion

The sandbox abstraction is core to Cognition's flexibility. Starting with `LocalDirectorySandbox` provides immediate value, while the architecture supports evolution to cloud-native, serverless, and specialized sandboxes as needs grow.

The AWS-native options (Lambda + ECS Fargate) represent a particularly promising hybrid approach, combining the cost-efficiency of Lambda for short operations with the persistence of ECS for long-running sessions.

This document serves as a reference for future architectural decisions and implementation planning.

---

**Related Documents:**
- Architecture Overview
- Configuration Reference
- Deployment Guide (when implementing cloud options)

**Contributors:** Brainstorming session, 2026-02-12
