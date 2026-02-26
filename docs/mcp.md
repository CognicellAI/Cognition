# MCP (Model Context Protocol) Support

Cognition supports the Model Context Protocol (MCP) for connecting to external tools and services. However, **Cognition takes a security-first approach** that differs from some other implementations.

## Security Stance

### What Cognition Supports: Remote MCP Only

Cognition **only supports Remote (HTTP/SSE) MCP servers**.

This means:
- **✅ Allowed**: Connecting to hosted MCP gateways (Glama, LiteLLM Proxy, etc.)
- **✅ Allowed**: Connecting to your own MCP servers running as web services
- **❌ Not Allowed**: Local (stdio) MCP servers that spawn subprocesses

### Why?

**Clear Separation of Concerns:**

| Capability | Use Native Tools | Use MCP |
|------------|------------------|---------|
| Local execution (shell, files) | ✅ Built-in tools | ❌ Not supported |
| Remote information (GitHub, Jira) | ❌ Limited | ✅ MCP servers |

**Security:**
- **Native Tools**: Handle execution on your system with proper sandboxing
- **MCP Tools**: Only access external information via HTTP APIs

This ensures:
1. **No subprocess attacks**: MCP servers can't spawn processes on your machine
2. **No environment leakage**: Your AWS keys, SSH keys, etc. never leak to MCP servers
3. **Clear boundaries**: You always know when code is running locally vs. remotely

## Configuration

### Global Configuration (in `.cognition/config.yaml`)

For infrastructure tools used across all sessions:

```yaml
mcp:
  github_gateway:
    name: "github"
    url: "https://api.glama.ai/mcp/github"
    headers:
      Authorization: "Bearer ${GLAMA_API_KEY}"
    enabled: true
```

### Session-Level Configuration

For app-specific MCP servers, pass them when creating a session:

```json
POST /sessions
{
  "agent_name": "default",
  "mcp_servers": [
    {
      "name": "user-jira",
      "url": "https://jira.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${JIRA_TOKEN}"
      }
    }
  ]
}
```

## If You Need Local MCP

If your application requires local MCP server support, you have two options:

### Option 1: Run MCP as a Web Service

Run the MCP server yourself as a local HTTP service:

```bash
# Terminal 1: Run the MCP server
npx -y @modelcontextprotocol/server-filesystem /path/to/files &
```

Then connect via HTTP:

```yaml
mcp:
  filesystem:
    name: "local-fs"
    url: "http://localhost:3000/mcp"
```

### Option 2: Build Into Your App

If you're building an application on top of Cognition, implement MCP support directly in your app layer:

```python
# Your app spawns the MCP server
# Your app handles the stdio communication
# Your app calls Cognition's API with pre-processed context
```

## Tool Selection

When both native tools and MCP tools are available, Cognition automatically selects the most appropriate:

- **Local file operations**: Uses native `read_file`, `write_file`, etc. (sandboxed)
- **GitHub operations**: Uses MCP tools from configured GitHub gateway
- **Web search**: Uses native `websearch` (no external dependencies)

## Security Checklist

Before using MCP in production:

- [ ] Only HTTPS URLs (never HTTP in production)
- [ ] Authentication headers configured via environment variables
- [ ] MCP servers hosted on trusted infrastructure
- [ ] Rate limiting on MCP endpoints
- [ ] Audit logging of MCP tool calls

## Examples

### Connecting to Glama

```yaml
mcp:
  github:
    name: "github"
    url: "https://api.glama.ai/mcp/github"
    headers:
      Authorization: "Bearer ${GLAMA_API_KEY}"
```

### Connecting to Context7

```yaml
mcp:
  context7:
    name: "docs"
    url: "https://mcp.context7.com/mcp"
    headers:
      CONTEXT7_API_KEY: "${CONTEXT7_API_KEY}"
```

### Connecting to Custom Server

```yaml
mcp:
  my_company_api:
    name: "internal-api"
    url: "https://internal.company.com/mcp"
    headers:
      X-API-Key: "${INTERNAL_API_KEY}"
```

## Troubleshooting

### "Local MCP servers are not supported"

If you see this error, you're trying to use a local (stdio) MCP server. Cognition requires HTTP/SSE endpoints.

**Solution**: Run the MCP server as a web service, or use Cognition's built-in tools for local operations.

### Connection timeouts

MCP servers have a default 5-second timeout. Increase this in your config:

```yaml
mcp:
  slow_server:
    name: "slow"
    url: "https://slow.example.com/mcp"
    timeout: 30000  # 30 seconds
```

### Authentication failures

Make sure to use environment variable references:

```yaml
# ✅ Good
headers:
  Authorization: "Bearer ${API_KEY}"

# ❌ Bad - never hardcode secrets
headers:
  Authorization: "Bearer actual-secret-key"
```

## Comparison with Other Tools

| Feature | Cognition | OpenCode | Claude Desktop |
|---------|-----------|----------|----------------|
| Remote MCP | ✅ Yes | ✅ Yes | ✅ Yes |
| Local MCP | ❌ No | ✅ Yes | ✅ Yes |
| Security stance | Remote-only | Hybrid | Hybrid |
| Use case | Production | Development | Personal |

**Cognition is optimized for production deployments** where security and predictability are paramount.

## Further Reading

- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Security Design Document](../design/mcp-security-design.md)
