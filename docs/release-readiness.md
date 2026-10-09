# 0.3.0rc13 release readiness

This is a native local release candidate, not a claim that every original design goal has shipped. Publish the scoped candidate only after the remaining gates below are checked.

## Scope

One OS user runs a loopback service with one shared Ollama generation profile, a separate embedding model, SQLite/sqlite-vec hybrid retrieval and context-only output. Fresh setup requires explicitly selected generation and embedding models and dimensions; no named model is bundled, downloaded or selected by default. The installer configures the model before source onboarding. Sources are explicit files, wrapped app commands, Docker containers, Railway/Vercel log CLIs or a configured timestamped JSON log command. HTTP ports identify apps; they cannot expose arbitrary application or browser logs.

The native source adapters are supported on macOS/Linux. Windows provider subprocess capture is rejected explicitly and the complete Windows installation is not verified. Python 3.12 is locally verified. Public CI passed on Linux/macOS with Python 3.12 and 3.13 ([verified run](https://github.com/satyam04sharma/logchat/actions/runs/37868170052)).

## Evidence and gates

| Check | Current evidence | Remaining gate |
| --- | --- | --- |
| Unit/regression suite | Synthetic local, adapter, model-contract, isolation and retry tests | Public Linux/macOS × Python 3.12/3.13 CI passed; rerun after changes |
| Provider credentials | Hidden prompting, scoped credential files, token cleanup and no literal-token source config tested | Validate real provider permissions against your own selected test project |
| Installed package | Wheel installed into a separate environment; service launched outside the repository | Repeat on a clean machine |
| Real local model | Ollama Mistral 7B + nomic-embed-text; synthetic Docker stdout and stderr ingested, summarized, embedded and retrieved | Broader workload/summary-quality evaluation |
| Restart and replay | Adapter leases, persistent cursors, failed-window retries and deduplication covered by regression tests | See measured installed results below |
| Packaging | Wheel/sdist structural checks and bundled-runtime byte comparison | Contents review before publication; structural checks are not a secret scan |
| Railway/Vercel | Official CLI integration and synthetic capped/error/rate-limit cases tested | Live CLI authentication, project scope and actual returned timestamps not verified in this run |
| Public distribution | README, MIT license, contributor/security docs and source-export tool prepared | Public repository and CI are live; choose a release/tag and publish only reviewed artifacts |

## Current measured acceptance

See [the 2026-10-07 release report](qa/release-2026-10-07.md) for fresh installed-package, browser, CLI, adapter, recovery, dependency and packaging results. Historical counts are not the current release gate. The [QA guide](qa/README.md) gives portable reproduction commands and explains screenshot scope.

## Release workflow

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
LOGCHAT_RUN_ACTUAL_EMBEDDINGS=1 python -m pytest -q tests/test_rag_retriever.py::ActualModelTests
python -m build
python scripts/check_release_wheel.py dist/logchat-0.3.0rc13-py3-none-any.whl
python scripts/check_release_artifacts.py
python scripts/prepare_public_source.py --output /ABSOLUTE/PATH/OUTSIDE/CHECKOUT/logchat-0.3.0rc13-source
```

The public repository was created from a reviewed allowlisted export, not by initializing Git in the private development folder. Continue using the exporter for public source archives. The private development folder contains working notes, media and local state; do not publish it directly. The export preserves application code, tests, historical source for regression and migration and current public docs while excluding Git history, local state, historical reports, historical screenshots, model data and generated hosted-site files. Two explicitly allowlisted synthetic reader screenshots are included. Review file contents: an allowlist does not prove absence of private text. Existing history is preserved in the development checkout.

From the exported source, build/install again and follow the README. Provider CLIs and Ollama models are installed separately. Setup must not silently substitute a generation model. An unavailable summarizer retries the same window; explicit temporary-original retention is separately configurable and bounded. Pending originals are not searchable RAG memory.

## Limitations to disclose

- Summary prose is model-produced and lossy. Exact retained fields and measurements have structural checks; this does not prove the summary is semantically correct or retain every original detail.
- Summary-only intake discards originals after durable compact preparation; omitted facts cannot be reconstructed. Existing historical full-text records are not automatically erased.
- Provider cursors describe requested windows, not guaranteed completeness. Retention, sampling, permissions, late arrivals and query limits can create gaps. Failed/capped windows are not advanced. Extremely dense windows may stall instead of dropping data.
- Hosted generation-provider/API-key setup, model downloads, Sentry integration, legacy history migration, cross-batch consolidation and a demonstrated year of low-footprint memory remain outside this candidate.
- The native service is not a shared/public multi-user deployment. Keep it loopback; provider tokens are privileged source credentials.

The older Docker/pgvector deployment stack is preserved on `archive/legacy-stack` and is not embedded in the native wheel. It is not evidence that the native runtime uses Postgres, and it is not the first-release quick start.

## First public CI correction

The first GitHub run exposed the hosted macOS Python distribution’s missing SQLite extension capability and a new Next.js security advisory. macOS CI now uses Homebrew Python with an explicit capability preflight; the retained historical web frontend updates Next.js to 16.4.0. The runtime remains SQLite vector RAG.

## Model evidence

The native installed runs used explicitly selected Mistral 7B and nomic-embed-text. This is a tested configuration, not an installer default. Native retrieval and summary quality with `qwen2.5:1.5b` have not been established by these runs; no smallest-model, parameter-total or download-size claim is made. Independent clean-machine installation and live authenticated Railway/Vercel acceptance remain open.
