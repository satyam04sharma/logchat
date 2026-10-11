# 100K pipeline bug report

Current release status: **not ready to publish as a validated 100K demo**. Installed Chrome cycle 3 passed all 20 checks. Fresh three-application pilot cycle 2 captured and indexed all 1,200 events in 44 native memories, with exact canonical ledger reconciliation and restart/replay checks passing. However, its cross-service and exact-incident queries failed strict model-selection validation despite receiving all three relevant candidates. The pilot therefore failed acceptance. Exact response diagnosis, a reviewed correction, a passing fresh pilot and fully indexed 100K rehearsal remain required before GitHub review and merge.

| Finding | Current evidence/status |
| --- | --- |
| Unrelated context for absent identifiers | Corrected; independently reviewed retrieval regressions pass |
| Native source counts report zero | Corrected; installed contracts pass |
| Source starvation and redundant preparation | Corrected candidate; real canonical HTTP400 completes in 184.546 seconds |
| Introduced replay identity regression | Withheld and corrected; historical replay/restart checks pass |
| Diagnostic failures leak the intake slot or replace cancellation | Corrected; fault regressions pass |
| Partial repeated-memory selection | Corrected instruction contract; fresh installed Chrome selects/copies both memories |
| Browser query races independently queued auth intake | QA readiness corrected; fresh cycle passes 20 checks |
| Auth embedding token overflow hidden by generic failure | Corrected; fresh pilot cycle 2 indexes all 400 auth events and all 1,200 total events |
| Repeated incident candidates crowd out a requested service | Corrected candidate; independent crowding, joint-match and comparison-alternative probes pass |
| Incomplete vector/citation acceptance checks | QA corrected; corruption probes reject incomplete evidence |
| Digest ledger only counted, not fully reconciled | QA corrected; all canonical values/scopes/job bindings checked and seven corruption probes rejected |
| Actual multi-service model selection fails validation | New blocking finding in fresh pilot cycle 2; exact response diagnosis in progress |
| Full 100K indexing and context acceptance | Outstanding; admission alone is insufficient |

The sections below preserve the failed cycles and exact diagnoses; earlier intermediate candidates are not release results.

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

## Installed-browser cycle: source readiness race

The first integrated Chrome cycle passed 19 checks, including both-memory paraphrase selection and supporting-memory copy, but failed the auth `dev` query with no candidates. Its early status snapshot showed zero accepted events, offset zero and `intake_busy_retry_required` for that source; final native storage contained both expected `dev` events with capture watching and no gaps. The checker waited for console indexing but assumed all independently contending auth sources were already indexed. The empty response correctly reported unknown behavior at query time; it was not cross-environment leakage or lost capture.

The QA correction waits at most 180 seconds for the exact auth source/environment fixture count, rejects extra events or a reached candidate cap, then runs the unchanged context, source isolation, full selected count and exact duration checks. Before/final status snapshots remain separate. Independent review passed the readiness correction. Failed cycle 1 remains failed; cycle 2 uses fresh owned state. Pilot dataset provenance also now reports the configured event target instead of always reporting 100,000.

## Embedding token context exceeds the character guard

Fresh installed Chrome cycle 2 passed all 20 checks. The subsequent three-application pilot generated and admitted 400 web, 400 worker and 400 auth events. All 1,200 events completed native model summarization with exact event ledgers and zero dropped, expired or rejected logs. Web and worker indexed 800 events in 28 native chunks/vectors; the auth prepared job failed atomically with `embedding_unavailable`, leaving all 400 auth events outside the searchable index. Correct cited web context was retrieved while 400 events remained pending. The failed pilot was stopped and preserved.

The failed auth batch passes compact validation and contains 16 complete memories. Six embedding inputs of about 3.3–3.5K characters exceed the selected nomic model's token context even though they are below the 16,000-character provider guard. The local model advertises `nomic-bert.context_length = 2048`; the real `/api/embed` endpoint rejects those individual inputs and the full batch with HTTP 400 and input-length-exceeded errors while `truncate` is false. This is not missing capture or a character-budget overflow. Generic embedding failure reporting concealed the precise cause.

A read-only actual-model diagnostic removed only `event_refs` bookkeeping from the semantic embedding input, retaining every original selected key/value and exact metric. All 16 inputs then embedded successfully at 768 dimensions without truncation; full catalogs and their reference bindings in durable state were unchanged. This is a diagnostic projection, not an integrated fix or passing pilot. The correction must explicitly bind embedding projection identity, keep existing vectors and canonical replay compatible, preserve every durable source field/ref, expose a safe context-limit failure category, and revalidate the three-application pilot and full 100K cycle.

The harness now stops draining when an embedding job is terminally failed instead of waiting out the drain budget. This does not relax acceptance: a failed or unindexed job still fails the rehearsal.

Independent full-run preflight found another acceptance-checker gap: digest rows were counted without checking every canonical value. The harness now regenerates each synthetic event in memory through the unchanged HTTP parser and checks exact per-source/global digest values, source scopes, target counts and matching job bindings. Seven disposable-copy corruptions are rejected. Read-only reconciliation confirms all 1,200 digests in the preserved failed pilot match; that pilot still fails because 400 auth events remain unindexed. This correction improves the release gate and does not claim an additional runtime data-loss bug.

## Repeated incident candidates crowd out a requested service

