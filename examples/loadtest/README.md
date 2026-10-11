# Native 100K rehearsal

Run from an installed source checkout with an explicitly selected existing local model profile:

```sh
python scripts/qa_100k.py --run-dir /absolute/path/outside/checkout --profile /absolute/path/to/existing/rag.json --count 100000 --minutes 90
```

The run directory must be fresh. This starts a private loopback service on port 18960 and three independent subprocess applications: a web-log simulator, worker-log simulator, and SSH-log simulator. Producers send actual HTTP pushes through native model grouping, compact summaries, durable scheduling, and embeddings. No direct database ingestion, model mocks, hosted models, or public deployment occurs.

[Loghub](https://github.com/logpai/loghub) Apache, Linux, and OpenSSH 2K structured samples and template catalogs are downloaded in memory at a pinned Git commit. Sample event frequencies weight the actual sanitized template replicas. Every generated event has a distinct synthetic ID. These are independent log simulator processes, not deployed implementations of Apache, Linux, or OpenSSH. This is 100,000 **synthetic replicas**, not 100,000 unique original dataset records. Input placeholders and identifying paths/usernames are replaced with synthetic values. Rare shared incident IDs connect three deliberately injected failure types.

A 48-event smoke measures elapsed processing time and extrapolates the 100K duration. The specified wall-time budget includes bounded backpressure retries. A run returns nonzero if the target or retrieval checks remain incomplete; accepted HTTP responses alone do not prove successful indexing. Read `report.json`, `smoke.json`, producer progress, and independent durable index counts. Evidence excludes credentials and original dataset messages. Model compact summaries and selected synthetic fields are retained as native RAG evidence.

Owned producers and the service are stopped at completion. Existing local services and Ollama remain running. Model output can vary; failed stages are surfaced by category and do not silently substitute a model.

To admit a concurrent 100K burst and then validate the fully indexed corpus, run:

```sh
python scripts/qa_100k.py --run-dir /absolute/path/fresh-burst-state --profile /absolute/path/to/existing/rag.json --count 100000 --minutes 15 --batch 400 --capture-burst --drain-seconds 7200
```

This enables bounded temporary synthetic originals (`retain_until_summarized`, 512 MiB, 24-hour expiry) while the actual selected native models run. It reports generated/admitted/indexed counts separately. `admission_complete` is not `rag_complete`; a backlog is not successful retrieval of 100K logs. The script still returns nonzero unless full native indexing and retrieval validation complete. Originals remain private in the owned disposable run state until summarized, expired, or the run directory is removed.

Full acceptance requires every synthetic ID exactly once in compact metadata and the source ledger, matching per-source counts, duration sums and status counts, all model jobs complete, no pending or failed originals, three-service incident context, negative and scope checks, authenticated-source isolation, and restart/replay durability. `report.json.acceptance_passed` must be true; exit code zero requires all gates. Use `--port` to select a free private loopback port. The drain budget is a test limit, not a claimed completion time. Failed cycles remain failed even when admission succeeds.
