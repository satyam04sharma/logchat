# Logchat

Compact, searchable log memory for coding agents. Connect application logs to one local RAG pipeline; your agent retrieves context and supporting evidence.

## Get started

Python 3.12+ and a running local Ollama server are required. Logchat connects to your models; it does not bundle or download them.

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
Inspect my local Ollama endpoint and available models. Help me choose a
compatible generation model and embedding model; don't assume or download one.
Configure Logchat, connect my app's command or log file, and give me the
project-bound MCP configuration. Verify capture and retrieve supporting context.
```

[Agent skill](SKILL.md) · [Source](src/logchat) · [Adapters](docs/connectors.md) · [Contributing](CONTRIBUTING.md)
