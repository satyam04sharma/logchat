# 100K pipeline bug report

Current release status: **not ready to publish as a validated 100K demo**. Historical pilot cycle 2 captured/indexed all 1,200 events but failed multi-service selection. The reviewed independent-classification correction now passes actual exact, cross-service and semantic proofs, plus a fresh twelve-memory HTTP breadth cycle with restart/replay and complete-input/provider-token reconciliation. Earlier installed Chrome checks pass, but fresh pilot cycle 3 exposes unsupported model-written operation counts in published and returned context. The first installed Chrome cycle with the source-backed correction fails compaction for some actual batches (10 checks pass, 9 fail). The reviewed lexical correction is integrated. An instrumented fresh native 1,200-event pilot still fails: duplicate model selections also copy a complete protected visible body. A safe catalog correction remains inactive after its first activation trial failed regression checks. A separate test-report provenance flaw is being corrected. A newly successful native pilot, installed browser cycle and fully indexed 100K rehearsal remain required before GitHub review and merge.

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
| Fresh breadth checker compares incompatible window shapes | Corrected with strict canonical times and half-open marker checks; ten corruption controls and a fresh complete native breadth cycle pass |
| Digest ledger only counted, not fully reconciled | QA corrected; all canonical values/scopes/job bindings checked and seven corruption probes rejected |
| Actual multi-service model selection fails validation | Duplicate references and contradictory decisions corrected structurally; full-input proofs now expose a separate order-sensitive auth relevance omission |
| Summary metric attribution and unsupported source adjacency | Per-record measurements corrected; fresh cycle 3 confirms remaining unsupported operation-count prose reaches returned context |
| Model-written operation counts contradict exact records | Fresh cycle 3 failed; reviewed versioned source-observation correction passes structural controls, fresh actual fidelity proof pending |
| Relevance prompts silently truncated by the native provider | Reviewed lossless transport and native guard pass actual overflow rejection and exact full-input/provider-token reconciliation for measured requests |
| Joint relevance decisions omit an eligible source | Historical full-input exact lookup fails twice with auth rejected. Reviewed independent classification now passes actual exact/cross-service/semantic proofs; fresh-workload acceptance remains pending |
| Source-backed facts escape a narrow query window | Reviewed per-cell correction passes independent reproduction and outside-ID zero-admission controls; fresh actual scoped proof pending |
| Fresh installed source-observation compaction rejects actual batches | New blocking failure: console HTTP503 `compact_invalid_output`, auth development 0/2 indexed; exact validator diagnosis pending |
| Valid preview context rejected before classification | Reviewed lexical correction integrated; 39 new controls pass, fresh actual installed/query proof remains pending |
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


## Actual selection diagnosis and incomplete correction proof

Two bounded actual model probes reconstructed the original failed requests without intake or regeneration. Both returned three selections with two unique references, one duplicate and valid confidence values. Strict validation rejected them with `selection_membership_violation`. A third diagnostic changed only candidate reference prefixes and the instruction example; it still returned a duplicate. Namespace ambiguity is present, but prefix changes alone are insufficient. No invalid selections were repaired or accepted.

The delegated candidate adds strict duplicate JSON-key rejection before parsing and a required, independently addressed judgement for each admitted candidate. Independent reviews pass key membership, reason/boolean/confidence checks, caps before confidence filtering, joint schema/input budgets, immutable record references and existing literal/source-fairness rules. The candidate suite passes 836 tests and 273 subtests, with one opt-in model test skipped.

Actual native exact-incident retrieval then selected three distinct services with complete citations on a disposable copy of the preserved index. The cross-service question still failed `invalid_model_selection` with three admitted candidates. The first proof did not capture the underlying rejection category. A later verified diagnostic confirms incompatible judgement reasons and selection flags, as described below. This remains a partial correction, not a passing pilot. That proof consumed three query generations: one harness citation-key error, then the two measured queries. Failed artifacts remain preserved; the original index files are unchanged.

## Compaction discards per-event measurement associations

Independent prose review found that the selected web summary assigns HTTP 504 to repeated forbidden-directory-index observations. Exact preserved data shows one separate payment incident at 504 and 32 directory-index records at 200. Storage, metric counts and canonical digests are correct; the wrong association is already present in the durable model summary and is quoted in the answer.

`_compact_request` previously supplied per-record refs/messages but only whole-section status counts, duration totals and first/last window timestamps. This removes the authoritative message-to-measurement association from model input. The reviewed correction restores each record's exact status, duration and timestamp in the transient model request, using the identical bounded request in preflight. Canonical storage, replay digests and embedding projection remain unchanged. Actual corrected prose must still be tested.

