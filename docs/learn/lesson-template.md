# Lesson template

Use this template for every lesson in the Learn section. It keeps lessons
separate from reference material and gives each page the same teaching rhythm:
concept first, design judgment second, implementation third.

```markdown
# Lesson Title

Audience:
Prerequisites:
Estimated time:
Surface:
Stability:

## What you will learn

## Why it matters

## Concept

## Design judgment

## Cognition implementation

## Walkthrough

## Verify

## Final checkpoint

## Common mistakes

## Related reference
```

## Field guidance

| Field | Purpose |
|---|---|
| Audience | Name the learner: beginner, builder, product developer, platform engineer, or maintainer |
| Prerequisites | List knowledge or completed lessons, not vague experience levels |
| Estimated time | Keep lessons scoped enough to finish in one sitting |
| Surface | Use `Foundations`, `Core`, `Production`, or `Labs` |
| Stability | Use `Stable`, `Advanced`, or `Experimental` |

## Writing rules

- Title lessons around agent-development problems, not product features.
- Teach one outcome per lesson.
- Start with the concept before showing the Cognition implementation.
- Link to reference docs for API and architecture details.
- Use runnable checks for Core, Production, and Labs when possible.
- Keep Foundations exercises short and conceptual.
- End Production lessons with a small portfolio checkpoint.
- Label experimental behavior clearly.

## Verification rules

Lessons should be verifiable when the subject allows it:

- Docs pages must pass strict MkDocs builds.
- Python/API lab code should be lintable.
- JSON fixtures should parse.
- Live Cognition checks should skip gracefully when provider credentials are not
  configured.
