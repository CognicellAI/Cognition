# Minimal Config Example

This is the smallest practical project-level Cognition example.

Use this when you want a simple starting point instead of the exhaustive reference example.

## What it demonstrates

- one provider bootstrapped from `.cognition/config.yaml`
- secrets supplied through environment variables, not YAML
- the recommended path for a local project default

## Recommended runtime usage

After startup, Cognition stores the effective provider config in the ConfigRegistry.

For session binding:

1. prefer `provider_id`
2. use `provider` + `model` only for direct overrides
3. avoid `model` alone unless it maps to exactly one enabled provider type