A sampled auth summary also asserts “30 consecutive” events, although 24 source events intervene between its first and last grouped records. Its 1,527 ms summed work is distinct from the 53 ms observed span. Semantic grouping does not establish source adjacency. Instructions now prohibit that inference; tests and actual separated-run prose checks must preserve the distinction.

Acceptance includes fresh actual mixed-status and separated-auth compaction checks, explicit unverified-summary labeling and exact selected-section status distribution beside readable metrics. Disclosure does not cure contradictory prose, and structural evidence selection cannot establish entailment. The fresh pilot and 100K release gates remain unchanged.


## Fixed-key judgement compatibility failure

The first follow-up diagnostic consumed one model call but failed to install its validator wrapper. It therefore established no exact internal reason and remains failed evidence. The host stopped that GNHF run before further work. A separate QA-only probe preserved the failed helper and report, verified wrapper installation and exception rethrowing across eight controlled cases without network/model calls, then ran one authorized actual query on a new disposable copy.

That verified call invoked the original validator once. It returned three judgement entries for three admitted candidates, with zero missing or extra keys, no field-shape mismatches and valid numeric confidence values. Two judgement reasons were incompatible with their selected flags. The strict validator rejected the response with `selection_membership_violation`; zero evidence was selected. The fixed-key schema permits independently valid booleans and reason enums whose combinations violate the later contract. This is an exact confirmed response-contract failure, separate from the earlier duplicate references.

The next delegated correction must remove or structurally prevent those contradictory combinations. It must preserve model-driven relevance, exact candidate membership, strict JSON-key rejection, confidence validation on every decision, selection caps before confidence filtering, citation/scope checks and the joint input/schema budgets. Invalid old responses cannot be repaired or promoted. Actual exact-incident and cross-service proofs, corrected fresh prose checks, a fresh pilot and the full 100K cycle remain required.

At this point the GNHF/verified follow-up phase has consumed five query generation calls; the three earlier original/prefix diagnostics are additional. These counts include both harness failures. No fresh compaction proof attempts have yet been consumed. Original failed state and diagnostic artifacts remain unchanged.


## Native relevance prompt truncation hides candidate evidence

Independent correlation of content-free local Ollama server warnings with the actual probe windows confirms prompt truncation. The exact-incident recheck supplied 8,811 tokens, the cross-service and verified diagnostic supplied 8,839 tokens, and the later label-contract query supplied 8,815 tokens. Each warning reports truncation to 4,099 tokens. Inspection of the installed Ollama 0.34.4 source shows that the requested 8,192-token window is honored: these full prompts exceed its 8,191-token input threshold, triggering a half-window shrink to 4,099. The smaller number is the result of overflow handling, not the loaded model capacity. Their completion timestamps and provider durations match the corresponding proof calls. The apparent exact-incident selection success therefore does not establish that all encoded evidence reached inference intact.

The adapter requests `num_ctx=8192`; the model advertises a 32,768-token context and the running-model endpoint reports 8,192. These declarations do not override the observed truncation. Character/byte admission limits and a provider `prompt_eval_count` of 4,099 cannot establish full token fit. The request contains complete candidates, but the runner discards part of it before generating a schema-valid answer.

This input-loss finding can undermine the model's relevance decisions. It does not prove that truncation alone caused every malformed reference or contradictory decision. The strict response-contract corrections remain independently useful, while their actual quality proof remains incomplete.

The next correction must reduce the model-only relevance representation without losing typed source fields, event IDs, record bindings, timestamps, measurements or semantic-query records. Lossless columnar records and invariant metadata are candidate approaches. Durable compact records, canonical replay, vector recipe, scope/citation validation and selection limits remain unchanged. Revalidate actual exact, cross-service and semantic requests, with full-fit evidence rather than blind context/model/budget increases. Fresh compaction prose must also be checked for the same class of input loss.

The final label-contract proof consumed one generation before its checker looked for confidence at the wrong response level; confidence belongs to row revalidation entries. It remains failed evidence. The model-contract phase now comprises six worker/verified query generations plus three earlier original/prefix diagnostics. No fresh compaction proof attempts have yet run. Query proof setup is being independently preflighted before further calls.


## Lossless projection and native acceptance guard follow-up

The delegated correction replaces the redundant selected-boolean/reason combination with one required label and confidence per candidate. Selection follows the declared label contract; invalid old responses remain rejected. Independent structural controls pass, but the actual proof is still incomplete. Its failed helper and original index remain preserved.

