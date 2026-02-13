# Execution Environments ("The Cell")

> **The Cell is the secure boundary where "Thought" becomes "Action".**

In an AI platform, the most dangerous moment is when the Agent decides to execute code. Whether it's running a Python script to analyze data or executing a shell command to configure infrastructure, this action must be contained.

Cognition solves this with **Pluggable Execution Backends**.

## The Philosophy: Bring Code to Data

Traditional architectures move data to the processing engine (ETL). Cognition flips this: it deploys an ephemeral execution environment (The Cell) right next to the data, executes the agent's logic, and returns only the insight.

## Backend Types

Cognition supports three types of Cells, allowing you to scale from a laptop to a Kubernetes cluster without changing your application logic.

### 1. Local Cell (Development)
**Backend:** `CognitionLocalSandboxBackend`

The "Soft" Sandbox. Ideal for rapid prototyping and development loops.

*   **Mechanism:** Runs commands as subprocesses on the host machine.
*   **Isolation:** Logical only. Restricted by Current Working Directory (CWD).
*   **Pros:** Zero overhead, instant startup, full access to local tools.
*   **Cons:** Not secure for untrusted code (Agent has user permissions).

```python
# config.yaml
backend: local
workspace: ./my-project
```

### 2. Docker Cell (Production / Single-Node)
**Backend:** `CognitionDockerSandboxBackend` (Future)

The "Hard" Sandbox. Ideal for production deployments on a single server or VM.

*   **Mechanism:** Spins up a fresh Docker container for each Session.
*   **Isolation:** Kernel-level namespaces (cgroups). The Agent cannot see the host.
*   **Data Access:** Volumes are mounted read-only or read-write as needed.
*   **Pros:** Secure execution of untrusted code. Reproducible environments.

```yaml
# config.yaml
backend: docker
image: cognition-agent:latest
volumes:
  - /mnt/data:/workspace:ro
```

### 3. Kubernetes Cell (Enterprise / Scale)
**Backend:** `CognitionK8sSandboxBackend` (Future)

The "Elastic" Sandbox. Ideal for high-scale platforms managing thousands of concurrent agents.

*   **Mechanism:** Schedules a Pod for each Session.
*   **Isolation:** Full cluster-level isolation with Network Policies.
*   **Pros:** Infinite horizontal scaling. Can run heavy workloads (GPUs).

## The Interface

Your platform interacts with all Cells through a unified Protocol. You never worry about *how* the code runs, only *that* it runs.

```python
# The Agent Logic (Same code for Local, Docker, or K8s)
agent.execute("python analyze_data.py")
```

## Security Model

The Cell provides the following security guarantees:

1.  **Ephemeral Lifecycle:** Cells are created for a Session and destroyed immediately after (or kept warm for performance). No state leaks between sessions.
2.  **Resource Quotas:** (Docker/K8s only) Limits on CPU, Memory, and Network prevent runaway agents from DOSing the platform.
3.  **Network Air-Gapping:** (Configuration) You can disable internet access for the Cell, ensuring no data exfiltration is possible.

## Choosing the Right Cell

| Use Case | Recommended Backend | Why? |
| :--- | :--- | :--- |
| **Building the Platform** | Local | Debugging is easy; inspecting files is instant. |
| **Internal Tools** | Docker | Good balance of security and simplicity. |
| **Customer-Facing SaaS** | Kubernetes | Strict isolation required between tenants. |
| **Malware Analysis** | Docker (No Net) | Absolute containment required. |
