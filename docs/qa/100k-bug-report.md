# 100K pipeline bug report

The native candidate at `1542801` admitted 100,000 synthetic Loghub-template replicas across three independent simulators, but its first 400-event compaction claim failed and none of the burst became searchable. A smaller 288-event run indexed successfully and returned cited context across all three services. The following findings block the requested release acceptance.

## Absent identifiers return unrelated evidence

Querying `QUANTUM_NONEXISTENT_92731` returned a web incident even though the full identifier occurred nowhere in the 49 indexed chunks. Lexical search returned zero hits; four vector neighbors were admitted and supplied to Mistral. The model selected an unrelated candidate with confidence 1.0.

`remapper._exact_value_scope` discovers literal constraints from values already present in candidates. A wholly absent requested value therefore produces no constraint. `retriever._identifiers` recognizes the token, but ranking uses it to admit exact hits rather than reject mismatches. Structural citation checks cannot reject irrelevant real evidence.

Acceptance: exact-value intent comes from the question, including absent values. Known, missing, prefix, and multiple identifier cases remain correctly scoped; unrelated high-confidence model selections cannot bypass exact constraints. Semantic paraphrases and ordinary uppercase topic words continue working.

## Dense queued compaction does not complete

With explicit 512 MiB temporary-original retention, three producers admitted 33,334 web, 33,333 worker and 33,333 auth events in approximately 16 seconds. The first claim contained 400 events. At 301.62 seconds after first admission, it deferred with `compact_intake_unavailable_retry_required`; zero events were indexed and all 100,000 remained pending. Timing is consistent with the 300-second native intake budget, but the generic category does not independently establish the internal exception.

`CaptureSpool.claim` defaults to 400 events. Preserved preparation creates per-event chunks; grouping considers bounded pages and compact summarization can make many model calls. A successful 48-event smoke took 118.453 seconds. A single global intake slot makes repeated model work a bottleneck. Merely raising timeouts or retaining logs longer does not establish sustainable processing.

Acceptance: repeated events can be prepared without redundant inference while preserving exact metrics, unique event accounting, scoped correlation fields, provenance and rare failures. No template-only alternate pipeline, fake vectors, skipped model stages or direct SQL ingestion. A real 400-event claim completes, and a fresh 100K workload fully summarizes/embeds with zero pending or failed work before claiming completion. Replays do not inflate counts; restart preserves progress.

## Concurrent sources can starve

The successful web producer reacquired the process-wide semaphore while the other producers repeatedly received `intake_busy_retry_required`. Completing the representative three-source test required yielding owned producers. Fairness must not require manual pauses.

Acceptance: concurrent sources make bounded progress under contention, raw intake remains bounded, retry failures remain explicit, and settings changes cannot overlap unsafe model preparation. Verify simultaneous capture and retrieval as well as exact per-source final totals.

## Multi-memory browser recall gate remains unmet

Installed Chrome passed 18 checks and failed one: a paraphrase selected only one of two fresh, indexed browser observations. Both were admitted to model input with no candidate or budget omissions. They describe repeated instances of the same symptom; the model currently permits a useful subset. This is partial recall, not lost intake.

Acceptance: define the intended repeated-observation selection contract, preserve model validation and unrelated-negative rejection, and verify the actual reader can select/copy all relevant supporting memories. Do not simply lower the check or promote all candidates without validation.

## Native per-source count was misleading

`LocalStore.list_sources` counted the historical `summaries` table rather than published native `rag_chunks`, reporting zero despite searchable native memories. The candidate now counts native chunks with owner/project/environment/source scope, reports historical counts separately and excludes queued/raw originals. Three regressions and a rebuilt installed-wheel check pass.

## Reproduction and release gate

