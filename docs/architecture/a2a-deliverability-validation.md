# A2A deliverability validation record

**Status:** Historical investigation evidence, September 7–8, 2026.

**Audience:** Cognition maintainers, builders and operators.

**Related:** [Architecture whitepaper](s3-files-a2a-deliverability.md),
[retention guide](../guides/published-file-retention.md),
[sandbox lifecycle](../concepts/sandboxes/aws-lambda-microvm/lifecycle-and-observability.md).

This record preserves the results supporting the publication, observability and
sandbox ownership changes. It does not certify a production deployment or imply
that these experiments were rerun during release-tree cleanup. The complete lab,
its infrastructure and bulk observations are archived outside the release tree.

## Live-model performance

Bedrock Claude Sonnet 4.6 executed natural-language report requests through
Cognition, Lambda MicroVM tools and S3 publication. Each cohort contained ten
requests in each run, with observed request overlap matching concurrency 1, 5
and 10. All sixty requests succeeded. Natural reports produced validated `data`
and small file `raw` Parts; this workload did not require all four Part kinds.

| Concurrency | Before completion p50 | After completion p50 | Before maximum | After maximum |
|---|---:|---:|---:|---:|
| 1 | 21.7 s | 21.3 s | 28.0 s | 26.6 s |
| 5 | 31.9 s | 26.3 s | 38.8 s | 33.7 s |
| 10 | 40.2 s | 27.1 s | 63.2 s | 46.1 s |

The baseline revision was `9633e64164aa6ba34a26ca946d905e9c69c940b6`.
The repeat used `3ba1053652324f877380ce882fcae6c515c3a899`, which moves blocking
S3 artifact operations off the event loop and reuses a bounded client transport.
Its Cognition image was
`sha256:90c57869cc95418c9cd77c0d43ec379885ac63be73c6c01aa1d3cce17e027383`.
Both runs used the 256 KiB inline threshold and 10 MiB publication maximum.

Completion is client-observed through streaming and terminal retrieval. These
were sequential live-model samples, not randomized paired trials. Provider
variability, content and cache effects remain confounders. Stage intervals overlap
and must not be summed. With ten samples, the empirical nearest-rank p95 equals
the maximum. These results establish neither guaranteed speedup nor capacity at
100–1,000 agents.

The S3 change passed 106 relevant regressions, Ruff and a 95-file server mypy
check. All thirty repeat execution traces had one semantic run root and no
missing manual-span parents. Following confirmed VM termination and Cognition
restart, 21 representative cohorts retained artifact identities, filenames,
media types and exact bytes; eleven URLs refreshed and 21 wrong-scope requests
were rejected. WayPost retrieved four existing demonstration tasks again.

## Publication and workspace evidence

The original publication implementation (`19e2895`) demonstrated `data`, `raw`
and `url` in one live report task and genuine model `text` in a follow-up task.
This proves protocol delivery across runs, not universal model behavior or all
four variants in every response. Mixed input history and streamed/terminal
artifact identities were retained.

After VM teardown and Cognition restart, the original 15-byte CSV retained
SHA-256 `d51be625f5a69b632f11a4b9c4511d14842a392a9ce35f613db2d59d61f2d46c`;
the 270,079-byte report retained
`39fc91dbce030f30f9372cbaf1adf4f111580d813c7c7b7c88f9c5099c80569d`.
Their task was `5e8916c7-6c97-5927-a1c4-e375b68b8aef`. Refreshed URLs resolved
the same published snapshots.

The builder-owned S3 Files integration (`86d958d`, evidence `3f8235a`) verified
workspace CRUD, replacement-VM remount fidelity, suspend/resume mount recovery,
and denial of another workspace's access point and the unrestricted filesystem
root. WayPost exercised creation and revision; original and revised deliverables
remained downloadable after teardown and server restart. Those checks used
separate scoped workspaces and execution roles, not a production tenant fleet.

Cognition ran with a read-only root filesystem, read-only configuration binds
and operational tmpfs, without a writable host workspace mount. Publication
transferred bounded bytes through memory without host-file staging. Background
workspace synchronization was separate from publication.

Retention revision `f989f2b` added published-object tagging and missing-content
handling. A real-S3 probe verified tags, exact bytes, deletion notices, scope
rejection and refreshed URLs after restoring the same content. It deleted only
its own temporary object versions; it used memory-backed task records and no
LLM. Separate retained PostgreSQL task retrieval passed after container
replacement. The retention suite passed 85 tests. No S3 Lifecycle rule was
installed; operators choose expiration policy. No new WayPost browser run or
latency baseline was performed for the additional availability check.

## Session sandbox ownership

SDK revision `e0f9f41` and Cognition revision
`fa60c92bad346344814bc6915fe602d2ad47b2ad` repair process-local sandbox reuse,
retain ownership/quota until confirmed teardown and reject execution through
released handles. The tested Cognition image was
`sha256:34f6fbede080e12560966c8eb66999ae3081df9225d29068768c135ad3f555cb`.