A model-only record projection now hoists exactly identical typed values and carries all varying values in ordered rows. Its inverse crosses the real JSON boundary and restores all fields and records, including the 33-record semantic fixture. It preserves booleans versus integers, signed floating zero, missing versus null, nested Unicode, source field names, record order and citation binding. The candidate full suite passes 856 tests with one opt-in model test skipped. This establishes reversibility, not token fit or actual retrieval quality.

The remaining adapter work must use verified provider behavior to reject oversized input rather than silently trim it. On the inspected native version, `truncate:true` invokes the provider's exact tokenizer and `shift:false` disables the truncation branch; an overfull prompt then fails before generation. The conservative version policy, bounded error handling and compact transport must be independently reviewed. Native QA will verify the full rendered prompt plus the 1,200-token output reserve fits the unchanged 8,192-token window. No context, model or selection limits are increased.

The host independently reran the corrected query helper's offline preflight: 12 validator cases and 20 API-shape/citation/quality cases passed with zero network or model calls. Fresh native exact/cross-service/semantic and twelve-memory breadth proofs, corrected prose, installed browser checks, the complete fresh pilot and fully indexed 100K cycle remain outstanding. No publication go-ahead or GitHub merge is authorized by these intermediate test results.


## Review caught a native guard routing gap

Independent review of the initial context-guard candidate found that GGUF/completion/empty-history metadata also permits a model with nonempty `remote_host` and `remote_model`. Ollama's inspected generation handler proxies such models before scheduling the verified local runner, bypassing the context guard. Thus a configured loopback endpoint alone does not establish local execution or input preservation.

The candidate is withheld pending a minimal correction: reject either remote-routing field when nonempty or malformed before generation, and prove this with both-field, single-field and between-call model-metadata changes. No remote requests or actual native proof calls were made during this review. The projection and compact transport remain independently valid; passing tests on the initial guard does not override this finding.


## Reviewed guard and native proof setup status

The routing repair now rejects either remote field unless absent or exactly empty. Independent review passes 197 guard tests plus 23 separate transport controls; the host's focused checks pass 265 tests and 120 subtests. The final candidate suite passes 1,054 tests and 316 subtests with one opt-in model test skipped. These establish bounded transport behavior, not actual retrieval acceptance.

Two synthetic native overflow preflights stopped before completion because their QA helper expected a `sha256:` prefix in running-model metadata. The inspected API returns the bare 64-character hexadecimal digest; it exactly matches the manifest file hash, and the loaded context is 8,192. Each attempt performed one render-only request, with no tokenization or completion request. The first helper recorded only its error category; a preserved diagnostic attempt established the exact identity-format mismatch. An independent process check verified the expected model runner.

This is a QA identity-representation bug, not a passing overflow control or a runtime-model failure. Correct the checker against the actual API shape, retain strict exact digest matching and all failed artifacts, then refreeze the changed helper before further native proofs. Model-contract query generations remain nine, and fresh compaction proof attempts remain zero. Full fresh simulation, 100K indexing and GitHub release acceptance remain outstanding.


## Native input-loss correction passes; exact lookup still omits auth

After the strict digest-format QA repair and a fresh immutable proof freeze, the synthetic native control measured 9,037 full input tokens against the unchanged 8,192 context. The actual completion endpoint rejected it with the fixed context-limit category; no generated result was accepted. Four render-only requests and one successful query completion, plus the rejected completion, appear in the inspected provider-log window. No input-prompt or input-message truncation warnings occurred. These temporal shared-provider records supplement, rather than replace, the exact request/token checks.

The fresh exact `INC_CORRELATION_73` query then measured 5,655 rendered input tokens plus the unchanged 1,200-token output reserve. The complete system instruction and serialized candidate payload survived rendering. The provider reported exactly 5,655 input tokens and normal stop termination, so this result is not attributed to input truncation. All three eligible memories were admitted without omission. The model returned three valid, uniquely addressed judgements with valid confidence values: two positive and one negative. The retained diagnostic records label counts, not the negative label's exact identity, so its internal rationale is unknown.

Strict selection, confidence, citation, scope, canonical bindings and provider counter checks passed. The selected services were web and worker; auth was missing. Selected precision was 1.0, while this finite fixture's recall and source coverage were both 2/3. The quality gate correctly failed. Canonical chunks, coverage and event ledgers, the original index and previous artifacts remained unchanged.

This establishes the transport correction for the measured cases and a distinct remaining model relevance omission. It does not establish passing retrieval, global recall or prose entailment. No fresh cross-service generation followed the failed exact query. Model-contract query generations now total ten, including the earlier failed helpers; the additional overflow endpoint was rejected and is accounted separately. Fresh compaction proofs remain zero. Diagnose the omitted-source decision and preserve the unchanged acceptance gates before any new simulation or publication claim.


