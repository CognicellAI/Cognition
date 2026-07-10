# Lambda MicroVM Runtime Command Server

The runtime command server is the HTTP service inside the Lambda MicroVM image.
Cognition uses it to perform Deep Agents sandbox operations after AWS launches
the MicroVM and issues a proxy auth token.

## Required Routes

| Route | Purpose |
|---|---|
| `GET /healthz` | Confirm the runtime is ready before command execution |
| `POST /execute` | Run a command; Cognition sends `["sh", "-c", command]` |
| `POST /upload` | Upload workspace files into the runtime |
| `GET` or `POST /download` | Download workspace files from the runtime |
| AWS lifecycle hooks | Optional ready, validate, run, resume, suspend, terminate hooks |

The server should listen on the `SandboxProfile.port`, usually `8080`.

Builders who need a ready starting point can use the
[default runtime image](./default-runtime-image.md) example. Its zipped runtime
source includes a commented `Dockerfile`, `server.py`, and runtime `README.md`.

## Auth Boundary

The command server does not need its own application-level public auth when it
is reachable only through the Lambda MicroVM proxy. Cognition requests AWS proxy
auth tokens at runtime, keeps them in memory, and sends them as AWS proxy
headers.

Auth tokens are never persisted and must not appear in logs, SSE events, or
runtime metadata.

## Image Builder Responsibilities

The builder owns:

- packaging the command server into the MicroVM image
- installing language runtimes and CLI tools required by agents
- exposing a writable workspace path, commonly `/workspace`
- keeping image dependencies patched
- choosing whether runtime logs go to CloudWatch
- validating that `/healthz`, `/execute`, `/upload`, and `/download` match
  Cognition's expected protocol

Cognition owns consuming the image ARN and mapping profile/agent config into
MicroVM launch requests.

## Command Semantics

Cognition sends shell commands through the sandbox protocol. Runtime
implementations should return structured stdout, stderr, exit code, and timeout
information. File upload/download routes should preserve file contents exactly
and reject path traversal outside the workspace.

## Related

- [Setup](./setup.md)
- [Default Runtime Image](./default-runtime-image.md)
- [IAM and Networking](./iam-and-networking.md)
- [Lifecycle and Observability](./lifecycle-and-observability.md)
