# Published-file retention

Operators can use S3 Lifecycle to expire generated deliverable bytes while keeping
Cognition's task history. Cognition does not install a retention rule or choose a
retention period. Workspace retention, task history and client caches have separate
owners.

## Delivery policy

For deployments where S3 should own generated-file retention, configure:

```dotenv
COGNITION_ARTIFACT_PUBLICATION_INLINE_MAX_BYTES=0
```

New nonempty files published with `publish_artifact` then use A2A `url` Parts.
Empty files can still use `raw`; they carry no file bytes. The generic default
remains 256 KiB and the publication maximum remains 10 MiB. This setting does not
change text/data output, incoming raw Parts, prior publications or downloaded
copies. Previously persisted raw Parts may still contain base64 file bytes in
task records, events and checkpoints after their S3 copy expires.

## Scoped publication policy

Builders may supply optional `publication` in an Agent definition or the Agent
create/update API. Omission (or an explicit null reset) preserves the deployment
policy. For example:

```yaml
publication:
  enabled: true
  max_bytes: 1048576
  delivery_mode: url
```

`enabled` is a boolean, `max_bytes` is an optional integer from one byte through
10 MiB, and `delivery_mode` is `auto` (deployment inline threshold) or `url`
(zero inline threshold). The effective byte maximum is the smaller of the Agent
and deployment limits. An Agent cannot override deployment disablement or raise
a deployment ceiling. These are runtime
controls; the builder owns authorization to change them and any product policy
that supplies their values.

The managed execution path resolves the current exact-scoped Agent policy at
graph construction and again before file reading and immediately before upload.
This policy is intentionally live even when the rest of a run definition is
pinned. API-owned Agents do not fall back to another scope or a shared file Agent
after deletion. Missing, invalid or unavailable current configuration denies
publication. A disabled policy omits the publication tool from newly constructed
graphs; previously constructed or interrupted graphs still check current policy
when their tool executes. Embedded graph callers may supply a static policy;
live registry enforcement requires the current Agent identity and config store.

An upload admitted by the final policy check may finish after a concurrent policy
update. A policy update does not cancel an already admitted upload or erase
existing publications. The observed revision returned by the Agent API proves
configuration persistence, not completion of every in-flight upload. Deployments
must account for this boundary when reporting disable convergence. Existing
artifact retrieval and signed URL lifetime remain independent of publication
permission.

`GET /capabilities` reports `scoped_artifact_publication_policy` support and the
deployment publication ceilings. Agent responses expose the configured policy
and existing revision/digest fields. No publication setting is added to public
Agent Cards. Definitions without a publication policy keep their previous
serialized shape and digest, including persisted pre-policy run manifests.

## Select only published bytes

Each newly published binary object carries this fixed tag, applied atomically
with its upload:

```text
cognition:content-class = published-file
```

The descriptor body and other artifact-store objects do **not** receive this tag.
Object keys remain unchanged:

```text
<configured-prefix>/scopes/<opaque-exact-scope>/
├── published/<publication-id>/<sha256>                 # tagged file bytes
└── artifacts/file/published-<publication-id>/...        # untagged descriptor
```

Do not expire the entire configured prefix: Cognition also uses it for other
artifact bodies, including configuration. S3 prefix filters cannot select a
middle path component across every opaque scope; the tag gives operators one
selector for published bytes across scopes. S3 supports tag filters, or a tag and
prefix combined with `And`. [AWS Lifecycle filters](https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html)

For example, this **disabled** rule illustrates a 30-day policy. Choose the period
and review existing rules before enabling it. Merge it into the bucket's existing
configuration; do not overwrite unrelated rules.

```json
{
  "ID": "expire-cognition-published-bytes",
  "Status": "Disabled",
  "Filter": {
    "Tag": {
      "Key": "cognition:content-class",
      "Value": "published-file"
    }
  },
  "Expiration": { "Days": 30 }
}
```

Existing publications are not automatically tagged or migrated. If operators want
the rule to cover them, they must separately review and tag only the binary
objects referenced by published-file descriptors. Unreferenced uploads from a
failed publication are tagged too, so the same age-based rule can eventually
remove their bytes. Age is measured from object creation, not last download.

