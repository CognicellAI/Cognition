# Examples

This directory contains reference configurations for Cognition.

These examples are intentionally split by purpose:

- `exhaustive-config/`: reference-complete example showing nearly every major configuration surface, including file-based `.cognition` config and sample API payloads for DB-backed config entities.
- `minimal-config/`: smallest practical starting point.
- `bedrock-config/`: AWS/Bedrock-oriented example.
- `aws-lambda-microvm-sandbox/`: AWS Lambda MicroVM sandbox prerequisites,
  Cognition profile config, and reusable Terraform for consuming a prebuilt
  MicroVM image ARN.
- `aws-lambda-microvm-default-runtime/`: commented default runtime source and
  Terraform for creating a builder-owned Lambda MicroVM image ARN.
- `scoped-multi-tenant/`: scoping and multi-tenant configuration example.
- `a2a-exposed-agent/`: A2A protocol exposure for agent-to-agent interoperability.

Use `exhaustive-config/` as documentation, not as a drop-in starter. It shows many options at once so you can discover the full shape of the system.

## Configuration Surfaces

Cognition configuration is split across three places:

1. `.cognition/config.yaml`
   - file-based project config (providers, agents, tools, skills, MCP servers)
   - best for stable project defaults

2. Environment variables / `.env`
   - infrastructure settings
   - provider credentials
   - deployment/runtime settings

3. API-managed config
   - providers
   - agent definitions
   - tools
   - skills
   - MCP servers (`/mcp-servers`)
   - global defaults

The exhaustive example includes all three so the full configuration model is visible in one place.
