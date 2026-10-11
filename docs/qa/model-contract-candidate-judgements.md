# Independently addressed relevance judgements, iteration 3

Base integration: `5a9c3a1`; branch: `fix/model-context-contract`.
Attribution fix: `10182a9`; native JSON uniqueness prerequisite: `0719dbf`.
This iteration leaves committing to the GNHF orchestrator; its eventual commit
ID is not yet available.

## Bounded change

The remapper now supplies call-local `candidate_ref` addresses (`c0`, `c1`, ...)
and requires a `judgements` object with exactly those keys. Each candidate gets
an explicit boolean `selected`, enum `reason`, and numeric `confidence`.
Positive reasons remain `topic_match`, `exact_identifier`, and
`comparison_baseline`. Negative reasons are `not_relevant` and `selection_limit`.
An all-negative complete object is valid empty evidence; an empty object when
candidates were supplied is invalid. No candidate is selected automatically.

This is a structural protocol change, not a prefix-only correction. The supplied
actual diagnosis confirmed duplicate selection-array refs even after a prefix
rename; it did not prove the cause or complete effective context survival. The
new contract removes the repeated reference field from output items and requires
one independently addressed decision for every admitted candidate. Instructions
explicitly distinguish candidate addresses from record-local refs and source
content named `ref`. Canonical durable ref names, records, source field values,
important-field bindings, identifiers and digests are untouched.

Missing, unknown, record-local, or extra output keys fail closed. Native decoding
rejects duplicate JSON keys before dictionary creation, including duplicate
candidate keys, decision booleans and confidences. There is no old-array
compatibility path, deduplication, output repair, fallback promotion or truncation.
Reason enums must agree with the boolean decision. Every confidence, including
negative judgements, must be finite, numeric, non-boolean and within [0, 1].

Selection count and per-cell caps still apply to **all true decisions before
confidence projection**, even if all confidences are below threshold. The dynamic
object schema cannot express those cross-candidate true-count caps in the native
schema subset; the unchanged strict post-provider limit gates enforce them.
Unknown/missing keys additionally fail in the provider schema check. The native
client still uses temperature 0, context 8192 and output limit 1200, and rejects
length termination. No configuration bounds, thresholds, timeouts or release
gates were raised or relaxed.

The full dynamic schema, instructions and complete candidate payloads remain
charged to the existing character/UTF-8 budgets. The new schema repeats decision
properties per candidate, so it costs more than an enum array. Focused instructions
were shortened while retaining relevance, scope, untrusted-data, source-diversity,
literal-eligibility and whole-section-metric rules. Existing 4200-byte admission,
6400-byte two-cell round-robin and Unicode no-slicing regressions retain their
original bounds. Omitted candidates remain absent from both context and schema
with explicit budget gaps.

## Verification

Focused protocol/retrieval/context/native-runtime tests:

```sh
python -m pytest -q tests/test_rag_remapper.py tests/test_rag_exact_remapping.py \
  tests/test_rag_preserved_context.py tests/test_rag_compact_summary_context.py \
  tests/test_rag_source_diversity.py tests/test_native_preserved_runtime.py
```

- 85 passed, 102 subtests, 0.48 seconds.
- Duplicate native JSON keys are tested with otherwise valid complete judgement
  objects so an unrelated schema failure cannot mask silent overwrite.
- Synthetic native transport also accepts a complete independent select/reject
  response and verifies unchanged generation options. This is not an actual
  model proof.
- A three-service compact fixture supplies three independent candidates, each
  with local record `e0` and a source field named `ref` whose value is `c1`.
  Candidate addresses are exactly `c0/c1/c2`; field bindings and durable bytes
  remain intact, all chunks validate, and three distinct citations survive.
  Complete input including dynamic schema measures 7448 characters/UTF-8 bytes
  within the unchanged native-runtime 24000/48000 bounds. These are synthetic
  fixture measurements, not measurements of the stored failed pilot input.
- Direct validator tests exercise specific count/cell-limit failures with
  low-confidence true decisions, and confidence/output-budget failures for
  negative judgements. Runtime cap tests assert every fixture candidate was
  admitted, preventing membership failures from masking cap checks.
- Final full suite: 836 passed, 1 skipped, 273 subtests, 35.71 seconds;
  two existing dependency deprecation warnings.
- Native UI contract, source/wheel builds, distribution structure, and all 71
  bundled runtime byte checks passed. `git diff --check` passed.

## Remaining acceptance

Actual model generation calls this iteration: **0**; durations: not applicable.
No intake, embedding calls, original-state mutation or background services were
started. The authorized remaining actual-proof budgets are still six sequential
query generations and two fresh compaction attempts. Model-slot authorization
was already granted in the host notes; no new approval is required.

The new contract is structurally verified, but actual same-stored-state native
exact-incident and cross-service selection of three distinct services with
citations remains unproved. The original failed state remains immutable. A
fresh disposable mixed 33-event web compaction proof and separated-auth prose
review still must reject false 504/directory associations, fabricated source
adjacency and duration-sum-as-span under current native Mistral/nomic and the
original gates. A permanent separated-auth request fixture and readable
unverified-summary/exact whole-section status-count disclosure remain outstanding.
Membership/metrics validation does not establish arbitrary prose entailment.
The overall stop condition and release acceptance are not met.
