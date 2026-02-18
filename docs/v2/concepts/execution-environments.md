# Execution Environments ("The Cell")

> **The Cell is the secure boundary where "Thought" becomes "Action".**

In an AI platform, the most dangerous moment is when the Agent decides to execute code. Whether it's running a Python script to analyze data or executing a shell command to configure infrastructure, this action must be contained.

Cognition leverages the advanced sandboxing capabilities of **LangGraph Deep Agents** to provide pluggable, secure, and performant execution environments.

## The Philosophy: Bring Code to Data

Traditional architectures move data to the processing engine (ETL). Cognition flips this: it deploys an ephemeral execution environment (**The Cell**) right next to the data, executes the agent's logic, and returns only the insight. This minimizes data movement and maximizes security.

## The Hybrid Backend

Cognition uses a hybrid approach for its default execution backend:

1.  **Native File Operations**: For speed and reliability, file operations (`read`, `write`, `edit`) use native Python I/O calls. This ensures that large files can be handled efficiently without the overhead of shell execution.
2.  **Isolated Shell Execution**: Commands that require a shell (like `pytest`, `git`, or `npm install`) are executed via a dedicated `LocalSandbox` that manages subprocesses, captures output, and enforces timeouts.

## Cognition-Owned ExecutionBackend Protocol

Cognition defines its own `ExecutionBackend` protocol, decoupled from Deep Agents internals. This allows seamless backend swapping without modifying agent logic.

```python
class ExecutionBackend(Protocol):
    async def execute(self, command: list[str], timeout: float) -> ExecuteResult: ...
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def list_files(self, path: str) -> list[FileInfo]: ...
```

Adapters translate Cognition's `ExecutionBackend` to Deep Agents' `SandboxBackendProtocol`, ensuring compatibility while maintaining flexibility.

## Security Tiers

Cognition supports multiple isolation tiers, allowing you to scale from a laptop to an enterprise cluster while maintaining a consistent agent interface.

### Tier 1: Local Cell (Development)
**Backend:** `LocalExecutionBackend`

The "Soft" Sandbox. Ideal for rapid prototyping and local development loops.

*   **Mechanism:** Runs commands as subprocesses on the host machine using argument lists (no shell=True).
*   **Isolation:** Logical containment. Restricted to the Current Working Directory (CWD).
*   **Security:** Commands parsed with `shlex.split()` to prevent shell injection.
*   **Best For:** Building the platform, local script execution, and trusted environments.

### Tier 2: Docker Cell (Production / Single-Node)
**Backend:** `DockerExecutionBackend`

The "Hard" Sandbox. Ideal for production deployments on a single server or VM.

*   **Mechanism:** Spins up a fresh Docker container for each Session.
*   **Isolation:** Kernel-level namespaces and cgroups. The Agent cannot see the host filesystem or processes.
*   **Features:**
    - Network isolation (configurable)
    - Resource limits (CPU, memory, disk)
    - Volume mounting for workspace persistence
    - Container lifecycle management (create/destroy per session)
*   **Best For:** Internal tools, CI/CD integrations, and semi-trusted code.

### Tier 3: Cloud-Native Cells (Scale)
**Backends:** `LambdaExecutionBackend`, `K8sExecutionBackend`

For platforms managing thousands of concurrent agents or requiring specialized hardware (like GPUs).

*   **AWS Lambda**: Executes stateless tasks in a fresh, short-lived micro-VM. Perfect for cost-optimized, high-burst workloads.
*   **Kubernetes (K8s)**: Schedules a dedicated Pod per session. Provides full cluster-level isolation and infinite horizontal scaling.
*   **Configuration:** `sandbox_backend = "local" | "docker" | "lambda" | "k8s"`

### Custom Integration
Cognition's execution layer is fully extensible. Implement the `ExecutionBackend` protocol to integrate with any execution provider (Firecracker MicroVMs, WASM runtimes, or proprietary internal clouds). The adapter layer handles Deep Agents compatibility automatically.

## Security Primitives

Every Cell, regardless of tier, is governed by three core security primitives:

### 1. Path Resolution & Containment
All file operations are resolved against a strict `root_dir`. Cognition automatically strips leading slashes and validates that resolved paths never escape the sandbox boundary (Path Traversal protection).

### 2. Ephemeral Lifecycle
Cells are created for a specific Session and destroyed immediately after (or kept warm according to your policy). This ensures no state leaks between different user sessions or agent tasks.

### 3. Output Control & Timeouts
To prevent Denial of Service (DoS) or resource exhaustion:
- **Output Truncation**: Tool outputs are truncated (default 100KB) to prevent memory crashes.
- **Execution Timeouts**: Every shell command has a configurable timeout (default 5 minutes).

## Choosing the Right Cell

| Use Case | Recommended Backend | Why? |
| :--- | :--- | :--- |
| **Building the Platform** | Local | Debugging is easy; file access is instant. |
| **Internal Ops Tools** | Docker | Good balance of security and simplicity. |
| **Customer-Facing SaaS** | Kubernetes | Strict tenant isolation and auto-scaling. |
| **High-Volume Stateless Tasks** | Lambda | Pay-per-use and zero infrastructure management. |
| **Malware/Untrusted Analysis** | Docker (No Net) | Absolute network containment required. |
