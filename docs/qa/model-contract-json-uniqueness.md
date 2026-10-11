# Native structured JSON uniqueness prerequisite, iteration 2

Base: integration `5a9c3a1`; branch: `fix/model-context-contract`.
Previous attribution fix: `10182a9`. This iteration leaves committing to the
GNHF orchestrator; its eventual commit ID is not yet available.

## Bounded outcome

Native `LocalModels` now rejects duplicate object keys during JSON decoding,
before dictionary construction can silently keep the last value. This applies
both to the bounded Ollama wire envelope and to generated structured content,
at every nesting level, before schema validation or optional sanitization.
Escaped spellings of the same decoded key are duplicates too. Errors expose no
source/model keys or values. Existing wire, context, output, model-option,
confidence, membership and selection limits remain unchanged.

This is a prerequisite for independently addressed candidate judgements,
**not a correction or actual proof of the observed duplicate-selection bug**.
Duplicate array reference values still reach the unchanged strict remapper
validator and are rejected there. Repeated values or local record refs in
distinct objects remain legitimate; the parser never deduplicates or repairs
them. Canonical intake parsing, durable records/source fields, digests,
refs-only embeddings and recovery are untouched. The OpenAI-compatible chat
client is outside this native-only slice.

## Diagnosis and next contract slice

Reviewed external `review/remapper-actual-probe.json`,
`review/remapper-actual-failure-diagnosis.md`,
`review/remapper-actual-failure-preflight.md`, and
`review/pilot-summary-fidelity.md`. The actual probes establish three selections
with two unique refs and valid confidences, even for the candidate-prefix-only
variant. They do not establish the cause or complete effective context survival.
Output termination was `stop`, not observed output exhaustion.

The proposed next slice is a response object with exactly one required key per
admitted candidate, each carrying an explicit model relevance decision and
checked reason/confidence. All admitted candidates must receive judgements;
missing or unknown keys must invalidate the entire response. Selection caps
still apply to model-selected decisions, including low-confidence decisions
before threshold projection, with no automatic promotion or truncation. Dynamic
schema bytes must remain in admission accounting under existing input bounds.
Candidate addresses must be distinguished explicitly from record-local refs and
source content named `ref`. A rename alone is not a demonstrated fix.

Before that protocol ships, regressions must cover multiple compact candidates
with the same local `e0`, exact key membership, boolean decisions, all existing
confidence/cell/count/output limits, and preserved durable field bindings.
This document is a design direction, not a claim that this slice implements it.

## Verification and remaining acceptance

- Focused native response/provider/remapper tests: 60 passed, 70 subtests,
  4.76 seconds.
- Full suite: 832 passed, 1 skipped, 258 subtests, 36.71 seconds;
  two existing dependency deprecation warnings.
- Native UI contract passed.
- Source distribution and wheel build passed.
- Release artifact structure passed; all 71 bundled runtime files match source.
- `git diff --check` passed.

Tests exercise native transport decoding with synthetic HTTP responses; they
are not actual model proofs. Regressions show ambiguous keys cannot conceal an
unknown evidence reference, an invalid confidence, output-length termination,
or a malformed generated response. Distinct judgement keys, repeated record ref
values, and source content named `ref` survive unchanged.

Actual model generation calls this iteration: **0**; durations: not applicable.
No intake, embeddings, stored-state mutation or background services were started.
The host has already granted the model slot; no additional permission is needed
for subsequent bounded proofs. This iteration consumes none of the six allowed
query generations or two fresh compaction attempts. Original failed pilot state
remains immutable. Exact-incident and cross-service runtime selection/citation
proofs, disposable mixed-web and separated-auth compaction prose proofs, and
readable unverified-summary/status-count disclosure remain outstanding. The
overall stop condition and release acceptance are not met.
