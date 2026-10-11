# Independent candidate relevance protocol

The active remapper and helpers in `src/logchat/rag/remapper.py` define
`independent-candidate-relevance-v1`, implementation
`bounded-independent-relevance-v1`, and selection policy
`positive-witness-fair-selection-v1`. The active `remap` path starts its absolute
deadline before validation, reserves the complete plan, executes sequentially,
and then selects only validated threshold-passing model positives. The legacy validator is
unchanged: four positives at a three-selection cap fail even below threshold.

`_classification_schema()` and `_validate_classification(value)` require exactly
`label` and `confidence`. Labels are `topic_match`, `exact_identifier`,
`comparison_baseline`, or `not_relevant`. Every confidence must be finite,
numeric, non-Boolean, and between zero and one, including negatives. No address
or selection quota appears in the response. Native decoding remains responsible
for rejecting duplicate JSON keys before dictionary creation.

`_classification_payload` carries the original question, one cell, and one
complete `_candidate_payload` projection at `payload["candidate"]`, including its
opaque `candidate_ref`. Records, column codec, summary warnings, metrics scope,
and matching indexes retain their original spelling. Literal anchors and
requested services must come from the complete scoped pool, never the isolated
candidate. A single observation may supply partial support for a comparison;
requested services are alternatives, while other topic/scope constraints apply.

`_classification_plan` takes the existing witness-ordered pools and reserves
complete requests in stable cell round-robin order, up to six per cell and eight
calls overall. Its returned tuples bind each reference to the immutable candidate
and payload. Selection caps do not change planning. Per-candidate omissions
distinguish input budget from call limit. The caller must separately report
admission omissions beyond six. References can have gaps after omissions.

Returned character and UTF-8 byte counts sum every planned instruction, the
unchanged native safety suffix, compact schema, and compact payload. Each
request is reserved atomically against the existing joint budgets; records and
instructions are never sliced. A budget omission does not prevent a later
smaller complete request from fitting.

`_execute_classification_plan(calls, model, config, deadline=...)` executes a
fully reserved queue through the unchanged `model.generate(instruction, payload,
schema)` seam. The active caller captures an absolute event-loop deadline at query start,
before validation and planning, and passes it here. The executor applies
the earlier of that deadline and its own configured timeout capped at sixty
seconds. Calls are sequential with no retry. External cancellation propagates.
Any attempted provider, validation, output-budget or deadline failure returns
an empty judgement mapping, even after valid earlier positives. The mapping is
classification evidence only; it includes negatives and below-threshold values
and must never be published directly as selected evidence.

The returned detail separates planned, attempted, returned/completed and
validated counts and preserves the ordered per-call ledger on failure. Each
ledger row contains protocol, one-based call index, opaque reference, immutable
cell/chunk/source binding hash, request hash, charged input character/UTF-8
counts, attempt/return/validation flags, strictly validated label/confidence,
safe failure category and elapsed seconds. The request hash is SHA-256 of
UTF-8 `model_json((instruction + safety_suffix, model_json(schema),
model_json(payload)))`. The binding hash uses `model_json([cell_id, chunk_id,
owner_id, project_id, environment_id, source_id])`. Source bodies and raw
exception strings are absent from the ledger. Successful earlier ledger rows
remain diagnostics when the aggregate execution fails, not semantic success.

Native/provider token counts and stop reason are explicitly null because the
existing structured-model return seam does not supply them. Host QA must retain
the actual provider results and independently bind those counts to every
request; null counts cannot satisfy native proof. The executor rechecks aggregate
input reservation before access and enforces an aggregate output character cap.

Focused mocks and synthetic native transports now prove planning, strict
classification, atomic late failure, shared deadline cancellation and ledger
semantics. They do not prove actual relevance quality or token counts. The active route reapplies literal/service/source witness ordering to the
threshold-passing positive subset, then interleaves cells in original order with
hard caps of three per cell and eight total (or lower configured limits).
Confidence is never used to rank candidates. Admission remains at most six per
cell even for larger custom configurations; candidates beyond that bound are
counted as admission omissions and produce cell gaps. Input/call omissions,
model negatives, below-threshold positives, and selection-cap omissions have
separate counts. Gaps identify missing positive requested-service support,
selected-service coverage, uncovered literal alternatives and selection caps.

The active-route regression mocks use flat per-candidate responses with explicit
positive, negative and below-threshold outcomes. Native synthetic transports also
exercise flat-schema validation and duplicate-key rejection after a valid earlier
positive. Legacy capped-validator controls remain separate.

From the repository root, in an environment installed with `pip install -e '.[dev]'`,
run these structural checks without a model server:

```sh
python -m pytest tests/test_rag_independent_classification.py tests/test_rag_remapper.py tests/test_rag_source_diversity.py tests/test_native_preserved_runtime.py -q
python -m pytest -q
python -m build
python scripts/check_release_artifacts.py
python -m pytest tests/test_prepare_public_source.py -q
git diff --check
```

No actual model/provider metadata, tokenizer, embedding, intake or bootstrap calls
are part of this verification. Runtime token fields stay null. Actual exact-lookup,
mixed semantic cross-service and twelve-memory/source-breadth acceptance requires
root-owned per-call provider results, independently measured native token counts
plus the 1200-token output reserve, and proof of the total sixty-second deadline.
Root and QA must independently freeze and review the committed active candidate
before separately authorizing those calls. Fresh compaction fidelity and fully
indexed fresh 100K gates remain separate outstanding acceptance work.
