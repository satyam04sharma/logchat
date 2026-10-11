# Archived native selection proof, iteration 4 — superseded

> **Archived historical evidence; acceptance revoked.** Subsequent installed-provider
> diagnosis confirmed that the full 8811/8839-token inputs were truncated to 4099
> tokens. **4099 was not complete input.** The historical apparent exact pass below
> is superseded after confirmed truncation and cannot establish relevance acceptance
> or a complete-input pass. Original measurements and failures are preserved here.
> The active independent classifier's actual acceptance remains pending; root owns
> independent actual QA and final acceptance. This archive grants no current call
> permission.

## Superseding diagnosis and current status

The following updates use the supplied reviewed diagnosis. Later full-fit exact
probes at 5655 and 5756 tokens (the latter provider-matched) still omitted auth.
An unchanged-evidence rotation at 5756 tokens selected all three services only as
a diagnostic; it is not acceptance evidence. The current baseline is **12 relevance
generations overall, with no fresh compaction proof attempts**. No new model or
network calls were made for this documentation correction.

The [100K bug report](100k-bug-report.md#fresh-pilot-cycle-2-complete-indexing-invalid-multi-service-selection)
preserves the failed pilot and its separate release gates. Complete indexing and
candidate availability do not establish successful model relevance. The execution,
results, call accounting and verification below describe the historical iteration,
not current QA or authorization.

Integration base: `5a9c3a1`. Attribution: `10182a9`; strict JSON decoding:
`0719dbf`; candidate judgements: `374e8d0`.
This was a documentation-only iteration, committed by GNHF as candidate `ec389b4`.

## Execution and immutable evidence

Ran actual `NativeRAGRuntime.context`, including native retrieval/query embeddings,
Mistral generation and cited context assembly, imported explicitly from this
worktree's `src`. Private artifacts record runtime import paths and SHA-256 for
runtime, remapper, sections and model transport. The developer environment's
editable integration install was not used as the fixed implementation.

The original cycle-2 state was copied byte-for-byte into disposable external proof
state. Native initialization and queries ran only against that copy. Before/after
hashes of every original state file agree. No intake, chunk regeneration, credential
injection, scheduler start or original-state writes occurred. The snapshot retains
44 stored chunks. No fake client, mock, selection repair or validation bypass was
used. The transport instrumentation delegated requests and responses unchanged.

External evidence directory: `model-contract-proofs/iteration-4` under the host's
existing evidence root. Scripts and private state remain external; this document
contains no personal paths, secrets or source rows.

## Historical actual results: apparent partial pass superseded, overall FAIL

| Query | Wall seconds | Provider total seconds | Prompt/output tokens | Outcome |
| --- | ---: | ---: | --- | --- |
| Exact incident | 4.213 | 3.576 | 4099 / 152 | Three distinct auth/web/worker selections; apparent result superseded after confirmed truncation; no complete-input or relevance acceptance pass |
| Cross-service incident | 15.799 | 15.593 | 4099 / 153 | `invalid_model_selection`; zero selected; failed |

Both completed with `done_reason=stop`, using Mistral 7B, temperature 0,
`num_ctx=8192`, `num_predict=1200`, the configured nomic embedding profile,
24000-character/48000-byte remap input bounds and confidence threshold 0.5.
Exact input measured 19668 characters/bytes; cross-service measured 19780.
Both admitted three candidates. Cross-service retrieval returned 12 candidates,
with nine literal nonmatches omitted by existing eligibility rules. All three
requested services were eligible; all three remained missing after rejection.

The historical exact proof checked three unique evidence IDs and services, retained incident
support in every selected row, original project/environment/source scope,
citation ID membership, unchanged durable summaries, successful query embedding
and successful remapper provenance. The original failed summaries remain
unverified; relevance and citation membership do not establish prose entailment.

The cross-service proof is a failure, not an empty-evidence success. Its empty-set
citation/scope checks are vacuous and are not evidence of successful selection.
No raw model output was saved. The exact internal `_validate_output` exception
was not instrumented, so the violated field/confidence/reason contract cannot be
inferred from the generic failure category. That was the historical diagnostic
uncertainty; it is not a current request for probes. Effective input survival is
now diagnosed: the full 8811/8839-token inputs were truncated to 4099, so these
counters represent incomplete input. This diagnosis does not identify the exact
internal validation exception. Do not infer repeated references, repair decisions
or weaken checks.

## Historical call accounting and scope (no current authorization)

**Three actual query generation calls consumed**, not two. The first completed
native exact query encountered a proof-script `KeyError`: the script used
`evidence_id` where citation objects use `id`. Its failed harness record was
preserved separately, with no pass claim. Its provider counters and elapsed
duration were not retained and cannot be reconstructed. After correcting that
check, the two measured calls above ran sequentially on a new byte-identical
snapshot. The corrected pair consumed two query embedding calls; the initial
failed-harness query also used actual native embeddings. No further model calls
were made after the cross-service failure.

At that historical point, three of the then-authorized six query generations and
both fresh compaction attempts were unused. These are historical counts, not
remaining-call authorization or current permission. Current accounting is the
12-generation baseline above, with no fresh compaction proof attempts.
Mixed-status/separated-auth actual prose proof, readable unverified-summary/status-count
disclosure and the permanent separated-auth request fixture were outstanding in
that iteration; this archive does not establish their current completion. Release
acceptance remains unmet, and active independent-classifier actual acceptance is
pending. The historical iteration released its model slot.

## Historical verification

Focused protocol/retrieval/context/native tests: **85 passed, 102 subtests**,
0.69 seconds. Full suite: **836 passed, 1 skipped, 273 subtests**, 36.81 seconds; two
existing dependency deprecation warnings. `git diff --check` passed.
No runtime/test code changes or build inputs changed in this iteration; prior
iteration build and artifact checks are not claimed as a new build here.
