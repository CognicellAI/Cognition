# Backend Routes Configuration Guide

This document describes how to configure custom backend routes for Deep Agents' CompositeBackend in Cognition.

## Overview

The `AGENT_BACKEND_ROUTES` environment variable allows you to specify custom routes for the virtual filesystem. Each route maps a virtual path to a backend storage system (filesystem, store, or state).

## Environment Variable Format

### AGENT_BACKEND_ROUTES

JSON-formatted string defining routes and their backend implementations.

**Type**: JSON object  
**Default**: Uses built-in defaults if not specified

### Format

```json
{
  "/path/": {
    "type": "filesystem|store|state",
    "root": "/absolute/path",
    "virtual_mode": true
  },
  "...more routes..."
}
```

## Backend Types

### 1. `filesystem` - Direct Filesystem Access

Maps virtual paths to host directories. Enables zero-copy semantics when Docker volumes mount the same path.

**Required fields:**
- `type`: `"filesystem"`
- `root`: Absolute path to host directory

**Optional fields:**
- `virtual_mode`: `true` (default) - Maps `/virtual/path/` to `root/`

**Example:**
```json
{
  "/workspace/": {
    "type": "filesystem",
    "root": "/data/workspaces/session-123/repo",
    "virtual_mode": true
  }
}
```

**Use cases:**
- Workspace with Docker volume mounts (zero-copy)
- Local file storage
- Shared filesystem access

### 2. `store` - LangGraph Store

Persistent storage across threads using LangGraph Store.

**Required fields:**
- `type`: `"store"`

**Optional fields:**
- None

**Example:**
```json
{
  "/memories/": {
    "type": "store"
  }
}
```

**Use cases:**
- Persistent memories across session restarts
- Long-term state and summaries
- Cross-session context

### 3. `state` - Ephemeral State

Temporary scratch space stored in agent state. Cleared at session end.

**Required fields:**
- `type`: `"state"`

**Optional fields:**
- None

**Example:**
```json
{
  "/tmp/": {
    "type": "state"
  }
}
```

**Use cases:**
- Temporary files
- Caching
- Scratch work that doesn't need to persist

## Default Configuration

If `AGENT_BACKEND_ROUTES` is not specified, the following defaults are used:

```json
{
  "/workspace/": {
    "type": "filesystem",
    "virtual_mode": true
  },
  "/memories/": {
    "type": "store"
  }
}
```

## Recommended Configuration for Projects

When using **persistent multi-session projects** (new in Cognition), we recommend the **hybrid memory strategy**:

```json
{
  "/workspace/": {
    "type": "filesystem"
  },
  "/memories/hot/": {
    "type": "store"
  },
  "/memories/persistent/": {
    "type": "filesystem"
  },
  "/tmp/": {
    "type": "state"
  }
}
```

**Why this configuration?**

| Path | Backend | Purpose |
|------|---------|---------|
| `/workspace/` | filesystem | Code files (zero-copy with container) |
| `/memories/hot/` | store (RAM) | Fast access to current context, snapshotted every 5 min |
| `/memories/persistent/` | filesystem | Long-term memory that survives server restarts |
| `/tmp/` | state (RAM) | Temporary scratch space, fastest but ephemeral |

**Benefits:**
- ⚡ Hot memories are fast (0.001ms RAM access)
- 💾 Persistent memories survive everything
- 🔄 Automatic snapshots every 5 minutes + on disconnect
- 🧠 Agent can access both fast and persistent memories

## Examples

### Example 1: Hybrid Memory Strategy (Recommended for Projects)

Best for persistent multi-session projects with automatic memory management:

```bash
export AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem"},
  "/memories/hot/": {"type": "store"},
  "/memories/persistent/": {"type": "filesystem"},
  "/tmp/": {"type": "state"}
}'
```

**Use with:**
- `MEMORY_SNAPSHOT_ENABLED=true`
- `MEMORY_SNAPSHOT_INTERVAL=300` (5 minutes)

### Example 2: Basic Configuration (Default)

Simple setup without project persistence:

```bash
export AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem", "virtual_mode": true},
  "/memories/": {"type": "store"}
}'
```

### Example 3: Multiple Directories

```bash
export AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem", "root": "/data/workspace", "virtual_mode": true},
  "/data/": {"type": "filesystem", "root": "/data/files"},
  "/cache/": {"type": "filesystem", "root": "/tmp/cache"},
  "/memories/": {"type": "store"},
  "/tmp/": {"type": "state"}
}'
```

### Example 4: S3-Ready Configuration

```bash
# Future: When S3Backend is implemented
export AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem", "root": "/local/workspace"},
  "/archive/": {"type": "s3", "bucket": "my-bucket", "prefix": "archive/"},
  "/memories/": {"type": "store"}
}'
```

### Example 4: Development vs Production

