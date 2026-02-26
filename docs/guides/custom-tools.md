# Guide: Adding Custom Tools

> **DEPRECATED: This guide is for manual tool registration. See [Tool Registry](./tool-registry.md) for automatic discovery.**

## ⚠️ This Guide is Deprecated

The Tool Registry now provides **automatic discovery** and **hot-reloading** for custom tools. 

**Please refer to the [Tool Registry Guide](./tool-registry.md) for current best practices.**

---

## Legacy: Manual Tool Registration

If you need to register tools programmatically (for built-in tools only):

```python
from server.app.agent_registry import get_agent_registry

registry = get_agent_registry()
registry.register_tool(
    name="my_api_client",
    factory=lambda: create_api_client(),
    source="programmatic"
)
```

## Why Automatic Discovery is Better

1. **No Code Changes Required**: Drop files in `.cognition/tools/`
2. **Hot Reload**: Changes picked up immediately
3. **Security Scanning**: Automatic AST scanning
4. **Error Visibility**: Clear error messages via CLI/API
5. **Middleware Support**: Built-in retry, rate limiting, PII redaction

## Quick Reference

- **Create Tool**: `cognition create tool my_tool`
- **List Tools**: `cognition tools list`
- **Reload Tools**: `cognition tools reload`
- **See Errors**: `curl http://localhost:8000/tools/errors`

## Related Documentation

- **[Tool Registry](./tool-registry.md)** - Complete guide for custom tools
- **[Tool Registry Design](../concepts/tool-registry-design.md)** - Architecture documentation
- **[Extending Agents](./extending-agents.md)** - Skills and memory