Use `scripts/qa_100k.py` and `examples/loadtest/README.md` with fresh state and the explicitly selected Mistral 7B/nomic-embed-text profile. Replication uses 151 sanitized Loghub templates weighted by 6,000 sampled rows, not 100,000 unique original dataset rows. See [Loghub](https://github.com/logpai/loghub) for dataset attribution.

After fixes, run the regression suite, installed-wheel browser checks, fresh concurrent 100K ingestion/indexing, incident/paraphrase/negative/scope/citation checks and restart/replay. Preserve failed cycles as evidence; success requires exact generated, admitted and indexed totals. Keep the native service on loopback and leave unrelated services intact.

## Correction cycle status

The release branch contains independently reviewed corrections for absent-ID eligibility, repeated-observation relevance instructions, native source counts, source-fairness admission and capture rotation, and transient exact-message grouping cohorts. Retrieval review also caught and corrected atomic email parsing, semantic version/HTTP-status filtering, and trailing question-colon handling. Focused regressions cover these cases; actual browser/model selection remains a separate gate.

After integration, 648 tests and 244 subtests passed with one opt-in model test skipped. The native renderer, build, two-artifact structure audit, 71-file installed-wheel byte audit, and 28 CLI/adapter contracts passed. These checks do not establish 100K completion.

A fresh real Mistral 7B/nomic-embed-text check sent 400 unique web events with six distinct messages and one rare incident through native summary-only HTTP intake. It failed after 300.052 seconds with `compact_intake_unavailable_retry_required`, phase `intake`. Indexed events, vectors, event keys, event digests and scheduled jobs were all zero. The owned server was stopped. The initial fixture setup omitted the required environment and stopped before ingestion; that setup error was corrected in fresh state before this measured run.

This cycle confirms that the grouping optimization alone has not resolved native preparation throughput. The release remains blocked pending a verified preparation correction, actual 400-event completion, installed-browser recall, and a fresh fully indexed 100K cycle. The acceptance harness now checks every expected synthetic event ID and source-ledger key, exact per-source durations/status counts, no pending or failed work, three-service incident context, negative/scope/auth checks, and capture restart/replay. Its accounting gate independently rejected disposable copies with a missing event or changed duration total.

## Native normalization prevents repeated-message reuse

Independent boundary reproduction sent the unchanged 400-event web fixture through `connectors.local.parse_event(..., preserve_fields=True)`. Six distinct input messages became 400 distinct normalized messages. `event_id` is not in the parser's known field set, so every unique ID is appended in the `Structured fields` suffix despite already being preserved in authoritative event metadata. Exact-message cohorts consequently cannot reuse the grouping work observed in simpler `LogEvent` fixtures. A fresh instrumented model run reached more than 30 grouping calls before compaction.

Acceptance: eliminate redundant attribution from the model projection without losing exact event IDs, arbitrary source fields, correlation identifiers or rare failures. Verify the actual push parser/HTTP boundary, not only direct fixture objects, and repeat the real-model 400-event and full 100K gates.

## Acceptance checker review corrections

Independent review exposed two false-acceptance gaps in the rehearsal itself: event accounting could pass with all vectors removed, and selected evidence could pass with only a partial actual citation list. The harness now requires an exact chunk/vector row-ID, scope and time binding plus full-text row completeness, and actual citation coverage with source/window binding and inline references. A disposable backup with zero vectors and JSON copies with partial, wrong-source or missing-inline citations all fail the corrected checks.

A proposed diagnostic change was separately withheld from the release branch after review found that logging-handler failure could replace cancellation, and diagnostics construction failure could leak the intake slot. The delegated runtime correction must make diagnostics nonfatal and preserve release/cancellation guarantees before integration.

## Normalization upgrade replay regression

A disposable copy of the earlier indexed native corpus reproduced an introduced compatibility bug in the proposed parser correction: replaying the exact unchanged incident payload with the previous parser produces the stored content digest, while the normalized candidate produces a different digest and raises `event_identity_content_conflict`. Content identity includes normalized message and fingerprint; inferred fingerprints can also change. Fresh-candidate replay tests alone miss this upgrade case. The identity-changing parser patch was withheld. The integrated correction restores canonical parser behavior and separates trusted transient model projections from replay identity, preserving changed-content rejection and keeping provenance outside durable compact state.

## Canonical restoration and projection review

The delegated correction restored the original parser. Frozen prior-parser fixtures, three previously indexed incidents, and disposable-state HTTP replay/restart checks in both intake modes pass: unchanged bodies deduplicate without generation, while changed content still conflicts. The delegated suite passed 675 tests with one opt-in model test skipped. These changes remain outside the release branch until the throughput correction is independently reviewed.

Architecture review identified why reducing compact request metadata alone is insufficient: cohort equality and grouping requests still use the ID-bearing canonical message. A producer can also supply text indistinguishable from a normalizer-added suffix, so stripping matching suffix text without origin evidence can silently change legitimate model input. The correction needs trusted normalizer provenance and a temporary validated grouping view, with full-message fallback for direct callers, old queued rows, and stale attribution. Canonical messages, fingerprints, digests, field catalogs and event references must remain exact. Older queued records lacking origin provenance remain replay-compatible but are not evidence of improved drain performance.

The acceptance harness additionally samples ordinary event IDs near the beginning, middle and end of every application corpus. Incident checks alone cannot establish that normal events are searchable. These finite retrieval checks report selected-memory relevance and coverage; prose entailment and broader accuracy require separate review.

## Integrated canonical preparation candidate

Independent reviews passed the origin boundary, grouping, compact feasibility and compact message projection. The integrated suite passed 756 tests and 244 subtests, with one opt-in model test skipped and two existing Typer/Click deprecation warnings. Native renderer contracts, both distribution audits, the 71-file wheel byte audit and 28 installed CLI/adapter contracts passed. Diagnostic fault coverage confirms cancellation and slot release survive diagnostic construction/logging failures.

The fresh final-code canonical HTTP400 check completed intake in 184.546 seconds: grouping took 7.896 seconds with five calls, and compaction took 176.587 seconds with 13 calls. The 13 resulting native chunks/vectors represent all 400 current events, exact metadata/digests, 25,790 ms aggregate measured duration, 399 status-200 events and one status-504 event. Rare fields, scheduler keys/digests, one completed job and actual 768-dimensional embedding reconciliation passed. An independent index audit also confirmed exact chunk/vector scope and time bindings and full-text completeness. The original read-only audit hit assertion errors; recovery checked the same indexed state without regenerating the corpus.

Authenticated unchanged replay deduplicated all 400 events across two server starts, six changed-body variants conflicted, and generation/embedding counters stayed zero. This isolated proof temporarily replaced the source credential because the original returned token was not retained; the original hash was restored, and all owned servers were stopped. The full rehearsal must verify original-credential restart/replay independently. Historical compact timing comparisons use different parser behavior and section counts on shared localhost and are diagnostic only.

This candidate is ready for the installed-browser and three-application pilot gates. The fresh fully indexed 100K rehearsal, finite context checks, GitHub bot review, required CI and merge remain outstanding.
