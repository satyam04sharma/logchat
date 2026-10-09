# Contributing

Fork the repository, make a focused branch, and open a pull request targeting `main`. Public visibility lets anyone propose changes; it does not grant write or merge access. Never push directly to `main`.

Maintainers review the diff and test evidence. Changes require all four Linux/macOS × Python 3.12/3.13 checks against the latest base, resolved review conversations, and the configured approval policy. New commits invalidate prior approval. The project owner is the code owner for every file, including workflows and ownership rules. Forked workflow runs from external contributors require maintainer approval before execution; that is separate from approval to merge.

Only maintainers merge, using squash. Automatic merging is disabled; branch deletion and force-pushes to `main` are blocked. AI-generated changes follow the same process. Contributor attribution comes from commit metadata, not repository permissions. Use your own verified Git author identity.

Install Python 3.12+ and run `python -m pip install -e '.[dev]'`. Run `python -m pytest -q`, `python -m build`, and the `scripts/check_release_artifacts.py` and `scripts/check_release_wheel.py` before submitting a change. Tests use synthetic fixtures; never add customer logs, credentials or local state.

Logchat’s stable boundary is the source-independent semantic context pipeline. Adapters normalize events, preserve timestamps and measurements, and feed native intake. They must not introduce separate summary stores or bypass compaction. Add tests for scope isolation, retry/deduplication, bounded reads, failure cursors and credentials. Use the configured shared generation model for grouping, summaries and relevance; embeddings remain a separate capability. Do not silently substitute models.

Document behavior and limitations together. Model-written summary prose is not verified truth, absence of records is not complete coverage, and query output is context rather than a proposed solution. New integrations should have an offline fixture suite plus an explicitly labelled live acceptance check.

For a PR, describe the concrete behavior change, relevant tests, and remaining limitations. Keep unrelated changes separate. See [.github/SECURITY.md](.github/SECURITY.md) for sensitive reports.

Code lives in `src/`: `logchat/rag` is the shared memory pipeline; `logchat/local` runs the local service, adapters and agent interface. `connectors` contains normalized source transports; `pipeline` supplies shared model/event helpers. `api` and older CLI modules retain compatibility contracts exercised by tests. Historical deployment assets live on the `archive/legacy-stack` branch, not the main release.
