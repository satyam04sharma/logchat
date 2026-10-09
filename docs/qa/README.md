# Reproducible local release QA

The sandbox is one isolated, loopback Logchat service with five synthetic demo projects. It shares the existing local Ollama server but uses separate state, credentials, ports and test data. No customer logs or real cloud credentials are required. See [current release evidence and screenshots](release-2026-10-07.md) and [case inventory](requirements.md). The inventory includes variants that are not covered by the measured walkthroughs; a list of requirements is not proof of execution.

## Install the artifact, not the source tree

Use Python 3.12+; Google Chrome, Ollama and Docker must be installed separately. The Docker gate requires a cached `python:3.12.13-slim` image and never pulls it. Ollama needs `mistral:7b` and `nomic-embed-text`; the harness does not download models or substitute them.

```sh
python3.12 -m venv /tmp/logchat-release-qa-venv
/tmp/logchat-release-qa-venv/bin/python -m pip install --upgrade pip
/tmp/logchat-release-qa-venv/bin/python -m pip install -e '.[dev,qa]'
/tmp/logchat-release-qa-venv/bin/python -m build
/tmp/logchat-release-qa-venv/bin/python -m pip install --force-reinstall --no-deps dist/logchat-0.3.0rc11-py3-none-any.whl
```

Use a fresh, disposable fixture directory outside the checkout. Its owner marker prevents accidental reuse of another directory. All commands below use the installed CLI beside the selected Python. Run modes sequentially: they share port **18940** and the local model. Do not run beside another service on that port.

```sh
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --prepare --checks
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --walkthrough
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --payments-walkthrough
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --custom-walkthrough
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --docker-walkthrough
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --recovery-walkthrough
```

`--checks` uses installed command help and mocked cloud subprocesses. `--walkthrough` uses actual headless Chrome: it clicks the HTML demo, explicitly transports captured browser console events through the adapter, and exercises the actual reader. The other modes verify wrapped stdout/stderr and exit 7, multiple custom CLI sources and reconnect, restricted Docker plus file capture in one project, and partial-line/disconnect/restart recovery with an independent producer.

The five demo projects are authentication, payments, browser console, inventory and custom CLI. Authentication files cover dev/preview/prod; environment controls stay hidden until explicitly enabled in the UI test. Preparation creates fixtures, not searchable memories. Each mode records fresh intake/indexing/retrieval evidence under `fixture/evidence/` and stops only its owned service/producer/container in `finally`. Reports must show zero failed checks; exit codes must be checked individually.

For manual reader review:

```sh
/tmp/logchat-release-qa-venv/bin/python scripts/qa_native.py --run-dir /tmp/logchat-release-fixture --serve
```

Open http://127.0.0.1:18940. Ctrl-C stops the owned service while preserving test memories. Other projects may be empty until their walkthrough runs. A URL alone does not capture a browser console; opening a page is not equivalent to clicking its event button and transporting those events.

## Automated and packaging checks

```sh
python -m pytest -q
LOGCHAT_RUN_ACTUAL_EMBEDDINGS=1 python -m pytest -q tests/test_rag_retriever.py::ActualModelTests
node tests/native_ui_contract.cjs
python -m build
python scripts/check_release_wheel.py dist/logchat-0.3.0rc11-py3-none-any.whl
python scripts/check_release_artifacts.py
python scripts/prepare_public_source.py --output /tmp/new-logchat-public-source
```

Use the activated QA environment for `python`. The DOM-double renderer check is not actual browser acceptance. Real-model tests are not year-scale retention tests. Live Railway/Vercel permissions and timestamps, hosted-model providers, and clean-machine/platform trials remain separate gates.

Screenshots linked from the README contain only synthetic evidence. Older local evidence is preserved in the development checkout; it is excluded from the public export. [The release report](release-2026-10-07.md) is the authoritative result for this run.
