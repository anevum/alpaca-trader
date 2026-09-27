# RHEN Research Agent artifact conventions

These paths define the stable convention for future authorized artifacts. This
foundation does not contain a real proposal or experiment.

- `research/agent/proposals/<proposal-id>/revision-<N>.json`
- `research/agent/manifests/<experiment-key>.json`
- `research/agent/methodology/<experiment-key>.md`

Proposal revisions are immutable after an authorization references their exact
`proposal_id`, `revision`, and SHA-256 `proposal_hash`. A material revision keeps
the proposal ID, increments the revision, records a reason, and produces a new
hash.

Canonical JSON uses UTF-8, sorted object keys, compact separators, no NaN, and a
single final newline in artifact files. The proposal hash covers the canonical
proposal payload. The manifest hash is SHA-256 over canonical manifest JSON with
its own `manifest_hash` value replaced by the empty string. The methodology
document's SHA-256 digest is included in the manifest, so any methodology change
changes the manifest checksum.

All development, validation, holdout, and quarantine windows begin locked and
unopened. Freeze only prepares metadata; it never opens or executes a stage.