The full fixture injects four shared-incident observations per application. Independent structural review constructed twelve valid compact incident memories in memory, four each for web, worker and auth, without changing persisted state or calling a production model. The cross-service question plans one environment/period cell. Ranking lacks service/source diversity; remapping supplies only the first six candidates. An allowed order of four web followed by four worker followed by four auth therefore supplies four web and two worker memories, leaving auth unavailable to any relevance model. The input fits the preserved-content budget, so this is a candidate-limit issue rather than input overflow. Unrelated vector neighbors can also occupy ranking slots before exact-value eligibility is applied.

Acceptance: preserve question-derived exact eligibility, then provide bounded fair service/source representation. Explicitly requested multi-service breadth must guide model selection before repeated observations within one service. Keep model relevance, confidence, scope, citation and input/output limits; do not fabricate selections or silently raise every cap. Insufficient validated coverage must remain explicit partial context and fail the three-service rehearsal gate. Structural tests do not substitute for fresh actual-model retrieval. The one-incident-per-application pilot alone cannot establish this scale behavior.

Review of the first correction found a second false-negative: twelve records containing only one of two requested identifiers consumed the retrieval cap before the single record containing both. Final exact eligibility correctly rejected those partial matches, but the joint record was already lost. The correction must apply the same retained-field, one-record cooccurrence and same-field comparison-alternative rules before the retrieval cap, preserving atomic identifiers and absent-value rejection.

The integrated correction derives shared exact-match rules from the same bounded, scoped and validated backend pools before per-cell ranking limits. Remapping uses the same pure retained-record eligibility helper. Actual eligible field-alternative and requested-service witnesses precede repetitions at both limits; excess witness demand remains an explicit gap. Independent thirteen-record joint-match and late-alternative probes, cross-window/environment comparison checks and the twelve-matching-plus-twelve-neighbor three-service fixture pass. Model selections still require validated membership, confidence and unchanged budgets; no evidence is automatically promoted. The full integrated suite passes 815 tests and 256 subtests, with one opt-in model test skipped and two existing deprecation warnings. Renderer, build, two-artifact structure and 71-file candidate-byte audits pass. Actual model quality remains a simulation gate.

## Reviewed embedding correction and recovery

The integrated candidate uses durable `compact-evidence-v2` to select the refs-only embedding recipe. Historical v1 rendering remains byte-for-byte unchanged. All selected source keys/values, JSON types, summaries, exact metrics, metadata and full reference bindings remain durable; the model digest and document/query prefixes remain unchanged. Recognized bounded provider context errors become the fixed terminal category `embedding_input_context_exceeded`, with no raw provider-body diagnostics or automatic truncation.

Explicit authenticated failed-job retry authorizes a leased v1-to-v2 upgrade. Typed canonical validation permits only the evidence version and derived chunk-ID changes, preserving the initial intake hash and exact canonical key/digest ledgers. Expired/stale/unleased/published work cannot use this path. Independent review also verified that a successful first vector request followed by overflow in a later request publishes zero vectors.

Actual disposable-copy recovery added 16 real 768-dimensional nomic vectors, reaching 1,200 indexed events and 44 vectors across three completed jobs. Independent read-only comparison confirms the original 28 payload/vector pairs, all 1,200 keys and digests, initial hashes and model spec are unchanged. No summary generation occurred. The original failed pilot is preserved. Two earlier disposable attempts recovered successfully but their audit helpers failed while decoding cleared completed-job payloads; all three attempts and their total 48 document-vector requests are disclosed. Fresh generation, actual model relevance and full 100K completion remain separate release gates. Exact tokenizer preflight is deferred; arbitrary oversized retained semantic inputs still fail closed.


## Fresh pilot cycle 2: complete indexing, invalid multi-service selection

The rebuilt installed candidate passed 815 tests and 256 subtests, with one opt-in model test skipped, plus the installed 28 CLI/adapter contracts and all 20 fresh Chrome checks. The fresh three-producer pilot then generated and captured exactly 1,200 unique synthetic events, 400 per application, and published 44 native chunks with 44 corresponding 768-dimensional vectors. All three jobs completed; pending, failed, dropped and expired event counts were zero. Every canonical event key and content digest, per-source event ID, status count and duration total reconciled. Original-credential restart and replay passed, including 48-event deduplication and source-auth isolation.

Observed indexing took 593.770 seconds from first smoke submission, including polling and the pending-drain query. Validation took 820.651 seconds before cleanup. These are shared-localhost observations, not production throughput estimates. Owned producers and the owned server stopped after reporting.

Fifteen of the seventeen final query checks passed. The pending-drain web query also passed while 400 events remained queued. The cross-service incident question and exact incident identifier both failed with `remapper_invalid_model_selection`. Each actual request supplied all three eligible service memories within the existing input budget, but no model selection survived strict validation. Candidate availability therefore does not establish usable multi-service context. The failed pilot remains failed; storage completeness does not override the retrieval gate.

Static review narrows the remaining failure to the model-selection response contract, but does not establish its exact cause. A bounded read-only actual-model probe must record a content-free validation reason before choosing a correction. Preserve strict confidence, reference uniqueness, cell membership, scope, citation and budget checks; do not promote candidates or weaken acceptance to hide the failure. A fresh full simulation remains required after correction.