On versioned buckets, current-version expiration can leave noncurrent bytes
behind a delete marker. Operators must also choose noncurrent-version retention
and delete-marker cleanup if physical deletion is required. Lifecycle processing
is asynchronous, so the configured age is not an exact access-revocation deadline.
[AWS expiration behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html)

## Permissions and retrieval behavior

The Cognition publishing principal needs `s3:PutObjectTagging` in addition to its
existing object read/write permissions. A tag-write denial fails publication;
Cognition does not silently upload untagged content or announce success. This
permission belongs to the publisher, not automatically to the agent sandbox.
[AWS PutObject permissions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html)

For a stored publication reference, Cognition validates the exact scoped
descriptor and expected object key, checks the binary using `HeadObject`, and
then signs a fresh download URL. The normal path adds one metadata request per
URL Part per projection; it does not download the binary. Generic external URL
Parts are not probed.

The signing duration is 900 seconds. URL expiration changes access through that
link; it does not delete the object. An authorized `GetTask` can provide a new
link while the content exists. Temporary credential expiration can shorten a
link's validity. Presigned links are bearer access and cannot recall bytes that
were already downloaded. [AWS presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)

If S3 reports missing binary content, Cognition verifies that the bucket remains
accessible and returns a standard text Part saying “Published content is no
longer available.” The artifact ID/name and task completion state remain intact;
other artifacts and history remain readable. Optional Part metadata under
`cognition` records `contentAvailability: unavailable`, the original URL kind,
filename and media type. This is application metadata, not a new A2A state or
required extension. The original durable reference remains persisted, so a later
restoration can make the download available again.

This notice does not claim that Lifecycle caused the deletion. A missing or
corrupt descriptor, wrong scope, inaccessible bucket, access denial or storage
outage remains an error. S3 can return 403 for missing objects when the caller
lacks `s3:ListBucket`; Cognition must not interpret that as expiration. Operators
who want reliable missing-content detection must give Cognition the corresponding
bucket permission. [AWS HeadObject permissions and errors](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)

The availability check and download are separate requests: an object can expire
between them. Clients should retrieve the task again after a failed stale link
and show the resulting notice or error. This is a client responsibility; the
change does not add automatic refresh or cache eviction to WayPost.

## Deploy and verify

Set `COGNITION_ARTIFACT_PUBLICATION_INLINE_MAX_BYTES=0` in the operator-managed
Cognition deployment when published-file retention should follow object storage.
Keep `COGNITION_ARTIFACT_PUBLICATION_MAX_BYTES` at or below the supported 10 MiB
maximum. Restart using your deployment's normal procedure, preserving the scope
HMAC key and durable stores. This setting changes new publication only; it does
not remove existing inline bytes from history or client caches.

Run the focused regression checks from the repository root:

```bash
uv run pytest tests/unit/test_publication_retention.py tests/unit/test_publication_files.py
```

In an isolated environment, publish a small file through an actual sandbox-backed
agent and check that the task exposes a URL, that the object carries the
published-file tag, and that an authorized download matches the source bytes.
Terminate the sandbox, restart Cognition and retrieve the task again. Check stable
artifact identity, refreshed access and rejection under a different scope.
For a disposable test object, deleting its body should yield a historical
unavailable-content notice; permission failures must remain errors.

The [validation record](../architecture/a2a-deliverability-validation.md) describes
completed live checks and their limits. Its archive instructions retain the
original Compose environment and real-S3 deletion/restore probe without making
those investigation-specific scripts part of the release. The historical
performance fixtures used 256 KiB; the later retention demo used zero.

Keep descriptors and task records as long as historical attribution is wanted.
Apply separate policies to PostgreSQL history/backups, old raw payloads, builder
workspaces and client caches. S3 Lifecycle alone is not deletion of every copy.
Cognition does not install an AWS expiration policy automatically.

## Inactive-context maintenance (integration branch)