## Bare-identifier clarification did not restore actual auth recall

Offline reconstruction reproduced the first full-fit request's exact hash. Worker, auth and web candidates contain one, one and 33 records respectively; each has the incident identifier at retained record index zero, and all three fit the three-selection cap. The initial prompt did not explicitly define a bare identifier's topic as lookup of that identifier across scoped sources.

The minimal reviewed clarification adds that definition while retaining additional topic/action/status/service/environment/period constraints for mixed questions. No classifier, forced positive decision, response repair, scope relaxation or budget increase was added. Four new structural regressions and the full 1,058-test suite pass. The host separately passes 52 tests and 123 subtests for intent, exact remapping and existing strict gates.

Its actual exact query still fails. The complete 5,756-token prompt plus 1,200-token reserve fits, provider input counts match exactly, and normal stop/strict labels/citations/canonical checks pass. The safe decision audit now records worker as `topic_match`, auth as `not_relevant`, and web as `topic_match`, all at uncalibrated confidence 1.0. Selected precision remains 1.0 and finite recall/source coverage remain 2/3. The explanation gap was real, but this clarification is insufficient to establish model quality.

This failed proof is retained. Model-contract query generations now total eleven; fresh compaction proof attempts remain zero. Escalated review must distinguish bulk/position effects from semantic misclassification with a bounded, preflighted diagnostic before choosing a further correction. Repeating the unchanged query, accepting two-service coverage or auto-promoting the rejected candidate would not satisfy release acceptance.


## One-shot rotation diagnostic: order-sensitive joint relevance

One bounded diagnostic rotated the same complete candidates from worker/auth/web to auth/web/worker and reassigned the opaque sequential references. Offline reversal reproduced every original evidence byte; question, instructions, schema, limits, measurements and model options were unchanged. The source/native executable freeze was independently verified before execution.

The rotated request again measured 5,756 input tokens, matched the provider count exactly and stopped normally. Auth, web and worker all received valid positive `topic_match` judgements at uncalibrated confidence 1.0. Original files remained unchanged. This supports order/reference-sensitive joint decisions for this fixture; it does not prove a universal middle-address bug or reliable retrieval. It is a direct diagnostic, not a fresh API-context or simulation pass. Relevance generations now total twelve.

The reviewed next correction separates independent relevance classification from final selection. A new flat label/confidence contract will evaluate one complete candidate per call, then select only validated threshold-passing positives with existing literal/service/source fairness. The legacy capped selection validator remains unchanged. The new policy must retain six admitted candidates per cell, three selections per cell/eight total, an explicit eight-call ceiling, aggregate existing input/output budgets and one shared 60-second deadline. Any attempted-call failure must publish no selections, and deterministic planned omissions must remain explicit partial gaps. No negative may be promoted, and no favorable shuffle is used as the release fix.

Implementation, independent review and multi-call native proof are pending. Fresh summary fidelity, installed browser, fully reconciled pilot/100K, GitHub bots and merge remain required.

## Active independent-classification candidate

The delegated candidate now routes the app through single-candidate classification and final selection of validated, threshold-passing positives. Independent architecture review passes for the active route. It retains strict legacy selection validation, complete evidence projections, fair service/source exposure, a six-candidate admission cap, three selections per cell/eight total, eight classification calls maximum, aggregate input/output budgets and one deadline beginning before eligibility/planning. Late attempted-call failures publish zero selections. Native counters remain independently measured by QA, rather than invented in runtime provenance.

The full candidate suite passes 1,088 tests and 363 subtests with one opt-in native test skipped. The host separately passes 85 tests and 172 subtests for active remapping, independent classification, source diversity and identifier intent. Build, distribution and 73-file source-byte checks pass. Versioned offline proof helpers pass 19 rejection controls, six saved API evaluations and the twelve-memory fixture controls. These are structural results; no native classification acceptance or fresh compaction proof has run for this candidate.

A publication review also caught the historical 4,099-token exact result still labeled as passed in its archived document. That apparent result was based on confirmed truncated input and cannot establish complete-input relevance acceptance. Its historical measurements and failed proofs must remain preserved with a prominent superseded annotation. Actual exact, cross-service and semantic multi-call proofs, fresh prose/pilot/100K, installed checks and GitHub review remain required.

## Actual independent-classification query proofs

The reviewed runtime passes three sequential actual proofs on byte-identical disposable copies of the historical 1,200-event index. Original state, canonical chunks and prior evidence hashes remain unchanged. Full provider responses are retained privately before decoding; public proof records contain only safe counters, decisions and hashes.

