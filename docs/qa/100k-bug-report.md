# 100K pipeline bug report

Current release status: **not ready to publish as a validated 100K demo**. Installed Chrome cycle 3 passed all 20 checks, and fresh three-application pilot cycle 2 captured/indexed all 1,200 events with exact reconciliation and restart/replay passing. Its multi-service queries failed. The reviewed correction now passes a real native overflow-rejection control and full-input token reconciliation, but the latest exact-incident query selects only two of three relevant services. Retrieval completeness, corrected fresh prose, a passing fresh pilot and fully indexed 100K rehearsal remain required before GitHub review and merge.

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
| Actual multi-service model selection fails validation | Duplicate references and contradictory decisions confirmed; label-only correction passes structural review, while actual acceptance remains blocked by input truncation |
| Summary metric attribution and unsupported source adjacency | Confirmed prose-fidelity bugs; per-record input correction reviewed, actual corrected prose proof still outstanding |
| Relevance prompts silently truncated by the native provider | Confirmed numeric server warnings correlate with failed and apparently successful probes; lossless input correction and full-fit proof required |
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
