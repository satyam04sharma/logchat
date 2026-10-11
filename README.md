# Logchat

Compact, searchable log memory for coding agents. Connect application logs to one local RAG pipeline; your agent retrieves context and supporting evidence.

## Get started

Python 3.12+ and SQLite loadable-extension support are required. The preserved native pipeline currently supports the reviewed **macOS Ollama `0.34.4` bundle**, `mistral:7b` and `nomic-embed-text:latest`, with exact approved model and library bytes. It also requires `/usr/bin/cc` and support for executing an Intel worker, including Rosetta on Apple silicon. See the [native compatibility contract](docs/native-token-counter-compatibility.md) before installation; other platforms, builds and model revisions are not validated by this path.

Native generation uses completion without stored message history. Complete requests receive exact token checks; oversized v3 embedding chunks are subdivided by source records before enqueue. Logchat connects to your installed models; it does not bundle or download them.

```sh
git clone https://github.com/satyam04sharma/logchat.git
cd logchat
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

## Copy this to your agent

```text
Set up Logchat for this project. Read SKILL.md first.
Check the native compatibility contract, local Ollama endpoint and available
models. Use my explicitly selected supported profile; don't download weights.
Configure Logchat, connect my app's command or log file, and give me the
project-bound MCP configuration. Verify capture and retrieve supporting context.
```

[Agent skill](SKILL.md) · [Source](src/logchat) · [Adapters](docs/connectors.md) · [Contributing](CONTRIBUTING.md)