| Query | Classification calls | Complete input tokens / matching provider counts | Classification wall seconds | Result |
| --- | ---: | --- | ---: | --- |
| Exact incident | 3 | 1,297; 1,271; 4,263 | 21.524 | Auth, web and worker selected; all gates pass |
| Cross-service incident | 3 | 1,322; 1,296; 4,288 | 17.660 | Auth, web and worker selected; all gates pass |
| Semantic paraphrase, web | 1 | 4,179 | 11.813 | Relevant web observation selected; all gates pass |

Every complete prompt plus the unchanged 1,200-token output reserve fits the 8,192-token context. Provider counters match exact native tokenization and all calls terminate with `stop`. Strict labels/confidence, immutable caller bindings, original citations, scope, aggregate budgets, hard caps and the shared deadline pass. Only model-positive evidence is selected. These finite fixture results establish the measured relevance correction; they do not establish fresh compaction prose, global recall, production throughput or 100K completion. Relevance-proof generations now total 19, including all preserved earlier failed/diagnostic calls. The separate fresh twelve-memory HTTP breadth proof is underway.

## Fresh breadth cycle: canonical window checker mismatch

The fresh HTTP fixture indexed all twelve one-event memories, twelve completed jobs and twelve bound 768-dimensional vectors with exact IDs, canonical keys/digests, source scopes, status 504 and duration 5,000 ms. Its first exact query classified five complete candidates under the aggregate budget and selected three distinct services. Precision and source coverage were 1.0; bounded selection recall was 3/12. All five provider input counts matched native tokenization, stop termination and strict decisions; classification took 13.719 seconds.

The overall proof still failed because its compound canonical check compared the API window dictionary directly with the stored window dictionary. Canonical start/end match exactly; the API also includes the established `bounds: "[start,end)"` marker. Summary, chunk IDs, service and project/environment/source scopes all match. This is a checker shape bug, not a changed source window. The correction must require exact canonical timestamps, the half-open marker and the expected API shape, with shifted/missing/extra/wrong-bound controls; it must not remove temporal checks.

The failed whole run remains preserved, and the owned server stopped. All 64 independently snapshotted original state and historical evidence files remain unchanged. It consumed 24 native intake generations and five relevance generations, accounted separately; the relevance-proof total is now 24. Cross-service/restart/replay for this fresh fixture did not run after the failure. A fresh full breadth cycle is required after reviewed QA correction; fresh prose/pilot/100K and publication gates remain outstanding.

## Fresh breadth cycle 2 passes

After the strict checker repair and ten offline corruptions, a new owned HTTP cycle with new project, source credentials and state passes all gates. It indexes twelve distinct one-event memories, completed jobs and real 768-dimensional vectors, four per service. Exact canonical event IDs, keys, digests, measurement values, scopes, windows and vector/full-text bindings reconcile. Exact and cross-service queries each select three distinct services with valid original citations. Their bounded precision and source coverage are 1.0; selected recall is honestly 3/12 under the three-selection cap.

Each query performs five independently validated classifications under the existing aggregate input budget. All complete prompts plus the output reserve fit, provider token counts match and calls stop normally. Classification wall times are 13.809 and 13.757 seconds; unclassified and selection-cap omissions remain explicit partial gaps. Restart with the original credentials preserves the canonical index, and replay deduplicates one original event without new intake generation. The owned server stops, and all 121 separately snapshotted historical/state files remain unchanged.

This cycle consumes 24 native intake generations and ten relevance calls. The earlier failed cycle's 24 intake and five relevance calls remain counted separately; the relevance-proof phase now totals 34. This establishes fresh finite ingestion and breadth, not mixed-record summary fidelity or 100K acceptance. The installed wheel matches all 73 runtime/resource files and passes 28 CLI/adapter contracts; its fresh Chrome cycle, full pilot/prose and 100K gates remain required.


## Fresh pilot cycle 3: unsupported operation counts

The fresh 1,200-event native simulation captures three concurrent applications through normal HTTP intake. Its first 400-event web preparation produces 13 compact chunks with exact event references, canonical digests, metadata and selected-field bindings. All 13 publish without changing their prepared hashes. These structural checks do not establish prose fidelity.

Independent source comparison finds concrete contradictions:

- A 35-record section contains 11 worker initialization observations and 24 module initialization observations. Its generated summary claims 17 and 32, totaling 49.
- Another 35-record section contains 24 and 11 observations respectively; its summary claims 35 and 10.
- The mandatory mixed section contains one payment incident with status 504 and duration 5,000 ms, plus 32 directory observations with status 200. The corrected measurement input binds the incident correctly, but prose describes only 30 directory observations. The query issued during drain returns that unsupported count in its selected context.

