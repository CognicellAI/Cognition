# Exhaustive Config Example

This example is a reference-complete Cognition project layout intended to show the major configuration surfaces in one place.

It is not meant to be copied unchanged into production. Instead, use it to answer questions like:

- What can go in `.cognition/config.yaml`?
- Which settings belong in `.env` instead?
- What does a file-based agent look like?
- How are skills, tools, and middleware laid out?
- Which config entities are API-managed instead of file-managed?

## Included Sections

- `.cognition/config.yaml`
  - project-level file config
  - agent defaults
  - provider bootstrap config
  - infrastructure settings that can live in YAML

- `.env.example`
  - all important infrastructure and credential environment variables from `server.app.settings.Settings`

- `.cognition/agents/`
  - YAML and Markdown agent examples

- `.cognition/skills/`
  - a sample skill with real structure

- `.cognition/tools/`
  - a file-based tool example

- `.cognition/middleware/`
  - a custom middleware scaffold

- `api/`
  - sample request payloads for API-managed config entities

## Precedence Model

Configuration precedence is:

1. built-in defaults
2. `~/.cognition/config.yaml`
3. project `.cognition/config.yaml`
4. environment variables
5. API-managed registry/config-store entities where applicable

## Important Note

Not everything in Cognition should be configured through files anymore.

File configuration is best for:
- project defaults
- checked-in examples
- initial provider bootstrap
- file-based agents/skills/tools/middleware

API-managed configuration is best for:
- user- or project-scoped provider configs
- dynamic tools and skills
- global provider and agent defaults
- programmatic builder/UIs
