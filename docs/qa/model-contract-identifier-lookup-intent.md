# Bare identifier lookup instruction

The remapper instruction now defines a question consisting only of an exact
identifier as a lookup for observations across the scoped sources and services.
Relevance comes from occurrence in retained source records, without requiring an
extra topic or a connection between service summary topics. Distinct matching
observations remain subject to existing limits. Questions that also request a
topic, action, status, service, environment or period retain that constraint;
literal occurrence alone is insufficient.

This is one instruction clarification. Candidate keys, all five decision labels,
confidence validation, requested-service priority, source fairness, joint literal
matching, scope, citation limits, input/schema budgets, model configuration and
backend guard version are unchanged. There is no intent classifier, forced
positive decision, fallback, response repair or schema change. Every admitted
candidate still requires a model judgement.

## Structural verification

`tests/test_rag_identifier_lookup_intent.py` uses mock HTTP transport through the
native decoder. It verifies that three divergent service messages with one exact
identifier reach the prompt alongside the clarification and three required
candidate keys. A complete response using the three positive labels can select
all three immutable candidates with distinct original citations. A mixed-topic
response can reject the matching but off-topic observations. An absent identifier
supplies no candidates and makes no generation request. Four positive decisions
still fail the existing per-cell cap of three without truncation.

The existing 6,600-character/byte round-robin test uses shorter synthetic repeated
padding to account for the added instruction; its budget, multi-cell admission,
complete-summary assertions and omitted-candidate behavior remain intact. Existing
twelve-memory breadth, source-witness, joint-match, scope and strict validator
tests remain part of the focused checks.

```sh
python -m pytest -q tests/test_rag_identifier_lookup_intent.py \
  tests/test_rag_remapper.py tests/test_rag_exact_remapping.py \
  tests/test_rag_source_diversity.py tests/test_rag_record_view.py \
  tests/test_rag_preserved_context.py tests/test_rag_compact_summary_context.py \
  tests/test_native_preserved_runtime.py
python -m pytest -q
git diff --check
```

## Actual acceptance remains pending

These tests check instruction delivery, candidate availability and strict
selection behavior with synthetic decisions. They do not demonstrate actual
model relevance, recall, source coverage or prose quality. No actual API,
generation, embedding, intake or stored-state proof calls are part of this patch.
The host owns trusted proofs, review and refreezing, subsequent exact/cross-service,
semantic and twelve-memory checks, fresh compaction/prose pilots, full-load and CI
acceptance. GNHF completion is not user release acceptance. Commits are left to
the GNHF orchestrator.
