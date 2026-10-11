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
