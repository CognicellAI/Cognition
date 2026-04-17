# Release Checklist

This checklist defines the standard release process for Cognition patch, minor, and major releases.

Use this document before cutting any Git tag or GitHub release. The goal is to prove that the exact release commit is shippable before publication, especially for multi-arch container images.

## Release Principles

1. Never tag first and validate later.
2. Keep source release, Git tag, and published container images tied to the same commit.
3. Do not mutate an existing published tag to fix packaging mistakes. Cut a new patch release instead.
4. Release-critical fixes must land on `main` before release preparation begins.
5. Prefer a short-lived `release/vX.Y.Z` branch for release-only metadata updates.

## Release Branch

1. Start from `origin/main`, not a stale or divergent local branch.
2. Create a short-lived branch named `release/vX.Y.Z`.
3. Limit branch changes to release-scoped updates:
   - `server/version.py`
   - `CHANGELOG.md`
   - minimal `ROADMAP.md` updates when required
   - other release metadata only when necessary
4. Do not mix feature work, opportunistic refactors, or unrelated dependency churn into the release branch.

## Pre-Release Validation

Run these checks from the exact release branch commit:

```bash
uv run pytest tests/unit -v
uv run ruff check .
uv run mypy .
```

All three must pass before the release PR is merged.

## Container Validation

Before tagging, prove the release commit can build all required container variants.

Required image targets:

1. App image `linux/amd64`
2. App image `linux/arm64`
3. Sandbox image `linux/amd64`
4. Sandbox image `linux/arm64`

At minimum, validate that:

1. `Dockerfile` builds on both target architectures.
2. `Dockerfile.sandbox` builds on both target architectures.
3. App and sandbox image build paths are independent.
4. Multi-arch manifest creation can succeed for app and sandbox images independently.

### Required Pre-Release Workflow

Run `.github/workflows/pre-release-images.yml` from the release branch before tagging.

Use temporary candidate tags such as `0.8.3-rc1` so the exact release commit exercises the real registry push and manifest creation path without mutating final semver tags.

That workflow must complete successfully for:

1. app `linux/amd64`
2. app `linux/arm64`
3. sandbox `linux/amd64`
4. sandbox `linux/arm64`
5. app candidate manifest creation
6. sandbox candidate manifest creation

## Release Workflow Expectations

For a release to be considered healthy, the release workflow must be able to complete these jobs successfully:

1. `test (3.11)`
2. `test (3.12)`
3. `build-amd64-app`
4. `build-amd64-sandbox`
5. `build-arm64-app`
6. `build-arm64-sandbox`
7. `merge-manifests-app`
8. `merge-manifests-sandbox`

If one image family fails, the other should still be able to build independently. That behavior is required for release workflow hygiene.

## Release PR

Open a PR from `release/vX.Y.Z` to `main`.

The PR should include:

1. version bump
2. changelog entry
3. any required roadmap release metadata updates
4. validation output summary

Recommended PR body sections:

1. Summary
2. Validation
3. Release notes or key fixes

## Tagging And Publishing

Only create the Git tag after:

1. the release PR is merged
2. the merge commit is confirmed on `origin/main`
3. validation is green
4. the pre-release image workflow has succeeded for the exact release commit
5. the release commit is the one intended for source and images

Then:

1. create tag `vX.Y.Z`
2. create GitHub release from that exact commit
3. monitor release workflow to completion

## Post-Release Verification

Verify all of the following:

1. GitHub release exists and points at the intended commit
2. release workflow completed successfully
3. app multi-arch manifest was published
4. sandbox multi-arch manifest was published
5. expected semver tags exist for both packages
6. no last-minute divergence exists between release notes and shipped code

## Recovery Rule

If a release fails because of packaging, workflow, or container issues after the tag is published:

1. do not rebuild different code under the same release tag
2. land the fix on `main`
3. prepare a new patch release
4. cut `vX.Y.(Z+1)` from the corrected commit

This keeps source history, release notes, and container artifacts consistent.
