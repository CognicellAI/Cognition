# Examples

This directory contains reference configurations for Cognition.

These examples are intentionally split by purpose:

- `exhaustive-config/`: reference-complete example showing nearly every major configuration surface, including file-based `.cognition` config and sample API payloads for DB-backed config entities.
- `minimal-config/`: smallest practical starting point.
- `bedrock-config/`: AWS/Bedrock-oriented example.
- `scoped-multi-tenant/`: scoping and multi-tenant configuration example.

Use `exhaustive-config/` as documentation, not as a drop-in starter. It shows many options at once so you can discover the full shape of the system.

## Configuration Surfaces

Cognition configuration is split across three places:

1. `.cognition/config.yaml`
   - file-based project config
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
   - global defaults

The exhaustive example includes all three so the full configuration model is visible in one place.
