# Source-observation correction status

## Current checkpoint — joint lexical controls, iteration 2

Added 16 deterministic controls with current authenticated v3 synthetic quotations
and below-floor (0.499999) vector scores plus actual mock-backend lexical hits.
Service/source fairness survives a leading crowd and the six-candidate and
three-selection caps; a fairly admitted negative source still cannot be selected.
Bounded lexical results exclude later vector-only hits. Exact-field controls
preserve same-field alternatives, late joint-field witness priority and absent-ID
exclusion. Late invalid/provider failures and deadline expiry discard earlier
positives; external cancellation cancels the pending call and stops execution.
The original shared deadline remains capped at 60 seconds even when the supplied
configuration permits 180 seconds. No runtime changes were needed this iteration.

Complete v3 requests exhaust the unchanged aggregate input budget before eight
calls in the three-cell fixture. The test verifies round-robin ordering, visible
budget omissions and bounded selection without shrinking evidence or increasing
limits. Separate character/UTF-8 budget controls retain complete-request admission.
Existing independent-classification controls still exercise the eight-call/global
selection ceiling. Retrieval can retain bounded nonmatching literal neighbors;
only eligible witnesses reach classification, with joint witnesses ordered first.

Validation: focused lexical, scope, fairness, classifier and retriever checks
**141 passed, 1 skipped, 92 subtests passed**. Full suite **1,280 passed,
1 skipped, 363 subtests passed**, with two dependency deprecation warnings.
`node tests/native_ui_contract.cjs` and `git diff --check` passed. Offline build
was retried with `--no-isolation` and remains blocked by missing `setuptools`;
no environment/dependency installation was performed. No linter/formatter is
configured in `pyproject.toml`. All started validation processes exited; no
background service, browser or watcher was started.

Worker delta review found changes limited to deterministic tests and this status
note; the prior runtime correction remains unchanged. The precise policy and
limits are documented in `source-observation-protocol.md`. The clean orchestrator
commit and root independent correction review remain pending, so the full stop
condition is not yet claimed. Root owns integration and actual native acceptance.
No actual model/network/embedding/intake/browser calls were made, and no actual
native, 1200/100K, release or installed UI acceptance is claimed.

## Prior checkpoint — joint lexical admission, iteration 1

The retriever now admits an actual bounded backend lexical hit below the unchanged
0.5 cosine floor only when all non-scaffolding query tokens occur in one checked
in-window v3 quotation, with at least two meaningful words. A closed conservative
English scaffolding policy, context-label exclusions (including label components),
and identifier exclusions prevent non-topic overlap from supplying those words.
See the protocol for exact matching and unchanged literal/classification limits.
No summary-format authentication, compaction, embedding or canonical identity
policy changed.

The new synthetic controls cover joint support, additional-topic and partial-word
rejection, cross-quote/cross-record non-stitching, pre-cap partial crowds, labels,
boilerplate, absent IDs, half-open scope, historical prose, required backend hits,
the unchanged vector floor, and strict negative/low-confidence/invalid responses.
The original question, including negation, reaches independent classification.
Offline read-only replay of the saved private preview artifact with both saved
query cells passed six controlled cases: lexical hit at 0.499999 admits to a
mocked classifier; without a lexical hit it rejects; 0.5 preserves vector
admission. These are synthetic scores, not the unavailable actual score. No
private source body or provider output was added to fixtures or documentation.

Focused checks: 80 passed, 1 skipped, 42 subtests passed. Final full suite:
1,264 passed, 1 skipped, 363 subtests passed, with two dependency deprecation
warnings. Native UI contract script and `git diff --check` passed. All validation
processes exited; no background service was started. Offline package build
remains blocked by missing `setuptools.build_meta`
in the prescribed environment; no dependency installation was attempted.
Iteration 2 still needs dedicated lexical-route fairness/OR/AND, shared-budget,
late failure/cancellation/timeout controls and the final correction review.
Root owns independent review, actual native acceptance and integration. The
orchestrator owns commits; this worker creates no commit. No actual model,
network, embedding, intake or browser acceptance is claimed.

## Prior checkpoint — query-scoped correction and control hardening

Fresh compaction is active v3, including complete reservations and batch privacy
checks. The earlier durable-dispatch checkpoint below is historical, not the
current runtime state. The independent review of `d55708a` accepted those
structural changes and reproduced narrow-window leakage.

This correction derives compact facts from each actual cell before literal
anchors and classification, preserves original refs and canonical bytes, and
uses per-cell evidence rows throughout readable/agent surfaces. Selected compact
metrics use in-window event identities once per cell. See the portable protocol
for the `(cell_id, original_chunk_id)` representation and canonical citation
compatibility note. No intake, embedding recipe, provider or budget change is
part of this correction.