The compact request already includes each record's timestamp, status and duration, complete projected source text, exact aggregate metrics and observed span. The output contract validates unrestricted summary and uncertainty text for shape and length; it does not validate the propositions within that prose. Exact aggregate rendering and an unverified label therefore coexist with contradictory semantic counts. Another prompt reminder is insufficient.

Acceptance: actual model compaction selects bounded, exact source evidence; code derives complete occurrence membership and renders counts, status, measured work and observed span from those members. The model may not write independent numerical, adjacency or causal claims. Preserve arbitrary typed fields, canonical identity, complete packing, original-message privacy, provider bounds and actual model stages. Version new durable semantics explicitly, keep historical identities and embedding recipes immutable, and exclude unchecked historical prose from active factual context. Require independent deterministic review, then fresh native 1,200 and fully indexed 100K cycles with useful cited context. Preserve this failed cycle unchanged.


Cycle 3 completed with 1,200/1,200 unique events, 400 per application, 44 complete finite native vectors, three completed jobs, and zero pending, dropped, expired or failed events. All canonical IDs, digests, status/duration totals, source scopes and ledger/job bindings reconcile. All 17 final retrieval checks and the query during drain pass their scope/citation/selection gates; restart and original-credential replay deduplication pass. Indexing took an observed 833.431 seconds; validation took 982.992 seconds. Owned processes were stopped. These results establish structural accounting and bounded retrieval, not source-prose fidelity.

The complete independent prose audit also finds unsupported counts across mixed auth sections: one 29-record section claims 29 authentication failures plus 29 reverse checks, whereas its source contains 19 and 10. Another implies 29 paired failures/checks where the distinct records contain 10 and 19. The whole-cycle release verdict is therefore **FAIL**, despite the structural rehearsal's acceptance flag. A fresh successful source-backed prose cycle and fully indexed 100K cycle remain mandatory.


## Withheld source-observation foundation: fragment reconstruction

Independent review of the inactive replacement protocol found a privacy regression before integration. Its phrase validator rejects individually complete bodies and unions exact covered character positions, but requires interior whitespace to be covered too. Selecting `auth` from one source and `operation` from another is accepted even when a third protected body is exactly `auth operation`; joining the retained phrases with a space reconstructs that complete body. The foundation remains withheld.

Acceptance: reconstruction exclusion applies across all protected current bodies and selected fragments, including non-witness records and whitespace-only gaps; add space, tab, newline and conservative separator controls without changing exact phrase occurrence membership. Also keep canonical rendering within the existing summary limit rather than raising it or losing retained fields. New protocol activation and real-model acceptance remain separate gates.


## Withheld active v3 candidate: narrow-window context leakage

Independent offline end-to-end review of the active candidate reproduces a temporal-context bug. One immutable compact chunk contains a status-504, 5,000-ms incident at time zero and a healthy status-200, 2-ms event at 30 seconds. A query restricted to 29–31 seconds still sends both phrase observations and their measurements to the relevance classifier, and the readable answer includes the out-of-window incident.

The new source-observation facts are correct for the stored chunk, but `active_compact_context(chunk)` has no requested-scope argument and consumers use its complete projection. Existing boundary-metric gaps do not scope the new phrase facts. Canonical storage remains unchanged; the active candidate is withheld despite its passing structural suite.

Acceptance: per-cell factual context includes only supported in-window records, selected-field references, source-phrase memberships and recomputed exact facts, or explicitly separates whole-artifact inspection from queried facts. The classifier must not use an out-of-window incident as query support. Preserve immutable canonical identity, historical recipes, literal/citation bindings and multi-cell semantics. Add actual route tests for narrow half-open boundaries, empty phrase membership, shared chunks across different cells and partial field membership, then independently review before fresh native proof.


## Reviewed source-backed and scoped-context correction

The release candidate now uses actual-model-selected exact source phrases and field references, with source-bound membership and deterministic occurrence facts. Free model-written counts and uncertainties are excluded from fresh compact output. Independent review verified complete accepted-output reservations, typed-field preservation, privacy checks across the complete packed batch, immutable historical recipes, and historical unchecked prose exclusion.

The subsequent per-cell correction passes independent reproduction of the temporal failure: the narrow query returns only the healthy event, and outside-only strong identifiers cause zero classifier calls or selections. Sparse original references, excluded original witnesses, field intersections and two distinct views of one canonical chunk are checked without altering canonical bytes or embedding text. The full root suite passes 1,241 tests and 363 subtests with one opt-in test skipped.

