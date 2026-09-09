# Deferred A2UI implementation and review

Reviewed 2026-09-08. Recommendation: retain the optional feature, but do not merge the existing release branch as ready to ship. Correct the demonstrated validation defects and approval-resume gap, then prove interoperability with a pinned renderer. This investigation makes no production changes and does not authorize a release.

## Scope and revisions

- A2UI feature: [`b162f78d4160ac9d57f60d0a296ffb414692e588`](https://github.com/CognicellAI/Cognition/commit/b162f78d4160ac9d57f60d0a296ffb414692e588), on `origin/release/v0.15.0`.
- Compared the isolated feature against its parent, rather than attributing earlier documentation-site work to A2UI.
- Current main: `8b688bc5c3015cb4fcae32acb1da22ae9d0da3b0`.
- Temporary synthetic integration tree: `d7ebb815877f0b0e47d0e634c2fb8c656307008a`, produced with `git merge-tree --write-tree origin/main origin/release/v0.15.0`. Only CHANGELOG.md and server/version.py conflict textually. Runtime auto-merges still require semantic review. For tests, extracted this tree to /tmp and replaced server/version.py with main's version. No release branch was changed.

## What exists and why it fits

This is an agent-definition opt-in A2A extension, not an alternate sandbox or file-delivery architecture. It advertises A2UI in the Agent Card, negotiates renderer capabilities, selects the bundled Basic catalog, requests typed model output, validates complete message batches, and projects them into A2A data Parts using application/a2ui+json. Existing task persistence and streaming carry those Parts. The client owns rendering; Cognition does not serve a UI renderer. Unsupported agent-function calls return explicit errors without inventing a tool registry.

That division fits Cognition's backend mission. A2UI describes interactive interfaces; the merged sandbox/S3 deliverability work supplies downloadable files. They are complementary. The implementation buffers its structured UI response before emitting a complete batch; it does not demonstrate progressive component rendering while the model is generating.

See the [implementation guide](https://github.com/CognicellAI/Cognition/blob/b162f78/docs/concepts/a2a/a2ui.md) and [proposal](https://github.com/CognicellAI/Cognition/blob/b162f78/docs/proposals/optional-a2ui-v1-a2a-extension.md). Upstream currently lists A2UI v1.0 as Candidate and v0.9.1 as the current production specification, independently of A2A's own version. [Official A2UI versions](https://a2ui.org/)

## Standards review — four findings, worst P1

These findings are from code inspection unless explicitly marked reproduced below. Counts overlap with the separate specification review and must not be added as unique defects.

1. **P1: approval resume loses A2UI policy.** In [deep_agent_service.py](https://github.com/CognicellAI/Cognition/blob/b162f78/server/app/llm/deep_agent_service.py#L1024), resume reconstructs the ordinary response format and its event handling at lines1084–1104 omits StructuredResponseEvent. Initial execution alone applies the negotiated envelope, validation and A2UI artifact conversion. Share output-policy handling across initial execution and resume and add a real interrupted-runtime regression. This conflicts with the backend's approval-lifecycle guarantees; AGENTS requires observable, attributable behavior rather than hidden divergence.
2. **P2: upward dependencies.** deep_agent_service.py:71–80 imports the A2A protocol implementation; a2ui/core.py:18 imports Layer7 observability. AGENTS: “Dependency direction is strictly top-down. No lateral or upward imports.” Keep transport negotiation/projection in the adapter and use protocol-neutral runtime parameters/events; lower instrumentation should use the standard OTel API.
3. **P2: validation precedes resource limits.** [executor.py:156–189](https://github.com/CognicellAI/Cognition/blob/b162f78/server/app/protocols/a2a/executor.py#L156) validates A2UI before normal input-size enforcement. core.py validates inline catalogs before rejecting them and materializes every validation error. Enforce cheap byte/count/depth limits first, reject unsupported inline catalogs immediately and stop after sufficient diagnostic information. No stress/DoS experiment was performed.
4. **P2: repair restarts the timeout budget.** deep_agent_service.py:749–764 grants each of two attempts the complete execution timeout. Use one monotonic deadline and only the remaining duration for repair. This is a configured-policy enforcement issue; elapsed-time overrun was not measured in this investigation.

## Specification review — five findings, worst P1

1. **P1: negotiated catalog boundary is unenforced — reproduced.** [core.py:204–211](https://github.com/CognicellAI/Cognition/blob/b162f78/server/app/protocols/a2a/a2ui/core.py#L204) checks schema/version without the negotiated catalog context. createSurface referencing https://foreign.example/catalog passes. This does not itself fetch a remote URL; the defect is emitting an unnegotiated identifier to the renderer. Proposal: “The model must not be able to invent or alter the selected catalog set.” Check all catalog references, including component/function overrides, before emission.
2. **P1: approval resume omits negotiated output handling.** Same runtime defect as the standards review. It violates the proposal's no-silent-fallback and existing approval-boundary guarantees.
3. **P2: valid extension metadata crashes validation — reproduced.** Bundled common_types.json:41–49 uses Unicode property escapes unsupported by Python re. Valid metadata.extensions={"vendor_x":true} raises `error: bad escape \\p at position 2`, bypassing the intended A2UI validation error/repair path. Use a compatible validation approach and regression-test official schema features. Do not silently rewrite the claimed pinned schema.
4. **P2: malformed metadata is treated as absent — reproduced.** core.py:115–116 converts non-object capabilities/data-model metadata to None. An explicitly activated request with string capabilities and an array data model successfully negotiates Basic. Missing and supplied-invalid metadata must be distinguished; the proposal requires all supplied A2UI metadata to pass validation.
5. **P2: the live test does not prove its claimed continuation.** [test_a2ui_live_model_e2e.py:199–232](https://github.com/CognicellAI/Cognition/blob/b162f78/tests/e2e/test_a2ui_live_model_e2e.py#L199) sends the action without the initial contextId or task reference. It starts an independent conversation, supplies explanatory text, and checks output message keys. No renderer is exercised. The proposal explicitly requires Basic-renderer interoperability and updating the original surface.

The generation envelope also uses list[dict[str, Any]], with a few prompt examples rather than a fully catalog-resolved generation schema. Post-generation validation exists, but that must not be described as model generation constrained by the complete catalog.

## Executed checks and their limits

- Original feature revision: **72 passed**, comprising test_a2a_jsonrpc_runtime.py, test_streaming_bugs.py and test_deep_agents_alignment.py.
- Synthetic main+A2UI tree: the same **72 passed**, plus **36 passed** across test_structured_result_publication.py, test_sandbox_ownership.py and test_publication_retention.py. Total **108**; these are selected regression tests, not full integration certification.
- Direct function probes reproduced the three validation findings. Their output was: `Foreign catalog accepted: True`; `Extension validation: error bad escape \\p at position 2`; `Malformed metadata activates: True`.
- Live-model test: **one skipped**, because COGNITION_OPENAI_COMPATIBLE_API_KEY was not present in the test environment. No live-model success or browser rendering is claimed here.
- Tests used Python3.12.11 and the existing investigation virtualenv, not a fresh lockfile install. Ruff/mypy and the full test suite were not rerun in this investigation.
- [Historical workflow32103957679](https://github.com/CognicellAI/Cognition/actions/runs/32103957679) passed Python3.11/3.12 unit, type and lint jobs plus amd64/arm64 app and sandbox builds at exact b162f78. It did not run the live A2UI E2E test.
- Downloaded and inspected that workflow's actual TCK artifact. It reports MUST66pass/1fail, SHOULD7pass, MAY4pass, with the existing CORE-SEND-003 advisory failure. The explanation attributes it to missing expected-error metadata in pinned TCK5996b79. This is A2A protocol evidence, not A2UI renderer conformance. [Existing upstream issue](https://github.com/a2aproject/a2a-tck/issues/202)
- A source search of the current local WayPost checkout found no A2UI references. This is not proof of every dependency's capability; an interactive A2UI renderer must be explicitly verified or added before claiming WayPost support.

## Recommended next increment

Upstream research verified all ten bundled files byte-match the claimed pin44a420b. At upstream head0ae7af8fdee975d5b727e2e7cea267175062048c, seven message/capability schemas remained identical, but common_types.json, catalog_definition.json and the Basic catalog changed. In particular, function caller rules changed while the catalog URL remained the same. This makes paired revision testing important; it does not prove ordinary Text surfaces are broken. [Pinned assets](https://github.com/a2ui-project/a2ui/tree/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0), [caller rules](https://github.com/a2ui-project/a2ui/pull/2238)

Renderer documentation is not a substitute for a compatibility test: the pinned-current support matrix lists v1.0 as planned for several renderers, while the development guide describes candidate v1_0 core support. Verify the actual selected renderer/package revision rather than claiming either universal support or universal incompatibility. [Support matrix](https://github.com/a2ui-project/a2ui/blob/0ae7af8fdee975d5b727e2e7cea267175062048c/docs/public/reference/renderers.md), [development guide](https://a2ui.org/guides/renderer-development/)

Bring the isolated feature onto current main on a dedicated feature branch; keep release numbering/changelog decisions separate. Correct the reproduced validation issues, share initial/resume output policy, fix limit ordering and repair deadlines, and remove the new upward dependencies. Preserve optional activation and standard A2A data Parts without adding a plugin framework or renderer to Cognition.

Then pin a compatible Basic renderer and prove: natural-language UI creation, rendering, action submission in the same scoped conversation, visible update, approval interruption/resume, and scoped GetTask replay. Keep this fixture small and outside production behavior. Run focused isolation/invalid-output tests, full quality checks and a fresh release-image workflow on the final revision. The current green tests support continuing the work, not merging it unchanged.

Decision: deferred by the release owner on 2026-09-08. The existing implementation and this review are preserved on codex/deferred-a2ui for a future feature branch. They are excluded from the current release scope. The original review remains in ignored localdocs in the investigation workspace.
