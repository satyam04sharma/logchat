# Logchat

Logchat turns application logs into compact, searchable context for coding agents. It captures explicitly connected sources, groups related events, writes summaries, stores embeddings locally, and retrieves supporting memories using **semantic + lexical search**. It returns context and evidence; your agent decides what to answer or do.

The first public candidate is **0.3.0rc12**. The native runtime uses Python, Ollama and SQLite with sqlite-vec. Docker and a hosted database are not required to run Logchat.

The `logchat` command runs this native pipeline only. The older Postgres/Docker implementation remains as historical source for migration and regression checks; it is not embedded in the installed wheel. See [runtime boundaries and hosting](docs/hosting.md).

## Get started

Requirements: Python **3.12+**, [Ollama](https://ollama.com/download) running locally, and enough RAM/disk for your selected models. Model downloads are separate from the Python package. The examples use Mistral 7B for grouping, summaries and relevance, and nomic-embed-text for embeddings.

On macOS, use a Python build with SQLite extension loading, such as Homebrew Python:

```sh
brew install python@3.12
```

Some macOS Python distributions omit this capability, which sqlite-vec requires. See [Python's SQLite extension documentation](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.enable_load_extension).

Start the Ollama app/service first (`ollama serve` in another terminal if needed). From a clone of this repository:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
ollama pull mistral:7b
ollama pull nomic-embed-text
logchat install --start
```

Setup asks for the local model endpoint and one shared generation model, then a log source: **file, app command, Docker, Railway, Vercel, a log CLI, or later**. Open **http://127.0.0.1:8765** for the optional reader. The native release targets macOS/Linux; see [platform and release checks](docs/release-readiness.md).

For repeatable setup without prompts:

```sh
logchat install --yes --start --model mistral:7b
```

Setup checks capabilities internally. It does not download models, switch to a fallback model, or send logs to a hosted model provider. Hosted model/API-key setup is not included in this release.

## Try a separate demo app

After setup, install the optional synthetic web app. This is a separate package, not a Logchat runtime dependency:

```sh
python -m pip install ./examples/quickstart
mkdir -p /tmp/logchat-demo-project
logchat local connect --project /tmp/logchat-demo-project --port 8000 -- logchat-demo-web --port 8000
```

Keep that terminal running. Open **http://127.0.0.1:8000**, then click **Emit session-renewal error**. The app emits a JSON event on stdout; the wrapper sends it through Logchat’s shared summary/embedding pipeline. In another terminal, activate the same environment and ask:

```sh
logchat local status --project /tmp/logchat-demo-project
logchat local ask "What context exists for demo-user@example.test and DEMO_SESSION_401?" --project /tmp/logchat-demo-project --json
```

Wait until status reports searchable memory. Model processing can take tens of seconds on local hardware. Open **http://127.0.0.1:8765**, choose the demo project, and ask the same question to inspect its supporting memory. Stop the wrapper with Ctrl-C; `logchat local stop` stops Logchat separately. This demo is loopback-only and uses synthetic data. It produces server logs; arbitrary browser console output still needs the explicit browser adapter.

## Connect your app

Wrap its startup command to capture stdout/stderr:

```sh
logchat local connect --project . -- npm run dev
```

For an already-running app that writes a file:

```sh
logchat local connect --project . --port 8000 --log-file ./app.log
```

The app keeps running independently. Logchat uses its own port; the app port is metadata, **not automatic log capture**. File capture starts with new lines; add `--from-start` to ingest existing lines. Browser `console.log` needs an explicit browser-console adapter; an HTTP server’s output does not include browser console messages.

Docker example:

```sh
logchat local connect-provider docker --project . --container my-app
```

Railway and Vercel examples (experimental: offline contracts pass; live-provider acceptance is pending; install their official CLIs first):

```sh
logchat local connect-provider railway --project . \
  --provider-project YOUR_RAILWAY_PROJECT_ID \
  --provider-environment production --service api --prompt-token

logchat local connect-provider vercel --project . --new-source \
  --provider-project YOUR_VERCEL_PROJECT_ID \
  --provider-environment production --prompt-token
```

`--prompt-token` reads hidden input and stores a protected, source-scoped credential. Omit it to use an existing provider CLI login. Tokens authenticate the **log source**, not the language model. Sources all feed the same local RAG pipeline. See [adapter setup, credentials, limits and custom CLIs](docs/connectors.md).

## Retrieve context

```sh
logchat local status --project .
logchat local ask "What context do we have about failed session renewal?" --project . --json
logchat local integration --project .
```

The integration command prints project-bound MCP configuration for your agent. Responses contain relevant summaries, selected original fields, timestamps, counts, supporting memory IDs, model provenance and gaps. **Configured, captured, queued and searchable are different states**; check the status before assuming data has been indexed.

```sh
logchat settings show
logchat local disconnect --project .
logchat local stop
```

Disconnect stops the currently bound capture source and retains memories. Other connected sources keep running. Stopping Logchat stops its managed capture workers; provider applications keep running. Independent instances need separate ports **and** state directories. See [settings](docs/settings.md) and [adapters](docs/connectors.md).

## Storage and privacy

New native memory stores compact model summaries, selected keys/values, exact metrics, references and vectors. Recognized correlation keys (IDs, emails, phones and error codes, including nested fields) are retained independently of the model’s optional field choices, within bounded budgets. Unrecognized or unstructured details can still be omitted. It does not retain original message bodies by default. Compression is lossy: omitted details cannot be reconstructed, and model prose is not verified truth. Selected values may include personal data or secrets supplied in your logs; inspect what you capture. Generation and embeddings use the configured loopback Ollama server.

An explicit bounded temporary-original mode can preserve intake while the model is unavailable: `logchat install --capture-only --start`. Pending originals are not searchable context; configure the model later to summarize and embed them. See [settings](docs/settings.md) for disk and expiry controls.

The native service is for **one OS user**, binds to loopback and authenticates control/ingestion. It is not a public multi-user server. Provider retention, late arrivals, unavailable models and capped queries can leave gaps. Long-term consolidation, year-scale capacity/quality and external legacy-history migration are unfinished. The older Docker/pgvector application is a separate implementation; it is not the native quick start.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
python -m build
python scripts/check_release_wheel.py dist/logchat-0.3.0rc12-py3-none-any.whl
python scripts/check_release_artifacts.py
```

Provider tests use synthetic output and do not fetch customer logs. Installed-package, real-model and live-provider checks are separate release gates: [release readiness](docs/release-readiness.md). See [contributing](CONTRIBUTING.md), [security](SECURITY.md), [architecture](docs/architecture.md), and the [animated walkthrough](docs/rag-rebuild/rag-explained.html).

For isolated synthetic release QA, install `.[dev,qa]` and follow the [native QA harness](docs/qa/README.md) and [requirements matrix](docs/qa/requirements.md). Contract checks, real-model checks and actual browser checks are separate evidence gates.

## See the local reader

These screenshots use only synthetic sandbox events. They show retrieved context, model provenance, explicit coverage gaps, and the supporting-memory selector—not generated solutions. See the [sandbox walkthrough and release evidence](docs/qa/release-2026-10-07.md) for the exact setup and test boundaries.

![Desktop Logchat reader with synthetic session-renewal context](docs/screenshots/release-reader-desktop.png)

<details>
<summary>Mobile supporting-memory inspection</summary>

![Mobile supporting-memory selector and retained evidence fields](docs/screenshots/release-memory-mobile.png)

</details>

MIT licensed. This repository is the install source until a package publication is explicitly announced.
