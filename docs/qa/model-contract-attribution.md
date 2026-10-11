# Model input attribution correction, iteration 1

Base: integration `5a9c3a1`; branch: `fix/model-context-contract`.
This iteration leaves committing to the GNHF orchestrator. Its resulting commit
ID must be recorded by the subsequent review, rather than guessed here.

The compact request now supplies each record's exact `request_status`,
`duration_ms`, and `timestamp` beside its local ref and complete projected
message. Aggregate metrics cannot recover these associations. The same request
builder serves feasibility checks and generation, so added bytes remain subject
to existing deterministic splitting and input bounds. Common service, level and
release remain in section context. Canonical parser, catalogs, stored evidence,
digests, embedding recipe, recovery and output validation are unchanged.

Instructions restrict status and duration associations to their bound record,
preserve null as unknown, distinguish section totals from per-record values and
reject source-adjacency claims based on grouped record order. No prose parser or
repair was introduced; structural validation still does not prove entailment.

Regression coverage includes a 33-record mixed section through the unchanged
push parser, both canonical and trusted projected-message inputs: one payment
incident has status 504 and duration 5000 ms; 32 directory-index observations
have status 200. Every supplied measurement and timestamp matches its canonical
and retained record ref. Catalog/refinement checks, event keys, exact coverage,
and replay digests remain intact. The fixture fits one compact request under
unchanged defaults. Missing measurements remain null. These are fixture-model
contract checks, not an actual model fidelity proof.

Validation:

- Focused compact, projection and feasibility tests: 44 passed, 18 subtests.
- Full Python suite: 818 passed, 1 skipped, 256 subtests in 37.93 seconds;
  two dependency deprecation warnings.
- Native UI contract passed.
- Source distribution and wheel build passed.
- Release artifact structure and wheel/runtime byte checks passed.
- `git diff --check` passed.

Actual native generation calls in this iteration: **0**; durations: not
applicable. No model slot was requested or consumed, no stored pilot state was
modified, and no background service was started. The failed original summary
remains immutable. A fresh disposable 33-event proof with configured native
Mistral and nomic is still required, within the two-attempt cap, with explicit
prose review for the false 504/directory association. This document does not
claim that membership or metadata validation proves arbitrary summary fidelity.

The separate remapper defect remains unfixed. The supplied actual diagnostic
establishes duplicate selections even after a c-prefix rename; a namespace
rename alone is insufficient. Its cause remains unproven. A bounded independently
addressed relevance response contract, strict validation regressions and same-
stored-state exact-incident plus cross-service runtime proofs remain outstanding
(at most six additional query generations after the supervising root grants the
model slot). No confidence, selection, context, time or release gate changed.
The overall stop condition and release acceptance are not met.
