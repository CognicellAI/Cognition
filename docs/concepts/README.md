# Concepts

Deep dives into Cognition's core concepts and architecture.

## Core Primitives

- **[Execution Environments](./execution-environments.md)** - The Cell: Secure, isolated execution contexts for AI actions
- **[Session State](./session-state.md)** - The Thread: Durable, resumable state management
- **[Audit Trails](./audit-trails.md)** - The Trace: Immutable proof of action for compliance
- **[Pluggability](./pluggability.md)** - The Plug: Extending agents with skills and memory

## System Architecture

- **[Architecture Overview](./architecture.md)** - Technical architecture and data flow
- **[Tool Registry Design](./tool-registry-design.md)** - P3-TR implementation: automatic tool discovery and management

## Design Philosophy

Each concept is designed around the **Agent Substrate** philosophy:

1. **Cells** provide isolation without complexity
2. **Threads** provide durability without database headaches
3. **Traces** provide auditability without log parsing
4. **Plugs** provide extensibility without framework lock-in

## Navigation

- New to Cognition? Start with [Execution Environments](./execution-environments.md)
- Building custom tools? See [Tool Registry Design](./tool-registry-design.md)
- Need compliance features? Check [Audit Trails](./audit-trails.md)
- Want to extend agents? Read [Pluggability](./pluggability.md)