Validation used the prescribed Python/PYTHONPATH, with deterministic fixtures:

- Final focused query-scope, source-diversity, independent-classification and
  fresh-v3 controls: **83 passed, 50 subtests passed**. This includes **27**
  narrow-cell controls (17 added in iteration 2). Earlier runtime checkpoint:
  **30 passed, 5 subtests passed**; activation/answering: **141 passed,
  21 subtests passed**.
- Final full suite: **1,241 passed, 363 subtests passed, 1 skipped** (two existing
  dependency deprecation warnings).
- `node tests/native_ui_contract.cjs`: passed, including new same-citation,
  different-cell comparison grouping and copied-snapshot scope controls.
- `git diff --check`: passed. No configured linter/formatter is present.
- Offline `python -m build --no-isolation` could not start: the prescribed
  environment lacks `setuptools.build_meta` / `setuptools`. No environment or
  dependency installation was changed; this is not a successful package build.

Iteration 2 adds controls without further runtime changes. Offset-spelled durable
timestamps filter by instant while retaining their original spelling. Derived
record renumbering, changed identities/typed measurements, duplicates, reordered
records and invented refs reject against the full durable representation; even
corruption outside the query cannot be hidden by filtering. Narrow multi-source
admission excludes outside-only strong IDs and preserves source fairness,
same-field OR, joint-field AND and absent-ID rejection. Late provider/invalid
classification failures discard earlier positives; external cancellation
propagates and stops further calls. Optional answer highlights preserve distinct
cell observations sharing one canonical citation. Query controls compare
immutable chunk/evidence/embedding bytes before and after queries; identity
hashes are included in the chunk snapshot. Re-signed timestamp fixtures test
durable encoding compatibility, not renewed source authentication. All started validation
processes exited; no background service, browser or watcher was started.
The worker structural stop condition is met; no further runtime change was
needed in this iteration. The package-build limitation above remains explicit.
Root-owned independent delta review and actual native acceptance remain pending.
The GNHF orchestrator owns the frozen commit; this worker made no git commit.
No actual model/API/tokenizer/embedding/intake/browser calls were made for this
correction; no native, 1200/100K, deployment or release acceptance is claimed.

## Historical checkpoint — durable dispatch iteration 2

Iteration 2 is a partial durable-dispatch checkpoint. Fresh runtime compaction
still emits historical v2 prose; the stop condition is not met. No actual model,
embedding provider, native intake, browser or historical state was invoked or
modified. Embedding integration was tested only with an in-process mock.

Changed files in this iteration:

- `src/logchat/rag/sections.py`: explicit v3 evidence/render validation,
  canonical summary equality, full-evidence pattern identity and v3 embedding
  recipe; historical v1/v2 recipes and projection-only recovery remain intact.
- `tests/test_compact_source_observation_versions.py`: canonical v3 dispatch,
  source-discard honesty, durable fact/render/identity tampering, historical
  identity goldens, summary-size rejection, serialization, migration rejection
  and mock embedding-spec/provenance controls.
- `docs/rag-rebuild/source-observation-protocol.md`: durable schema, embedding
  recipe and unchanged budget/migration boundaries.
- This status note.

Validation used the prescribed evidence virtualenv Python with `PYTHONPATH`
pointing to this worktree's `src`:

```sh
PYTHONPATH="$PWD/src" /Users/jaadu/Downloads/logchat-100k-evidence/venv/bin/python -m pytest -q tests/test_compact_source_observation_versions.py tests/test_source_observations.py tests/test_compact_embedding_versions.py tests/test_compact_message_projection.py tests/test_compact_feasibility.py tests/test_rag_compact.py tests/test_native_compact_persistence.py
```

Result: **124 passed, 25 subtests passed**. `git diff --check` passed.
No configured linter/formatter was found in `pyproject.toml`. No background
process was started. The full deterministic suite and native UI contract remain
required before completion; this bounded iteration does not claim completion.

New constraint: the foundation renderer with one phrase and nine typed fields
renders 1,670 characters, exceeding the unchanged 1,600-character summary cap.
Durable dispatch correctly rejects that representation. Fresh integration needs
a compact canonical renderer and deterministic reservations for phrases,
member refs, fields and rendered facts; do not increase caps or truncate fields.
The passing durable fixture retains seven typed fields within the current cap.

Open work: integrate strict v3 fresh model output and catalog selection, reserve
all bounds during complete/atomic packing, fence prepared/pending
representations, exclude historical prose from every active factual/classifier
context, and extend replay compatibility controls. Existing active-context
consumers are not yet v3-ready, so fresh v3 emission must remain disabled until
those consumers are updated. No real-native fidelity or 100K acceptance is
claimed.