Both release artifacts build and pass structure checks; all 75 installed runtime/resource files match the reviewed source, and 28 installed CLI contracts pass. Fresh installed Chrome, native-token/source-oracle instrumentation, actual corrected three-app prose and full 100K acceptance remain separate gates. The earlier failed cycle remains unchanged.

## First installed source-observation cycle fails actual compaction

The fresh installed-wheel Chrome cycle on the independently reviewed source-backed/scoped candidate completed with 10 passing and 9 failing checks. Console capture returned HTTP503 with processing category `compact_invalid_output`. Auth development indexed 0 of 2 expected events; production indexed all 4 and preview indexed all 3. Subsequent missing-context and populated-reader checks also failed. These checks are recorded as failures, without treating all nine as independently diagnosed runtime bugs. The owned service stopped, with host capture disabled and no demo servers or containers started.

The installed runtime/resource bytes match the reviewed candidate. The complete regression suite (1,241 tests and 363 subtests), 28 installed CLI contracts, package structure audit and UI contract controls passed; none override this actual failure. The run did not preserve parsed provider output, so `compact_invalid_output` alone cannot establish the exact rejected selector or validator reason. Independent offline diagnosis and a fresh instrumented native run are required. Release, the 100K go-ahead and GitHub merge remain blocked.

## Indexed preview context fails the hybrid admission gate

The same installed cycle independently indexed all three preview events, yet both saved preview API/browser queries returned no selected evidence. Provenance reports one vector hit, one lexical hit, one gate rejection and zero classifier calls. Offline reconstruction validates the retained chunk and scopes all three records correctly, with four source observations and thirteen retained fields. It finds no requested strong identifier or exact field-value anchor.

The rejected condition is `retriever._rank`: without an exact identifier/field match, a candidate requires vector similarity at least 0.50 even when lexical retrieval found it. The actual score was not retained; rejection establishes that it was below this threshold, without supplying its numeric value. Controlled offline scores of 0.499999 and 0.50 reproduce rejection and admission respectively; shared literal eligibility passes with zero anchors. This is separate from the failed console/development compaction, and from relevance-model rejection.

Acceptance: meaningful lexical source evidence may reach bounded candidate classification while preserving the cosine threshold, strict positive model validation, literal eligibility, scope and service fairness, and existing candidate/input/deadline caps. Boilerplate-only lexical matches, outside-window facts and absent identifiers must not manufacture admission. Verify the actual installed preview query again after a reviewed correction.

## Instrumented source-observation pilot rejects a source witness

A new normal three-producer capture-burst run submitted all 1,200 events and preserved the exact sanitized producer input configurations privately. The first 400-event native claim failed after 34.051 seconds with `compact_invalid_output`. A transparent validator observer recorded `invalid_source_phrase_witness` before the public category coalesced it. An exact offline replay now identifies duplicate phrase text: all four record references and substrings are valid, but the same 35-character phrase is selected four times against different witnesses. Each phrase belongs to all 35 records in that section; the second selection violates unique phrase text. No event, chunk, vector, key, digest or prepared job was published; all 1,200 events remain pending, 400 marked with the failed attempt. No logs were dropped, expired or rejected at admission. The owned server and three producers stopped.

Seven original completion requests were attempted: six grouping requests (including one canceled during final harness cleanup) and one compact selection. Six parsed responses were saved privately before validation; each of those six stopped normally and its exact native input token count matched provider `prompt_eval_count` (453, 560, 444, 558, 572 and 4,975). The canceled request has no returned parsed response and is not claimed as preserved or completed. Native preflight separately made 21 metadata requests, seven render-only requests and seven tokenizer requests; production generation guards made fourteen metadata requests. One existing configuration-check embedding ran; no document or query embedding ran. Native preflight cost 3.858 seconds and provider preservation 0.037 seconds inside unchanged deadlines. These are measured-phase counts; uninstrumented earlier Chrome activity prevents a global call-count claim.

The same run found an independent private-QA defect: legitimate grouping membership-repair phases were labeled unknown in the ledger. The original runtime recognized their instructions and executed them normally. This makes the token-proof acceptance fail independently, and a new helper version is correcting its phase enumeration without rewriting the failed run. Provider preservation, exact submitted-input binding and request accounting remain valid diagnostic evidence. Neither the runtime failure nor the QA defect is relaxed into acceptance.

The replay used all six saved original responses against the exact first 400 spool events. Instruction, payload and schema hashes matched every recorded native request; provider hashes also matched. Grouping produced thirteen chunks; the first compact section contains thirty-five records. All four field references are valid and distinct. The failure is exact phrase redundancy, rather than fabricated text, a wrong record reference, altered source bytes, native truncation or an exhausted completion. A correction must validate every incoming witness before considering explicitly defined duplicate normalization, preserve exact memberships/counts and full-body privacy, and be independently reviewed before a new actual cycle. The failed run remains failed.