The private `cognition retention` command provides a bounded maintenance pass for
inactive runtime contexts. It is not enabled on a schedule automatically. Run it
with the same storage configuration as the runtime. Deployment-wide settings are
`COGNITION_SESSION_RETENTION_ENABLED` (false),
`COGNITION_SESSION_RETENTION_DAYS` (30), and
`COGNITION_SESSION_RETENTION_BATCH_SIZE` (100, maximum 1000).
The command uses the configured inactivity age and batch size by default;
`--before` and `--limit` can override them for a maintenance pass. Explicit cutoffs
must include a timezone. Applying cleanup requires the enablement setting.
When session retention is enabled, `COGNITION_A2A_TERMINAL_TASK_TTL_SECONDS`
must remain zero. The older task cleaner removes run ownership and can delete
artifact bytes; settings validation rejects running both policies together.
`COGNITION_SESSION_RETENTION_INTERVAL_SECONDS` (3600) controls the private worker:
run `cognition retention --watch --apply` with enablement set to true. It processes
one bounded page per interval, cycles back after the last page, and retries
transient failures without exposing provider errors. It uses a fresh age cutoff
for each page; `--watch` rejects `--before`. Cancellation stops the worker.
Deploy this command as an operator-owned maintenance workload; the API server
does not start it automatically. No per-Agent override is provided. The command
opens only record storage, without initializing the S3 transport.

```sh
cognition retention --before 2026-08-01T00:00:00Z --limit 100
COGNITION_SESSION_RETENTION_ENABLED=true cognition retention --before 2026-08-01T00:00:00Z --limit 100 --apply
```

The default previews candidates without deleting content. `--scope-json` selects
one exact scope; omitting it scans all scopes, including scopes with no current
Agent definition. Follow the returned `next_cursor` with `--cursor` until it is
null. Start subsequent sweeps from the beginning so failed candidates are retried.
The preview is advisory: application rechecks eligibility atomically and can
protect a candidate that received new activity after scanning.

An eligible context is marked expired before cleanup. New runtime work cannot be
admitted to that context. Cleanup removes run-owned artifact records,
checkpoints, and runtime messages/events/tasks/runs before removing the session.
It neither reads nor deletes S3 bodies. Published-file expiration belongs to the
storage lifecycle policy. S3-backed text artifact bodies and descriptor bodies
also need a separately admitted storage policy; deleting their database records
does not expire those objects, and the published-file tag does not select them.
A dependent-store failure retains the expired session and run identities for
retry, reports a failure count, and exits nonzero. A timeout or failed provider
operation is not reported as completed deletion. Live tasks, active or resumable
runs, and shared checkpoint threads prevent reclamation.

Coverage is context-based: this command does not impose a maximum age on individual
tasks inside a continuing context. It does not delete Agent definitions,
cross-thread memory, externally mounted workspaces, noncurrent object versions,
or backups. Those require their respective owners' retention policies. New
published descriptors carry their trusted run identity. Legacy descriptors lacking
that identity and historical orphaned data require separate inventory and migration;
this pass must not be presented as evidence of their erasure.

This maintenance slice is still under integration review, particularly concurrent
artifact-version ownership and interaction with the older opportunistic A2A task
retention path. Do not enable production scheduling on the strength of unit tests
alone. Published-body lifecycle expiration remains independent of context cleanup.

### Moving existing inline artifacts to S3

Changing `COGNITION_DURABLE_FILE_BACKEND` does not migrate existing artifact
bodies. The experimental offline maintenance module can move PostgreSQL inline
artifacts into the configured S3 destination while preserving their scoped
identities and versions. Keep the previous runtime configuration and a tested,
restorable database backup. Stop runtime and administration writers before apply;
this command does not coordinate an online cutover.

With the destination's normal S3 and PostgreSQL settings supplied securely:

```sh
python -m server.app.storage.migrate_artifacts --limit 100
python -m server.app.storage.migrate_artifacts --limit 100 --apply --writers-stopped
```

Preview reports the pending row count without initializing or writing S3. Apply
moves at most the requested page and verifies uploaded bytes through the existing
artifact store before activating its manifest. Failed rows are counted without
printing provider errors or content. Inspect failures before repeating; do not
interpret zero pending rows as sufficient evidence if a page reported failures.
Confirm scoped historical retrieval and content before restarting writers with
the new backend. Retain the backup until that acceptance succeeds. The command
is under validation and is not yet admitted for unattended production migration.