**Development** (`.env`):
```
AGENT_BACKEND_ROUTES={""/workspace/"": {"type": "filesystem"}, ""/memories/"": {"type": "store"}}
```

**Production** (with persistence):
```
AGENT_BACKEND_ROUTES={
  "/workspace/": {"type": "filesystem", "root": "/mnt/persistent/workspace"},
  "/archive/": {"type": "filesystem", "root": "/mnt/archive"},
  "/memories/": {"type": "store"}
}
```

## Configuration in .env File

You can specify routes in `.env` (properly escaped):

```bash
# .env
AGENT_BACKEND_ROUTES='{"'"/workspace/'"":{""type"":""filesystem""},""'"/memories/'"":{""type"":""store""}}'
```

Or more readably in a JSON file and reference it:

```bash
# config/backends.json
{
  "/workspace/": {"type": "filesystem", "virtual_mode": true},
  "/memories/": {"type": "store"}
}

# .env
AGENT_BACKEND_ROUTES=$(cat config/backends.json | tr '\n' ' ')
```

## Performance Characteristics

| Route Type | Latency | Persistence | Best For |
|-----------|---------|-------------|----------|
| `filesystem` | 1-2ms | ✅ Persistent | Working files, code |
| `store` | 10-50ms | ✅ Persistent | Memories, state |
| `state` | <1ms | ❌ Session only | Temporary cache |

## Implementation Details

### Path Resolution

1. Agent makes tool call: `read_file("/workspace/main.py")`
2. CompositeBackend routes to matching path prefix: `/workspace/`
3. FilesystemBackend resolves to: `<root>/main.py`
4. File is read from host filesystem

### Docker Volume Mounting

When using `filesystem` backend with Docker volumes:

```python
volumes={
    workspace_path: {
        "bind": "/workspace/repo",
        "mode": "rw",
    }
}
```

The agent's `/workspace/` maps to host filesystem via FilesystemBackend, while the container's `/workspace/` mounts the same host path via Docker volume. Both see the same files instantly (zero-copy).

### Persistence

- **filesystem**: Data persists on host disk
- **store**: Data persists in LangGraph Store (in-memory by default, can be configured for database persistence)
- **state**: Data lives in agent state, cleared when session ends

## Future Extensions

CompositeBackend supports adding new backend types:

```json
{
  "/s3/": {"type": "s3", "bucket": "my-bucket"},
  "/db/": {"type": "postgres", "table": "documents"},
  "/redis/": {"type": "redis", "ttl": 3600},
  "/http/": {"type": "http", "base_url": "https://api.example.com"}
}
```

## Troubleshooting

### Configuration Not Loaded

**Symptom**: Agent uses default routes instead of custom routes

**Solution**:
1. Verify `AGENT_BACKEND_ROUTES` env var is set: `echo $AGENT_BACKEND_ROUTES`
2. Check JSON syntax with `jq`: `echo "$AGENT_BACKEND_ROUTES" | jq`
3. Check logs for parsing errors

### Path Not Found

**Symptom**: Agent can't access files at a configured path

**Solution**:
1. Verify path exists on host: `ls /your/configured/path`
2. Check permissions: `ls -la /your/configured/path`
3. Verify path in JSON matches exactly (case-sensitive)

### Slow Filesystem Access

**Symptom**: Filesystem operations are slow (>50ms)

**Solutions**:
1. Use `filesystem` backend for frequently accessed paths
2. Move infrequently accessed files to slower backends
3. Consider mount options for Docker volumes (e.g., `cached` mode on macOS)

## Configuration Examples by Use Case

### Local Development

```json
{
  "/workspace/": {"type": "filesystem"},
  "/memories/": {"type": "store"}
}
```

### Production with Persistence

```json
{
  "/workspace/": {"type": "filesystem", "root": "/mnt/persistent/workspace"},
  "/memories/": {"type": "store"},
  "/archive/": {"type": "filesystem", "root": "/mnt/archive"}
}
```

### Multi-Session Setup

```json
{
  "/workspace/": {"type": "filesystem", "root": "/data/session-{id}/repo"},
  "/memories/": {"type": "store"},
  "/shared/": {"type": "filesystem", "root": "/shared/data"}
}
```

### Testing Environment

```json
{
  "/workspace/": {"type": "state"},
  "/memories/": {"type": "state"},
  "/tmp/": {"type": "state"}
}
```

## Best Practices

1. **Use `filesystem` with Docker volumes** for workspace - enables zero-copy
2. **Use `store` for memories** - persists important state
3. **Keep working files on fast paths** - filesystem for frequently accessed files
4. **Archive cold data** - use separate filesystem paths for historical data
5. **Test configuration** - validate JSON before deploying
6. **Monitor performance** - measure latency for different route types
7. **Document custom routes** - keep configuration in version control

---

For more information, see `ARCHITECTURE_DEEP_AGENTS_FS.md` for the complete architecture documentation.