A second offline counterfactual proves that duplicate normalization alone is insufficient. The selected 35-character phrase equals every one of the thirty-five visible projected messages and authenticated producer prefixes, although canonical messages are longer because of their structured attribution suffix. Keeping only one duplicate still raises `source_phrase_body_echo`; each selector individually violates the protected whole-body rule. Thus the model copied a complete visible source message and repeated it against four witnesses. The correction must constrain actual semantic selection to authenticated safe source fragments, preserve full-body and union-reconstruction rejection, and preserve useful rare-incident context. It cannot discard the privacy check or claim deduplication fixes this run.

## Checked lexical admission correction integrated

The delegated lexical correction preserves the 0.50 vector route and adds a separate candidate route requiring a real lexical hit and all remaining query topic terms together in one checked in-window source quotation. At least two meaningful English words must support the route. Closed query scaffolding and context-label/identifier-only overlap cannot supply topic support; extra terms remain conservatively required. Negation stays in the original question for strict independent relevance classification. Historical unchecked prose cannot qualify. Scope, literal eligibility, source fairness, admission/selection caps, model budgets and deadline remain unchanged.

Independent review of the runtime reproduces the saved preview case with controlled sub-threshold similarity: a strict mock positive selects one memory; negative/low-confidence responses select none; removing the lexical hit admits none. It passes 164 tests and 182 subtests without changing canonical or embedding bytes. The subsequent sixteen controls add bounded lexical pools, fairness, literal OR/AND, absent IDs, input/call/selection caps and atomic late failure/cancellation. Root integration passes all thirty-nine new controls. The delegated full suite passes 1,280 tests and 363 subtests; its two-iteration GNHF run ended at its iteration cap, not a user acceptance decision. Fresh actual installed and native proofs remain required.

## Safe catalog activation remains incomplete

The delegated foundation validates exact source-derived phrase candidates against every protected body and their complete union before model selection. Reviewed optional request/packing bounds and a generic connective-gap policy pass offline controls. Reconstruction of the captured three-application fixtures confirms the seeded operation/outcome candidates are offered, without actual model calls. These facts do not establish production activation or model-selected useful context.

The first activation trial failed and restored the last tested checkpoint. Its 123 regression failures include shared mock-construction failures after the selector protocol changed; they are not 123 independently demonstrated reader defects. Independent diagnosis identifies two concrete implementation gaps: useful no-connective topic fragments can be omitted despite nonempty pattern coverage, and the additional catalog transport can make previously feasible field-heavy singletons exceed unchanged input limits. The next bounded delegated correction must activate production alongside generic privacy-gap planning, measured lossless transport and meaningful protocol-aware tests. Existing privacy, complete fields, native context/output budgets and deadlines remain unchanged. No catalog runtime correction or new native acceptance is claimed yet.

## Scoped test accepts a report from the wrong corpus

Independent private-harness review reproduces a test provenance bug: a scoped-query prerequisite accepts any source-oracle report marked PASS with eighteen checked responses, even if it identifies a different simulation corpus. Freezing that chosen report's bytes preserves the mismatch. This does not demonstrate a runtime source leak, but it invalidates the intended test prerequisite.

A new oracle helper candidate binds a completed source check to its original public identity, all eighteen response/input hashes, source/producer/harness and model/profile/native-helper hashes, and complete typed logical database contents. The scoped prerequisite compares these bindings with the supplied original corpus and final native source guard. Logical identity includes canonical rows, vectors, FTS, index metadata, event keys/digests and approved shadow tables; physical SQLite backup file hashes are not used for cross-corpus equality. Wrong-run, changed input/source/helper/model and changed database values must reject. Older helpers/reports remain unchanged. Candidate offline controls pass; independent review and actual fresh execution remain required.

Independent follow-up review passes the new private prerequisite correction. A new native helper records the exact original execution guard before instrumentation/startup and binds its full source/helper/model/profile files into the final proof. The source oracle revalidates that complete original guard and rejects a later replacement model guard. Both checker files and their already-loaded bytes are pinned at freeze; the report copies the frozen identity and checks it again after the source audit. Five independent corruption variants reject: changed model bytes, incomplete original model map, absent execution identity, fresh hashing of changed checker code after import, and changed oracle code after startup. Offline suites pass nineteen oracle methods, eighty existing native controls plus five guard controls, nine new scoped binding controls and fifty-one scoped controls plus fifteen source-oracle methods. This repairs the test provenance flaws; no new actual native, scoped, installed-browser or 100K acceptance is implied.
