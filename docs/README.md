# Cognition: The Agent Substrate

> **The foundational layer for trusted, executable AI platforms.**

## The Platform Paradox

We are in the midst of a platform shift. Every industry—from Cyber Security to FinTech to BioTech—is rushing to build "AI Agents" into their workflows.

But building a production-grade Agent Platform requires solving three incredibly hard infrastructure problems that have nothing to do with the AI model itself:

1.  **Isolation (Execution):** How do I let an AI run code or tools safely without destroying my infrastructure?
2.  **State (Persistence):** How do I ensure an investigation or workflow survives server restarts and lasts for weeks?
3.  **Trust (Auditability):** How do I prove to a regulator or legal team *exactly* what data the AI accessed and what logic it used?

Most teams waste 12-18 months building this scaffolding before they write their first line of domain logic.

**Cognition is that scaffolding.** It is the **Agent Substrate**: a hardened, pre-built runtime engine that handles Execution, State, and Trust so you can focus on building your platform.

## Core Primitives

Cognition provides three fundamental primitives that you compose to build your platform:

### 1. The Cell (Execution Environment)
The Cell is the secure boundary where "Thought" becomes "Action".

- **Concept:** Bring the Code to the Data.
- **Capabilities:**
    - **Local Cells:** Lightweight process isolation for rapid development loops.
    - **Docker Cells:** Hardened container environments for semi-trusted code.
    - **Cloud-Native Cells:** AWS Lambda and Kubernetes for infinite scale.
- **Powered By:** Built on the industry-leading **LangGraph Deep Agents** sandboxing protocol.
- **Benefit:** Your platform logic remains clean. You simply request a tool execution, and the Substrate handles the containment, cleanup, and resource limits.

### 2. The Thread (State Management)
The Thread is the continuous, resilient memory of a workflow.

- **Concept:** Durable, resumable state.
- **Capabilities:**
    - **Pluggable Backends:** SQLite for local/edge deployments; PostgreSQL for cloud scale.
    - **Checkpointing:** Every step the AI takes is saved. If your server crashes, the Agent picks up *exactly* where it left off.
- **Benefit:** Enables long-running "Cases" or "Projects" that span days or weeks, rather than transient "Chats."

### 3. The Trace (Audit Trail)
The Trace is the immutable proof of action.

- **Concept:** Trust through verification.
- **Capabilities:**
    - **OTLP Integration:** Native OpenTelemetry support allows you to pipe traces to Jaeger, Splunk, or Datadog.
    - **Chain of Events:** See exactly what file was read, what API was called, and what reasoning the AI used to make a decision.
- **Benefit:** Essential for platforms operating in regulated environments (Security, Legal, Healthcare).

### 4. The Plug (Extensibility)
The Plug is the mechanism for domain-specific specialization.

- **Concept:** Convention over Configuration.
- **Capabilities:**
    - **Skills & Memory:** Inject project-specific rules and reusable workflows via simple files.
    - **Subagents:** Orchestrate specialized experts for complex tasks.
- **Benefit:** Allows you to build deep domain expertise into your platform without modifying the substrate.

## Architecture

Cognition is designed to be the "Headless Backend" for your platform. You build the specialized UI/UX; we provide the engine.

```mermaid
graph TD
    subgraph "Your Platform (The Application)"
        UI[Custom React/Vue Dashboard]
        API_GW[Your API Gateway]
    end

    subgraph "The Substrate (Cognition Engine)"
        API[REST API / SSE Stream]
        
        subgraph "Trust Layer"
            Audit[OTLP Tracer]
            State[Persistence - SQLite/Postgres]
        end
        
        subgraph "Execution Layer"
            Router[Agent Router]
            
            subgraph "The Cell (Sandbox)"
                Tool_A[File System]
                Tool_B[Shell / Python]
                Tool_C[Custom Tools]
            end
        end
    end

    UI --> API_GW
    API_GW --> API
    API --> Router
    Router --> State
    Router --> Tool_A
    Router --> Tool_B
    Tool_B -.-> Audit
```

## Use Cases

Cognition is domain-agnostic. It powers platforms such as:

*   **[GeneSmith (BioTech):](./blueprints/genesmith.md)** A biological foundry for designing and simulating proteins in a secure, audited environment.
*   **[StarKeep (SpaceOps):](./blueprints/starkeep.md)** An orbital administrator for autonomous satellite repair and edge computing.
*   **[ZeroOne (DeFi):](./blueprints/zeroone.md)** An algorithmic CEO for managing decentralized capital with transparent logic.
*   **[BreachLens (Security):](./blueprints/cyber-investigation.md)** A security investigation platform for safely analyzing malware and breach logs.
*   **[DataLens (Analytics):](./blueprints/data-analyst.md)** A headless data scientist for securely analyzing and visualizing business data.

## Getting Started

1.  **[Quick Start](./guides/quickstart.md):** Get up and running in 5 minutes.
2.  **[Core Concepts](./concepts/execution-environments.md):** Understand the primitives (Cell, Thread, Trace, Plug) in detail.
3.  **[Technical Architecture](./concepts/architecture.md):** Deep dive into the engine's internal design and data flow.
4.  **[Extending Agents](./guides/extending-agents.md):** Learn how to customize agent behavior with Skills, Memory, and Multi-Agent support.
5.  **[Tool Registry](./guides/tool-registry.md):** Create custom tools with automatic discovery and hot-reloading.
6.  **[Blueprints](./blueprints/cyber-investigation.md):** See a reference architecture for a Cyber Security Investigation Platform.
7.  **[Build Guide](./guides/building-platforms.md):** Learn how to integrate the Cognition API into your application.

## What's New

- **Agent Switching:** Dynamically change the active agent for a session via the API (`PATCH /sessions/{id}`)
- **Multi-Agent Registry:** Define specialized agents with different capabilities and switch between them
- **Pluggable Storage:** Choose between SQLite (local) or PostgreSQL (production) backends
