# Cognition Lambda MicroVM Runtime Source

These files are zipped and passed to AWS Lambda MicroVM image creation.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the MicroVM application layer, installs baseline tools, creates `/workspace`, and starts the command server. |
| `server.py` | Implements the HTTP command server used by Cognition after AWS launches a MicroVM. |
| `README.md` | Documents the runtime source for builders. It is not used by Cognition at runtime. |

## Runtime Routes

| Route | Method | Purpose |
|---|---|---|
| `/healthz` | `GET` | Readiness check before Cognition sends commands. |
| `/execute` | `POST` | Runs an argv command in `/workspace` and returns stdout, stderr, exit code, timeout state, and duration. |
| `/upload` | `POST` | Writes a file under `/workspace` from `content_base64` or text `content`. |
| `/download` | `GET` or `POST` | Reads a file under `/workspace` and returns `content_base64`. |
| `/aws/lambda-microvms/runtime/v1/*` | `GET` or `POST` | Acknowledges AWS lifecycle hooks. |

## Security Assumptions

- The command server has no application-level auth because requests arrive
  through the AWS Lambda MicroVM proxy with Cognition-managed proxy auth tokens.
- The server rejects file paths that escape `COGNITION_WORKSPACE_ROOT`.
- Command execution is intentionally powerful. The MicroVM boundary, execution
  role, and network connectors are the security boundary.
- Builders should fork this runtime if they need stricter command policy,
  additional auditing, or organization-specific hardening.

## Safe Customization Points

- Add tools in `Dockerfile`.
- Change `COGNITION_MAX_CAPTURE_BYTES` to tune command output size.
- Change `COGNITION_MAX_BODY_BYTES` to tune upload limits.
- Add lifecycle-hook behavior in `server.py` if the image needs per-run setup or
  cleanup.
