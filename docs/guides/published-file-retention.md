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
