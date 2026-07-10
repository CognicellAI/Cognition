# Local Sandbox

The `local` sandbox backend runs commands from the Cognition server process.
It is useful for local development, demos, and trusted environments where
process isolation is not required.

## Behavior

- Commands run as the same operating-system user that runs Cognition.
- File operations use the configured workspace path.
- `.cognition/` is protected from agent write/edit/delete operations by
  Cognition's sandbox wrapper.
- No network or process isolation is provided by this backend.

## When To Use It

Use `local` when:

- you are developing Cognition locally
- all users and tools are trusted
- startup speed matters more than isolation
- you do not need per-session containers or cloud runtime boundaries

Do not use `local` for untrusted multi-tenant command execution.

## Configuration

```yaml
sandbox:
  backend: local
```

Or set:

```bash
COGNITION_SANDBOX_BACKEND=local
```

## Related

- [Sandboxes](../index.md)
- [Security](../../security.md#sandbox-isolation)
- [Configuration](../../../guides/configuration.md#sandbox-execution)
