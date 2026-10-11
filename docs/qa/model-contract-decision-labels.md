# Compatible candidate labels, continuation iteration 1

Base candidate: `ec389b4`. Prior attribution (`10182a9`), strict duplicate-key
decoding (`0719dbf`) and independently addressed judgements (`374e8d0`) remain.
This bounded iteration changes the relevance output contract and its fixtures;
it makes no actual native model proof claim.

## Contract

The supplied independent diagnosis in
`model-contract-proofs/verified-followup-diagnostic/result.json` and
`review/verified-diagnostic-preflight.md` reported three admitted candidates,
three complete judgements, valid confidences, and two incompatible boolean/reason
pairs. Missing keys, extra keys and duplicate/numeric guards were not the cause.
That diagnosis is supplied evidence, not a newly executed diagnostic here.

`bounded-model-relevance-v9` requires exactly `label` and `confidence` per
candidate. The provider schema permits one of `topic_match`, `exact_identifier`,
`comparison_baseline`, `not_relevant`, or `selection_limit`. The first three
select; the last two reject. A separate decision boolean does not exist, so the
schema cannot express contradictory decision/reason combinations. The remapper
derives selection from this new contract and preserves the selected label as
the existing downstream `SelectedEvidence.reason`.

Complete old boolean/reason responses fail, even when internally compatible.
Mixed old/new responses also fail. There is no translation, repair, deduplication,
fallback promotion or omission of invalid negative judgements. Required candidate
keys, duplicate JSON decoding, finite non-boolean confidence in [0, 1] for every
label, positive caps before confidence filtering, literal cooccurrence, source
fairness, scope, citations, and joint instruction/schema/payload budgets remain
strict. Generation options, thresholds, context, timeout and all configuration
bounds are unchanged. The shorter schema may admit more complete candidates
within the same bounds; it does not slice candidate content.

Source: `src/logchat/rag/remapper.py`; SHA-256:
`46a3212c46894576964ba1e66121e0c13b4f68883f78a9aea0cf974aa41ffd80`.
This identifies tested source bytes, not an actual native proof import.

## Validation

- Focused protocol/retrieval/context/native-runtime suite: 87 passed, 145 subtests.
- Full suite: 838 passed, 1 skipped, 316 subtests in 36.92 seconds; two existing
  dependency deprecation warnings.
- Native UI contracts, isolated source/wheel builds, distribution structure,
  and all 71 bundled runtime byte comparisons passed. `git diff --check` passed.

Synthetic native transport tests exercise every label and confirm the actual
schema and unchanged generation options. Both native schema validation and direct
remapper validation reject old/mixed schemas, unknown or non-string labels,
unknown/missing candidate keys and missing/extra judgement fields. Duplicate
candidate, label and confidence keys fail before dictionary construction using
otherwise valid new-contract fixtures. Both negative labels retain strict
confidence guards. The output-budget fixture now uses ten negative judgements
because eight shorter new-contract judgements fit the unchanged 500-character
test limit. Positive cap tests still check low-confidence decisions before
projection. The updated three-source fixture retains canonical compact bytes,
local record/source refs and three distinct citations.

## Remaining bounded acceptance

Actual query generations, intake/grouping/compaction and native embedding calls
this iteration: **0**. No original state, source credentials or external proof
artifacts were changed. No background services were started. No commit, push,
PR or merge was performed by this worker.

The current fresh phase still permits at most **two additional actual query
generations**, one exact INC query and one cross-service query, on a byte-identical
disposable original cycle-2 state, through `NativeRAGRuntime.context` with an
explicit import from this worktree's `src` and current Mistral/nomic. Dry preflight
must succeed before model access; citation objects use `id`. Stop the phase on
the first actual failure and record its exact rejection; no retries beyond the
granted phase. Prior call counts remain as supplied: four old worker query calls
plus one verified diagnostic = five; a separate original two diagnostics plus
one prefix variant = three. This note supersedes historical remaining-budget
claims without rewriting old evidence.

All **two fresh compaction attempts** remain unused, each permitted to exercise
both the mixed web and separated auth sections. Their actual prose must reject
false 504/directory associations, unsupported consecutiveness/source adjacency,
and summed work as elapsed span. Native call accounting, source/state hashes,
canonical records/catalog bindings/metadata/digests and explicit runtime imports
remain required. Membership and metadata checks do not establish prose entailment.

Permanent separated-auth request fixtures, minimal readable “unverified model
summary”/exact whole-section status distribution disclosure, fresh native proofs
and final candidate checks remain outstanding. The model slot remains reserved
for GNHF under the current grant. The loop stop condition is not met; worker
completion will not constitute user acceptance or release approval. Root's fresh
Chrome/pilot/100K/GitHub review remains outside this iteration's scope.