| Gate | First task | Follow-up | Result |
|---|---:|---:|---|
| Deterministic model, real VM/tools/S3 | 12.55 s | 4.00 s | Both succeeded, same VM |
| Live Bedrock report agent | 21.94 s | 14.55 s | Both succeeded, same VM |

The live task IDs were `145e8193-67ed-58be-8f6e-f193babbe930` and
`fdec84b4-67c2-534d-8816-32f18167dbc5`; their traces were
`fefd1da262c677fcef84bc484fb0e105` and `cd10a11f5f826d873d3c839a5247dda6`.
AWS confirmed benchmark VM termination afterward. Historical demo downloads
also passed after Cognition replacement. These are individual observations,
not a revised concurrency baseline or controlled warm-start speedup.

The ownership repair passed 179 regressions, Ruff, targeted mypy and strict
MkDocs. Live approval-resume and a new suspend/resume campaign were not rerun.
Ownership remains process-local: distributed recovery and infrastructure-enforced
writer fencing are deferred. Even a single process needs old-VM cleanup after a
crash before reusing an affected workspace.

## Remaining evidence limits

- The earlier deterministic campaign completed 30 tasks at each concurrency
  level and 102 publication-size samples. It exercises real tools/storage but
  cannot establish live-model latency. Initial warm/resume cohorts were invalid
  because of the subsequently repaired ownership defect; matched instrumentation
  overhead was not established after the repair.
- Live Collector outage, concurrent 10 MiB memory stress and backing-S3
  visibility-lag measurements were not completed in the reduced campaign.
  Exporter failures and tracing-disabled metrics have regression coverage.
- One of 36 earlier sampled traces was incomplete. The thirty traces in the S3
  repeat passed parentage checks; that does not explain the earlier missing trace.
- The latest archived full pinned A2A TCK result was 91 passed, one failed and
  173 skipped at TCK revision `5996b79f9cefa6fc390980e383e358a66fb9e49e`.
  `CORE-SEND-003` treats the required unsupported-media error as a failure because
  of its expectation metadata. Cognition returned `-32005` with
  `CONTENT_TYPE_NOT_SUPPORTED`. The full TCK was not rerun for later storage,
  retention or ownership changes; this is not a fully passing conformance claim.
- Early cross-model probes found workspace-path guidance issues, missing file
  publication with some models and a legacy GPT-OSS adapter/tool incompatibility.
  Providing the workspace location was sufficient for the sampled Claude runs.
  Those small experiments establish neither universal-model reliability nor a
  guarantee that requested structured output is always produced.

## Archive and reproduction

The complete source and evidence are preserved at commit
`79982391cb17d215503f4168674ac05663f11405`, named locally by
`codex/a2a-investigation-archive`. No remote publication of that branch is implied.
From a clone containing the commit, create a separate checkout:

```bash
git worktree add --detach ../cognition-a2a-investigation 79982391cb17d215503f4168674ac05663f11405
```

Within that checkout, use these paths under `examples/a2a-parts-investigation/`:

| Purpose | Archived source |
|---|---|
| Compose, AWS provisioning, publication smoke and cleanup | `README.md` |
| S3 Files mounts, isolated workspaces and WayPost | `s3-files/README.md`, `s3-files/EVIDENCE.md` |
| Performance runner, Collector and dashboards | `performance/README.md` |
| Original baseline and exact observations | `performance/LIVE-LOAD.md`, `performance/evidence.json` |
| S3 comparison, revisions, task/trace IDs and checksums | `performance/ASYNC-S3.md`, `performance/async-s3-evidence.json` |
| Historical caveats and incomplete cohorts | `performance/VERIFICATION.md` |
| Ownership gates and task/trace correlations | `performance/SANDBOX-OWNERSHIP.md`, `performance/sandbox-ownership-evidence.json` |
| Retention probe and outcomes | `RETENTION-VERIFICATION.md` |
| Cross-model diagnostic probes | `NATURAL_LANGUAGE_FINDINGS.md` |

These scripts retain investigation-specific assumptions and are not supported
deployment defaults. Supply your own AWS profile, isolated resource names and
authorized bindings. Credentials, local configuration and Terraform state are
not in Git. Existing state must be retained for cleanup; a fresh checkout alone
cannot safely take ownership of an existing investigation's resources. Do not
run competing copies against the same workspaces. The archive runbooks include
explicit VM termination and optional destructive storage cleanup commands.

## Release-tree cleanup checks

On September 8, the lab and its example-only mount test were removed from the
release tree. A clean export of the staged tree, without ignored local lab files,
passed 1,061 unit tests (four skips), Ruff, mypy over 268 source files and strict
MkDocs. Wheel and source-distribution builds succeeded and contained no
investigation files. Existing framework warnings remain. No production source
changed in this cleanup; the live campaign and full external TCK were not rerun.
Local operational files and AWS data were retained. The archive is currently a
local Git branch; retain it or publish it deliberately before discarding this
clone. Prefer a squash merge of the reviewed release changes if intermediate
investigation commits should also stay out of the release's ancestry.
